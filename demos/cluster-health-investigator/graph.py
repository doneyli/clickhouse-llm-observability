"""
Cluster Health Investigator — an orchestrator-workers LangGraph agent.

Pattern #4 (Orchestrator-Workers, dynamic task decomposition). The defining
feature — and the thing the repo lacked — is RUNTIME decomposition: a planner
LLM reads the symptom and decides at runtime WHICH system-table analyses to run
and HOW MANY, emits a structured plan, fans out one worker per task via
LangGraph's `Send` API, reviews coverage, optionally re-plans a second wave,
then synthesises an evidence-cited diagnosis.

Flow:

    START
      |
    orchestrator ── plan (agent span OUTPUT) ──► assign_workers (Send × N, runtime)
      ▲                                              │
      │ (round 2, only if replan)                    ▼
    replan_gate ◄──────────────────────────────── worker × N  (parallel)
      │                                              │ findings ⊕ operator.add
      │ sufficient                                   │
      ▼                                              │
    synthesize ──► END                               │

The trace SHAPE varies per input: a narrow symptom → `worker (2/2)`, a broad
instability report → `worker (6/6)`, `--fault overplan` → `worker (8/8)`. The
LLM decides WHAT; deterministic code (MAX_PLAN_ROUNDS, MAX_WORKERS_TOTAL)
decides WHEN to stop.

Heavy deps (langgraph, langchain_anthropic) are imported lazily so the module,
the pydantic schemas, and the pure guard helpers import cleanly for unit tests
without those packages installed.
"""

from __future__ import annotations

import operator
import os
from typing import Annotated, Callable, List, Optional, TypedDict

from pydantic import BaseModel, Field

import langfuse_config as lf
from analysis_catalog import CATALOG, CATALOG_DIGEST, CATALOG_KEYS

# --------------------------------------------------------------------------- #
# Config (env-driven; defaulted in docker-compose)
# --------------------------------------------------------------------------- #
PLANNER_PROMPT = "cluster-health-planner"
WORKER_PROMPT = "cluster-health-worker"
SYNTH_PROMPT = "cluster-health-synthesizer"

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")     # planner + synthesizer
WORKER_MODEL = os.getenv("WORKER_MODEL", "claude-haiku-4-5")          # model tiering
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
MAX_WORKERS_TOTAL = int(os.getenv("MAX_WORKERS_TOTAL", "8"))
MAX_PLAN_ROUNDS = int(os.getenv("MAX_PLAN_ROUNDS", "2"))
PLANNER_LABEL = os.getenv("PLANNER_LABEL", "production")


class PlanError(Exception):
    """Raised when the planner cannot produce a valid plan (retry-once-then-abort)."""


# --------------------------------------------------------------------------- #
# Plan schema (the pattern-defining first-class object)
# --------------------------------------------------------------------------- #
class Task(BaseModel):
    analysis_type: str = Field(description="A key of the analysis catalog")
    focus: str = Field(default="", description="What to look for, natural language")
    rationale: str = Field(default="", description="Why this analysis given the symptom")


class Plan(BaseModel):
    tasks: List[Task] = Field(min_length=1, max_length=8)
    reasoning: str = Field(default="")


class GateVerdict(BaseModel):
    sufficient: bool
    missing_analyses: List[str] = Field(default_factory=list)
    reasoning: str = Field(default="")


class State(TypedDict, total=False):
    symptom: str
    tasks: list            # current wave's tasks (replaced each orchestrator run)
    planned: Annotated[list, operator.add]   # every task across rounds (audit trail)
    findings: Annotated[list, operator.add]  # result store — reducer merges parallel writes
    round: int
    workers_spawned: int
    fault: Optional[str]
    diagnosis: str
    trace: Annotated[list, operator.add]     # human-readable step log (reducer for parallel)
    _replan: bool


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without LLM / DB / langgraph)
# --------------------------------------------------------------------------- #
def validate_plan(plan: Plan) -> Plan:
    """Drop tasks whose analysis_type is not in the catalog; raise if none valid."""
    valid = [t for t in plan.tasks if t.analysis_type in CATALOG_KEYS]
    if not valid:
        raise PlanError("plan has no valid catalog analysis_types")
    return Plan(tasks=valid, reasoning=plan.reasoning)


def enforce_guards(plan: Plan, prior_types, workers_spawned: int,
                   max_workers: int = MAX_WORKERS_TOTAL) -> Plan:
    """Dedupe against prior rounds + within the wave; cap to the worker budget.

    "The LLM decides WHAT, deterministic code decides WHEN to stop." This is the
    anti-runaway rail: no analysis runs twice, and total workers never exceed
    MAX_WORKERS_TOTAL across all rounds.
    """
    seen = set(prior_types or [])
    kept: List[Task] = []
    for t in plan.tasks:
        if t.analysis_type in seen:
            continue
        seen.add(t.analysis_type)
        kept.append(t)
    remaining = max(0, max_workers - workers_spawned)
    kept = kept[:remaining]
    if not kept and remaining > 0:
        # Everything new was deduped away but budget remains — keep one task so
        # the wave still produces a finding (defensive; normal delta re-plans
        # never hit this because the planner is told to propose only new work).
        kept = plan.tasks[:1]
    return Plan(tasks=kept, reasoning=plan.reasoning)


def should_replan(sufficient: bool, current_round: int, workers_spawned: int,
                  max_rounds: int = MAX_PLAN_ROUNDS,
                  max_workers: int = MAX_WORKERS_TOTAL) -> bool:
    """Deterministic stop: re-plan only if not sufficient AND under both guards."""
    return (not sufficient) and (current_round < max_rounds) and (workers_spawned < max_workers)


def all_tasks_have_findings(state: State) -> bool:
    """Structural check: every dispatched worker produced a non-empty finding."""
    findings = state.get("findings", [])
    spawned = state.get("workers_spawned", 0)
    return spawned > 0 and len(findings) == spawned and all(f.get("summary") for f in findings)


def plan_with_retry(planner_llm, text: str, attempts: int = 2) -> Plan:
    """Call the planner up to `attempts` times; validate; abort (raise) if all fail.

    Module-level so it is unit-testable without constructing the LangGraph graph.
    "retry-once-then-abort": attempts=2 → one initial call + one retry.
    """
    last: Optional[Exception] = None
    for _ in range(attempts):
        try:
            raw = planner_llm.with_structured_output(Plan).invoke(text)
            return validate_plan(raw)
        except Exception as e:  # includes pydantic ValidationError + PlanError
            last = e
    raise PlanError(f"planner failed after {attempts} attempts: {last}")


def _compile_local(template: str, **variables) -> str:
    out = template
    for k, v in variables.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


# --------------------------------------------------------------------------- #
# Local fallback prompts (mirror the managed prompts seeded by seed_prompts.py).
# Every generation prefers the Langfuse-managed prompt (by label) and links it;
# these run only when Langfuse is unavailable, so the investigator always works.
# --------------------------------------------------------------------------- #
PLANNER_FALLBACK = """You are the planning brain of a ClickHouse cluster health investigator.
Given a SYMPTOM, decide which system-table analyses to run to diagnose it.

You may ONLY choose from this analysis catalog (never invent analyses or SQL):
{{catalog}}

Scaling rules (respect these — fan-out is cost):
- A single, specific complaint (e.g. one slow query) needs 1-2 analyses.
- A multi-symptom or broad instability report warrants up to 6 analyses.
- Never exceed {{max_workers}} analyses total.
- Do NOT choose two tasks with the same analysis_type.
- Do NOT repeat any analysis already covered: {{prior_findings}}.

For each analysis provide: analysis_type (exactly one catalog key), focus (a
short natural-language instruction), and rationale (why, given the symptom).

SYMPTOM: {{symptom}}

Return a plan whose task count is proportionate to the symptom's breadth."""

# v1 = the fault-overplan variant: scaling rules removed → maximum fan-out.
PLANNER_FALLBACK_OVERPLAN = """You are the planning brain of a ClickHouse cluster health investigator.
Given a SYMPTOM, run analyses to diagnose it.

Analysis catalog:
{{catalog}}

Investigate thoroughly: run every analysis that could conceivably be relevant.
SYMPTOM: {{symptom}}"""

WORKER_FALLBACK = """You are a ClickHouse diagnostics worker. You ran the '{{analysis}}' analysis.
Focus: {{focus}}

Rows returned from ClickHouse system tables:
{{rows}}

Write a compact finding (<= 150 tokens):
- State an explicit healthy / unhealthy verdict for this dimension.
- Cite specific numbers and at least one named table where relevant.
- If the rows show an error or are empty, say so plainly.
Do not speculate beyond the data."""

SYNTH_FALLBACK = """You are the lead ClickHouse SRE synthesising a diagnosis.

SYMPTOM: {{symptom}}

Worker findings (each from one system-table analysis):
{{findings}}

Write a diagnosis:
- Lead with the most likely root cause.
- Support each claim with evidence, citing the worker it came from as
  [worker:<analysis_type>] (e.g. [worker:parts_pressure]).
- Reflect every finding exactly once; never repeat evidence under two claims.
- End with concrete next steps."""

GATE_FALLBACK = """You review whether a set of ClickHouse analyses sufficiently covers a symptom.

SYMPTOM: {{symptom}}
Analyses already run: {{covered}}
Available analyses not yet run: {{remaining}}

Decide:
- sufficient: true if what has run is enough to diagnose the symptom.
- missing_analyses: catalog keys (only from the not-yet-run list) that should
  still run to close a real gap; empty if sufficient.
- reasoning: one sentence.
Prefer sufficient=true unless a clearly relevant analysis is missing."""


# --------------------------------------------------------------------------- #
# LLM construction (lazy — keeps the module import-light for tests)
# --------------------------------------------------------------------------- #
def _build_llm(model: str, temperature: float, max_tokens: int = 1200):
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        default_request_timeout=float(os.getenv("LLM_TIMEOUT", "45")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )


def _default_ch_select(sql: str) -> list:
    import ch_client
    return ch_client.select(sql)


# --------------------------------------------------------------------------- #
# Dynamic dispatch — the runtime fan-out (module-level so it's edge-friendly)
# --------------------------------------------------------------------------- #
def assign_workers(state: State):
    """Fan out one worker per planned task — the edge list is computed at runtime.

    Two runs of the same binary produce different numbers of `worker` nodes.
    """
    from langgraph.types import Send
    return [
        Send("worker", {"task": t, "symptom": state["symptom"]})
        for t in state.get("tasks", [])
    ]


class Investigator:
    """The orchestrator-workers agent. Components are injectable for testing."""

    def __init__(self, *, planner_llm=None, worker_llm=None, gate_llm=None,
                 synth_llm=None, ch_select: Optional[Callable] = None,
                 catalog=None, planner_label: Optional[str] = None,
                 max_workers: int = MAX_WORKERS_TOTAL, max_rounds: int = MAX_PLAN_ROUNDS):
        self._planner = planner_llm
        self._worker = worker_llm
        self._gate = gate_llm
        self._synth = synth_llm
        self._ch_select = ch_select if ch_select is not None else _default_ch_select
        self.catalog = catalog or CATALOG
        self.planner_label = planner_label or PLANNER_LABEL
        self.max_workers = max_workers
        self.max_rounds = max_rounds
        self.graph = self._build()

    # -------------------------------------------------------- lazy LLM getters
    def _planner_llm(self):
        if self._planner is None:
            self._planner = _build_llm(DEFAULT_MODEL, TEMPERATURE)
        return self._planner

    def _worker_llm(self):
        if self._worker is None:
            self._worker = _build_llm(WORKER_MODEL, TEMPERATURE, max_tokens=400)
        return self._worker

    def _gate_llm(self):
        if self._gate is None:
            self._gate = _build_llm(WORKER_MODEL, TEMPERATURE, max_tokens=400)
        return self._gate

    def _synth_llm(self):
        if self._synth is None:
            self._synth = _build_llm(DEFAULT_MODEL, TEMPERATURE, max_tokens=1600)
        return self._synth

    # ------------------------------------------------------------- prompt help
    def _render_prompt(self, name: str, label: str, fallback: str, **variables):
        prompt_obj = lf.get_prompt(name, label=label)
        if prompt_obj is not None:
            try:
                return prompt_obj.compile(**variables), prompt_obj
            except Exception:
                pass
        return _compile_local(fallback, **variables), None

    # ------------------------------------------------------------------- nodes
    def orchestrator_node(self, state: State) -> dict:
        prior = [f["analysis_type"] for f in state.get("findings", [])]
        round_no = state.get("round", 1)
        spawned = state.get("workers_spawned", 0)
        with lf.observe("orchestrator", as_type="agent",
                        input={"symptom": state["symptom"], "round": round_no,
                               "prior_findings": prior}) as obs:
            fault = state.get("fault")
            if fault == "overplan":
                plan = self._overplan(prior)
            else:
                try:
                    plan = self._plan(state, prior)
                except PlanError:
                    # abort planning cleanly: minimal safe plan so the run still
                    # yields a diagnosis instead of crashing on a bad LLM reply.
                    plan = Plan(tasks=[Task(analysis_type="slow_queries",
                                            focus="fallback plan", rationale="planner aborted")],
                                reasoning="planner failed twice; safe fallback")
            plan = enforce_guards(plan, prior, spawned, max_workers=self.max_workers)
            if obs:
                obs.update(output=plan.model_dump())  # <-- THE PLAN OBJECT (pattern-defining)
        tasks = [t.model_dump() for t in plan.tasks]
        line = (f"plan (round {round_no}) → {len(tasks)} task(s): "
                + ", ".join(t["analysis_type"] for t in tasks))
        return {"tasks": tasks, "planned": tasks,
                "workers_spawned": spawned + len(tasks),
                "trace": [line]}

    def worker_node(self, ws: dict) -> dict:
        task = ws["task"]
        analysis_type = task["analysis_type"]
        focus = task.get("focus", "")
        rows: list = []
        with lf.observe("worker", as_type="agent",
                        input={"analysis_type": analysis_type, "focus": focus}) as wobs:
            # identity in METADATA, never the span name — keeps the Aggregated
            # Agent Graph collapsing to `worker (N/N)`.
            if wobs:
                wobs.update(metadata={"analysis_type": analysis_type, "focus": focus})
            analysis = self.catalog.get(analysis_type)
            with lf.observe("run-system-query", as_type="tool",
                            input={"analysis_type": analysis_type}) as tobs:
                sql = analysis.render(focus) if analysis else ""
                rows = self._ch_select(sql) if sql else [{"error": "unknown analysis_type"}]
                if tobs:
                    tobs.update(output={"sql": sql, "row_count": len(rows)})
            summary = self._interpret(analysis_type, focus, rows)
            finding = {"analysis_type": analysis_type, "focus": focus,
                       "summary": summary[:1200]}
            if wobs:
                wobs.update(output=finding)
        return {"findings": [finding],
                "trace": [f"worker[{analysis_type}] → {len(rows)} row(s)"]}

    def replan_gate_node(self, state: State) -> dict:
        covered = [f["analysis_type"] for f in state.get("findings", [])]
        round_no = state.get("round", 1)
        spawned = state.get("workers_spawned", 0)
        with lf.observe("replan-gate", as_type="evaluator",
                        input={"covered": covered, "round": round_no}) as obs:
            verdict = self._gate_verdict(state, covered)
            replan = should_replan(verdict.sufficient, round_no, spawned,
                                   self.max_rounds, self.max_workers)
            if obs:
                obs.update(output={"sufficient": verdict.sufficient,
                                   "missing": verdict.missing_analyses,
                                   "replan": replan})
        lf.score_current_span("coverage_sufficient", 1.0 if verdict.sufficient else 0.0,
                              comment=f"round {round_no}")
        line = (f"gate (round {round_no}) → "
                + ("sufficient" if verdict.sufficient else f"insufficient, missing {verdict.missing_analyses}")
                + (" → replan" if replan else ""))
        out: dict = {"trace": [line], "_replan": replan}
        if replan:
            out["round"] = round_no + 1
        return out

    def synthesize_node(self, state: State) -> dict:
        findings = state.get("findings", [])
        text, prompt_obj = self._render_prompt(
            SYNTH_PROMPT, "production", SYNTH_FALLBACK,
            symptom=state["symptom"], findings=self._format_findings(findings))
        with lf.observe("synthesize-diagnosis", as_type="generation",
                        input={"symptom": state["symptom"],
                               "findings": [f["analysis_type"] for f in findings]}) as obs:
            if obs and prompt_obj is not None:
                try:
                    obs.update(prompt=prompt_obj)
                except Exception:
                    pass
            resp = self._synth_llm().invoke(text)
            diagnosis = getattr(resp, "content", str(resp))
            if obs:
                obs.update(output=diagnosis)
        # Trace-level scores here (terminal node → an active trace context is
        # guaranteed, unlike calling score_current_trace after graph.invoke).
        lf.score_current_trace("worker_count", float(state.get("workers_spawned", 0)),
                               comment="fan-out chosen by the planner")
        lf.score_current_trace("plan_execution_complete",
                               1.0 if all_tasks_have_findings(state) else 0.0,
                               comment="deterministic structural check")
        return {"diagnosis": diagnosis,
                "trace": [f"diagnosis → synthesised {len(findings)} finding(s)"]}

    # ----------------------------------------------------------- node internals
    def _plan(self, state: State, prior) -> Plan:
        text, prompt_obj = self._render_prompt(
            PLANNER_PROMPT, self.planner_label, PLANNER_FALLBACK,
            symptom=state["symptom"], catalog=CATALOG_DIGEST,
            prior_findings=", ".join(prior) or "none",
            max_workers=str(self.max_workers))
        with lf.observe("plan-analyses", as_type="generation",
                        input={"symptom": state["symptom"]}) as obs:
            if obs and prompt_obj is not None:
                try:
                    obs.update(prompt=prompt_obj)
                except Exception:
                    pass
            plan = self._plan_with_retry(text)
            if obs:
                obs.update(output=plan.model_dump())
        return plan

    def _plan_with_retry(self, text: str, attempts: int = 2) -> Plan:
        return plan_with_retry(self._planner_llm(), text, attempts=attempts)

    def _overplan(self, prior) -> Plan:
        """Deterministic maximum fan-out (fault:overplan) — reliably hits the cap."""
        types = [k for k in self.catalog.keys() if k not in set(prior)][:self.max_workers]
        tasks = [Task(analysis_type=t, focus="fault:overplan full sweep",
                      rationale="scaling rules removed; investigate everything")
                 for t in types]
        return Plan(tasks=tasks, reasoning="FAULT overplan: maximum fan-out")

    def _interpret(self, analysis_type: str, focus: str, rows: list) -> str:
        from ch_client import format_rows
        text, prompt_obj = self._render_prompt(
            WORKER_PROMPT, "production", WORKER_FALLBACK,
            analysis=analysis_type, focus=focus or "general", rows=format_rows(rows))
        with lf.observe("interpret-findings", as_type="generation",
                        input={"analysis": analysis_type}) as obs:
            if obs and prompt_obj is not None:
                try:
                    obs.update(prompt=prompt_obj)
                except Exception:
                    pass
            resp = self._worker_llm().invoke(text)
            content = getattr(resp, "content", str(resp))
            if obs:
                obs.update(output=content)
        return content.strip()

    def _gate_verdict(self, state: State, covered) -> GateVerdict:
        remaining = sorted(k for k in CATALOG_KEYS if k not in set(covered))
        text = _compile_local(GATE_FALLBACK, symptom=state["symptom"],
                              covered=", ".join(covered) or "none",
                              remaining=", ".join(remaining) or "none")
        try:
            verdict = self._gate_llm().with_structured_output(GateVerdict).invoke(text)
            verdict.missing_analyses = [
                m for m in verdict.missing_analyses
                if m in CATALOG_KEYS and m not in set(covered)
            ]
            return verdict
        except Exception:
            # fail-safe: stop the loop rather than risk runaway re-planning
            return GateVerdict(sufficient=True, missing_analyses=[],
                               reasoning="gate error → stop (fail-safe)")

    @staticmethod
    def _format_findings(findings) -> str:
        blocks = []
        for f in findings:
            blocks.append(f"[{f['analysis_type']}] (focus: {f.get('focus','')})\n{f.get('summary','')}")
        return "\n\n---\n\n".join(blocks) if blocks else "(no findings)"

    # ------------------------------------------------------------------- edges
    def _gate_edge(self, state: State) -> str:
        return "orchestrator" if state.get("_replan") else "synthesize"

    # ------------------------------------------------------------------- build
    def _build(self):
        from langgraph.graph import StateGraph, START, END
        g = StateGraph(State)
        g.add_node("orchestrator", self.orchestrator_node)
        g.add_node("worker", self.worker_node)
        g.add_node("replan_gate", self.replan_gate_node)
        g.add_node("synthesize", self.synthesize_node)

        g.add_edge(START, "orchestrator")
        g.add_conditional_edges("orchestrator", assign_workers, ["worker"])  # dynamic dispatch
        g.add_edge("worker", "replan_gate")
        g.add_conditional_edges("replan_gate", self._gate_edge,
                                {"orchestrator": "orchestrator", "synthesize": "synthesize"})
        g.add_edge("synthesize", END)
        return g.compile()

    # --------------------------------------------------------------------- run
    def run(self, symptom: str, session_id: Optional[str] = None,
            fault: Optional[str] = None) -> dict:
        session_id = session_id or lf.new_session_id()
        handler = lf.get_handler()
        config = {"callbacks": [handler]} if handler else {}
        tags = list(lf.DEFAULT_TAGS) + ([f"fault:{fault}"] if fault else [])
        initial: State = {"symptom": symptom, "tasks": [], "planned": [], "findings": [],
                          "round": 1, "workers_spawned": 0, "fault": fault,
                          "diagnosis": "", "trace": [], "_replan": False}
        with lf.trace_context("investigate-cluster-symptom", session_id=session_id, tags=tags):
            final = self.graph.invoke(initial, config=config)
        lf.flush()
        return {
            "symptom": symptom,
            "diagnosis": final.get("diagnosis", ""),
            "workers_spawned": final.get("workers_spawned", 0),
            "rounds": final.get("round", 1),
            "plan": final.get("planned", []),
            "tasks": final.get("tasks", []),
            "findings": final.get("findings", []),
            "steps": final.get("trace", []),
            "session_id": session_id,
            "fault": fault,
        }


def create_investigator(**kwargs) -> Investigator:
    return Investigator(**kwargs)


def run_pipeline(symptom: str, planner_label: str = "production",
                 session_id: Optional[str] = None, fault: Optional[str] = None) -> dict:
    """Experiment entry point — build an investigator bound to a planner label."""
    inv = Investigator(planner_label=planner_label)
    return inv.run(symptom, session_id=session_id, fault=fault)
