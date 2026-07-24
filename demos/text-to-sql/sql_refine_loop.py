"""Evaluator-Optimizer loop (Pattern #5): generate SQL -> critique against real
ClickHouse evidence -> refine with critique history, until accepted or the
iteration/oscillation budget is exhausted.

Structure per iteration, all under one ``sql-refine-loop`` span:
    generate-sql   (generation, managed-prompt-linked, metadata.iteration + history)
    gather-evidence (tool span, output = EXPLAIN excerpt / error / result rows)
    critique-sql   (native `evaluator` observation, output = structured Critique JSON,
                    span-level score `sql_critic_score`)

Cross-iteration convergence (``converged`` / ``iterations_to_accept`` /
``sql_quality_delta``) is app-computed and pushed as trace-level scores, because a
Langfuse code evaluator sees one matched observation and cannot read sibling
iterations (field guide §Evaluate).

Local prompt fallbacks mirror the Langfuse-managed prompts seeded by
``scripts/seed-app-prompts.py`` (``text-to-sql-generator`` / ``text-to-sql-critic``),
so a fresh clone runs without Langfuse. Keep them in sync by hand.
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import List, Optional

from sql_evidence import Evidence, gather_evidence
import langfuse_config as lf

MAX_ITERATIONS = int(os.getenv("REFINE_MAX_ITERATIONS", "3"))
ACCEPT_THRESHOLD = float(os.getenv("REFINE_ACCEPT_THRESHOLD", "0.9"))

GENERATOR_PROMPT_NAME = os.getenv("GENERATOR_PROMPT_NAME", "text-to-sql-generator")
CRITIC_PROMPT_NAME = os.getenv("CRITIC_PROMPT_NAME", "text-to-sql-critic")

GENERATOR_TEMPERATURE = float(os.getenv("REFINE_GENERATOR_TEMPERATURE", "0.2"))
CRITIC_TEMPERATURE = float(os.getenv("REFINE_CRITIC_TEMPERATURE", "0.0"))


# --------------------------------------------------------------------------- LLM
@lru_cache(maxsize=4)
def _llm(temperature: float):
    # Built lazily and cached per temperature (generator 0.2, critic 0.0). Imported
    # here rather than at module load so unit tests can monkeypatch `_ask` without
    # constructing a real client.
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        temperature=temperature,
        max_tokens=1200,
        default_request_timeout=float(os.getenv("LLM_TIMEOUT", "45")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )


def _ask(prompt: str, temperature: float = 0.0) -> str:
    return _llm(temperature).invoke(prompt).content.strip()


def _strip_fences(text: str) -> str:
    return text.replace("```sql", "").replace("```", "").strip()


# ----------------------------------------------------------------- local prompts
# These MIRROR the Langfuse-managed prompts in scripts/seed-app-prompts.py. When
# managed and fallback match, enabling prompt management changes nothing until you
# edit the prompt in Langfuse.
_FALLBACK_GENERATOR = (
    "You write a single read-only ClickHouse SQL query answering the user's question "
    "against the public demo datasets at sql.clickhouse.com.\n\n"
    "Rules:\n"
    "- A single SELECT (or WITH ... SELECT). Never INSERT/UPDATE/DELETE/DROP/ALTER.\n"
    "- ALWAYS include a LIMIT.\n"
    "- Use fully-qualified database.table names (e.g. uk.uk_price_paid, nyc_taxi.trips,\n"
    "  stackoverflow.posts).\n"
    "- Reply with ONLY the SQL, no prose, no markdown fences.\n\n"
    "Question: {question}\n\n"
    "Analysis of which datasets apply:\n{analysis}\n\n"
    "Critiques of previous attempts (fix EVERY cited issue — do NOT repeat a mistake a "
    "critique already flagged):\n{critique_history}\n\n"
    "SQL:"
)

_FALLBACK_CRITIC = (
    "You are a strict SQL critic. Judge the candidate SQL ONLY from the EVIDENCE below "
    "(real EXPLAIN + bounded execution against ClickHouse) — never from the SQL text "
    "alone. You may NOT accept if any evidence check is false.\n\n"
    "Question: {question}\n\n"
    "Candidate SQL:\n{candidate_sql}\n\n"
    "{evidence}\n\n"
    "Return STRICT JSON only, no prose, with keys:\n"
    '  "verdict": "accept" | "revise"\n'
    '  "score": a number 0.0-1.0 (how well the SQL answers the question, grounded in evidence)\n'
    '  "cited_evidence": a verbatim line you are quoting from the EVIDENCE above\n'
    '  "feedback": ONE actionable fix, grounded in the citation\n'
)

# opinion-only critic (Experiment B / collusion demo) — judges from SQL text alone,
# NO evidence. Never labeled production; used only to make reward hacking measurable.
_FALLBACK_CRITIC_OPINION_ONLY = (
    "You are a SQL critic. Judge the candidate SQL from the SQL text alone.\n\n"
    "Question: {question}\n\n"
    "Candidate SQL:\n{candidate_sql}\n\n"
    "Return STRICT JSON only, no prose, with keys:\n"
    '  "verdict": "accept" | "revise"\n'
    '  "score": a number 0.0-1.0\n'
    '  "cited_evidence": ""\n'
    '  "feedback": ONE actionable fix\n'
)


def _format_critique_history(history: "List[Critique]") -> str:
    if not history:
        return "None yet — first attempt."
    return "\n\n".join(
        f"CRITIQUE {n} (iteration {n}): {c.feedback}\n  evidence: {c.cited_evidence}"
        for n, c in enumerate(history, 1)
    )


# --------------------------------------------------------------------- dataclasses
@dataclass
class Critique:
    """The structured critique object (field guide §3) — not a binary verdict."""

    verdict: str  # "accept" | "revise"
    score: float  # 0.0-1.0
    checks: dict = field(default_factory=dict)  # deterministic + evidence checks
    cited_evidence: str = ""  # verbatim excerpt the critic must quote
    feedback: str = ""  # ONE actionable fix, grounded in the citation


@dataclass
class RefineResult:
    sql: str
    rows_preview: str
    converged: bool
    iterations: int
    stop_reason: str
    critic_scores: List[float]

    def as_context(self) -> str:
        """Rendered context handed to the existing response_chain."""
        head = "" if self.converged else (
            f"WARNING: no candidate passed review after {self.iterations} iteration(s) "
            f"({self.stop_reason}). Best attempt shown — caveat the answer.\n\n"
        )
        return f"{head}Validated SQL:\n{self.sql}\n\nExecution result:\n{self.rows_preview}"


# ------------------------------------------------------------------------- helpers
def _get_prompt(name: str, label: str):
    """Fetch a managed prompt by label (None -> local fallback)."""
    return lf.get_managed_prompt(name, label=label)


def _parse_critique_json(raw: str, ev: Evidence) -> Optional[Critique]:
    """Parse the critic's JSON. Returns None on malformed JSON (caller retries)."""
    text = _strip_fences(raw)
    # tolerate leading/trailing prose by extracting the first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except Exception:
        return None
    try:
        verdict = str(data.get("verdict", "revise")).strip().lower()
        verdict = "accept" if verdict.startswith("accept") else "revise"
        score = float(data.get("score", 0.0))
    except Exception:
        return None
    score = max(0.0, min(1.0, score))
    return Critique(
        verdict=verdict,
        score=score,
        checks=dict(ev.checks),
        cited_evidence=str(data.get("cited_evidence", "") or "")[:500],
        feedback=str(data.get("feedback", "") or "")[:500],
    )


# --------------------------------------------------------------------------- steps
def _generate(
    question: str,
    analysis: str,
    history: "List[Critique]",
    iteration: int,
    fault: Optional[str] = None,
    generator_label: str = "production",
) -> str:
    """Per-iteration generation observation: managed prompt (linked), critique
    history in the prompt AND metadata. Optional `fault:wrong-column` injection
    guarantees a multi-iteration demo beat (repo convention)."""
    with lf.langfuse_observe(
        "generate-sql", as_type="generation",
        input={"question": question, "iteration": iteration},
    ) as obs:
        prompt_obj = _get_prompt(GENERATOR_PROMPT_NAME, generator_label)
        critique_block = _format_critique_history(history)
        text = None
        if prompt_obj is not None:
            try:
                text = prompt_obj.compile(
                    question=question, analysis=analysis, critique_history=critique_block
                )
            except Exception:
                prompt_obj = None
        if text is None:
            text = _FALLBACK_GENERATOR.format(
                question=question, analysis=analysis, critique_history=critique_block
            )

        sql = _strip_fences(_ask(text, temperature=GENERATOR_TEMPERATURE))

        # Deterministic fault: corrupt iteration-1 SQL so EXPLAIN returns
        # UNKNOWN_IDENTIFIER and the refine beat is guaranteed on stage.
        if fault == "wrong-column" and iteration == 1:
            sql = sql.replace("price", "price_gbp", 1)

        if obs:
            update_kwargs = {
                "output": sql,
                "metadata": {
                    "iteration": iteration,
                    "max_iterations": MAX_ITERATIONS,
                    "critique_history": [c.feedback for c in history],
                },
            }
            if prompt_obj is not None:
                update_kwargs["prompt"] = prompt_obj  # prompt link -> Deploy node
            obs.update(**update_kwargs)
    return sql


def _gather(sql: str, iteration: int) -> Evidence:
    """Evidence-gathering tool span; output = real EXPLAIN/execution text."""
    with lf.langfuse_observe(
        "gather-evidence", as_type="tool", input={"sql": sql, "iteration": iteration}
    ) as obs:
        ev = gather_evidence(sql)
        if obs:
            obs.update(
                output=ev.as_text(),
                metadata={"iteration": iteration, "checks": ev.checks},
            )
    return ev


def _critique(
    question: str,
    sql: str,
    ev: Evidence,
    iteration: int,
    history: "List[Critique]",
    critic_label: str = "production",
) -> Critique:
    """Native `evaluator` observation + span score + the anti-collusion hard rule:
    evidence overrides the critic's opinion."""
    with lf.langfuse_observe(
        "critique-sql", as_type="evaluator",
        input={"candidate_sql": sql, "iteration": iteration},
    ) as obs:
        prompt_obj = _get_prompt(CRITIC_PROMPT_NAME, critic_label)
        evidence_grounded = critic_label != "opinion-only"

        def _compile() -> str:
            if prompt_obj is not None:
                try:
                    if evidence_grounded:
                        return prompt_obj.compile(
                            question=question, candidate_sql=sql, evidence=ev.as_text()
                        )
                    return prompt_obj.compile(question=question, candidate_sql=sql)
                except Exception:
                    pass
            if evidence_grounded:
                return _FALLBACK_CRITIC.format(
                    question=question, candidate_sql=sql, evidence=ev.as_text()
                )
            return _FALLBACK_CRITIC_OPINION_ONLY.format(question=question, candidate_sql=sql)

        text = _compile()
        raw = _ask(text, temperature=CRITIC_TEMPERATURE)
        crit = _parse_critique_json(raw, ev)
        if crit is None:
            # Hard failure (malformed JSON), NOT a quality failure -> one retry
            # outside the quality loop (field guide §4). Still bad -> safe revise.
            raw = _ask(text + "\n\nReturn STRICT JSON only.", temperature=CRITIC_TEMPERATURE)
            crit = _parse_critique_json(raw, ev)
        if crit is None:
            crit = Critique(
                verdict="revise", score=0.0, checks=dict(ev.checks),
                cited_evidence="", feedback="critic returned malformed JSON",
            )

        # ANTI-COLLUSION RULE: evidence overrides opinion. The critic can NEVER
        # accept while any deterministic/evidence check is false. Only enforced for
        # the evidence-grounded critic — the opinion-only variant is deliberately
        # allowed to collude (that is Experiment B's whole point).
        if evidence_grounded and not all(ev.checks.values()):
            crit.verdict = "revise"
        # Acceptance threshold.
        if crit.score < ACCEPT_THRESHOLD:
            crit.verdict = "revise"

        if obs:
            obs.update(
                output=asdict(crit),
                metadata={
                    "iteration": iteration,
                    "checks": ev.checks,
                    "critique_history": [c.feedback for c in history],
                },
            )
        # Span-level (not trace-level) because this step repeats per iteration —
        # same convention as retrieval_relevance in demos/agentic-rag/graph.py.
        lf.score_current_span("sql_critic_score", crit.score, comment=crit.feedback)
    return crit


# ---------------------------------------------------------------------------- loop
def run_refine_loop(
    question: str,
    analysis: str,
    fault: Optional[str] = None,
    generator_label: str = "production",
    critic_label: str = "production",
    max_iterations: Optional[int] = None,
) -> RefineResult:
    """Generate -> critique (against real evidence) -> refine, until accepted or
    the iteration/oscillation budget is exhausted. Returns the best candidate + its
    execution preview, and pushes convergence scores to the trace."""
    max_iters = max_iterations if max_iterations is not None else MAX_ITERATIONS
    history: List[Critique] = []
    candidates: List[str] = []
    scores: List[float] = []
    stop_reason = "max_iterations"
    last_ev: Optional[Evidence] = None

    with lf.langfuse_observe("sql-refine-loop", as_type="span", input=question) as loop_obs:
        for i in range(1, max_iters + 1):
            sql = _generate(question, analysis, history, iteration=i,
                            fault=fault, generator_label=generator_label)
            if sql in candidates:  # oscillation guard (field guide §4)
                stop_reason = "oscillation"
                break
            candidates.append(sql)
            last_ev = _gather(sql, iteration=i)
            crit = _critique(question, sql, last_ev, iteration=i,
                             history=history, critic_label=critic_label)
            scores.append(crit.score)
            if crit.verdict == "accept":
                stop_reason = "accepted"
                break
            history.append(crit)

        converged = stop_reason == "accepted"

        # Convergence: app-computed, pushed as trace scores. Cross-iteration eval is
        # NOT a built-in Langfuse evaluator — the loop holds the history. Pushed
        # inside the loop span so the active trace is always resolvable.
        lf.score_current_trace(
            "converged", 1.0 if converged else 0.0, data_type="BOOLEAN",
            comment=f"stop_reason={stop_reason} after {len(candidates)} iteration(s)",
        )
        lf.score_current_trace("iterations_to_accept", float(len(candidates)))
        if len(scores) > 1:
            lf.score_current_trace(
                "sql_quality_delta", scores[-1] - scores[0],
                comment=f"critic scores by iteration: {scores}",
            )
        if loop_obs:
            loop_obs.update(output={
                "converged": converged,
                "iterations": len(candidates),
                "stop_reason": stop_reason,
                "critic_scores": scores,
            })

    return RefineResult(
        sql=candidates[-1] if candidates else "",
        rows_preview=last_ev.rows_preview if last_ev else "",
        converged=converged,
        iterations=len(candidates),
        stop_reason=stop_reason,
        critic_scores=scores,
    )
