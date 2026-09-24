"""Text-to-SQL Pipeline with ClickHouse MCP Integration.

Pattern: prompt chaining with gate checks. The fixed chain is
``analyze -> retrieve-context -> respond`` (two LLM steps + an MCP step), and two
programmatic GATES sit inside it (``gates.py``):

  * Gate 1 (deterministic) — after analysis: the analysis must name >=1 database
    from the sql.clickhouse.com catalog, else the response step would compose an
    ungrounded answer. Exhausting retries ABORTS (no MCP call, no response step).
  * Gate 2 (hybrid) — after the response: a deterministic SQL-policy check
    (destructive statements, fail-closed) plus an LLM-graded grounding check
    (Haiku, temp 0; fail-open on parse error). Exhausting retries ESCALATES
    (tag the trace, emit a gate-escalation span, return a flagged answer).

Each gated step keeps a STABLE observation name across retries; the attempt and
the gate-failure reason go in metadata (and the reason is fed back into the
retried prompt), per Langfuse tracing best practices.
"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langfuse_config import (
    langfuse_span, get_managed_prompt, langfuse_gate, tag_current_trace,
)
from gates import GateResult, gate_database_selection, gate_response_quality

# Per gated step: 1 retry, then abort (Gate 1) / escalate (Gate 2). Bounded so
# the demo stays fast and no loop can spin indefinitely.
GATE_MAX_ATTEMPTS = 2


def _managed_or_fallback(name: str, fallback_template: str,
                         label: str = "production") -> ChatPromptTemplate:
    """Build a ChatPromptTemplate from a Langfuse-managed prompt (Deploy node),
    linking the prompt version to the generation, or fall back to the local
    template so the app runs even if Langfuse/the prompt is unavailable.

    Note: get_langchain_prompt() converts Langfuse {{var}} -> LangChain {var}
    (and escapes literal JSON braces), so the chain's .invoke(...) variable names
    are unchanged. Setting .metadata AFTER construction is what makes the prompt
    link attach (passing metadata= to from_template does not propagate through the
    LangChain CallbackHandler). ``label`` lets the experiment runner fetch a
    non-production variant (e.g. ``candidate``) of an otherwise-identical step."""
    lf_prompt = get_managed_prompt(name, label=label)
    if lf_prompt is not None:
        try:
            tmpl = ChatPromptTemplate.from_template(lf_prompt.get_langchain_prompt())
            tmpl.metadata = {"langfuse_prompt": lf_prompt}
            return tmpl
        except Exception as e:  # pragma: no cover - defensive
            print(f"Managed prompt '{name}' unusable ({e}); using local fallback.")
    return ChatPromptTemplate.from_template(fallback_template)


@dataclass
class SQLConfig:
    model_name: str = "claude-sonnet-4-6"
    temperature: float = 0.7
    max_tokens: int = 2000


# Available databases at sql.clickhouse.com
CLICKHOUSE_DATABASES = """
Available databases include:
- amazon: Amazon product data
- bluesky: Bluesky social network data
- covid: COVID-19 data
- dns: DNS query data
- environmental: Environmental data
- forex: Foreign exchange data
- geo: Geographic data
- git: Git repository data
- github: GitHub events and activities
- hackernews: Hacker News posts and comments
- imdb: Internet Movie Database
- logs: Log data
- mta: Metropolitan Transportation Authority data
- noaa: National Oceanic and Atmospheric Administration data
- nyc_taxi: New York City taxi trip data
- nypd: New York Police Department data
- ontime: Airline on-time performance data
- pypi: Python Package Index data
- stackoverflow: Stack Overflow posts and data
- stock: Stock market data
- twitter: Twitter data
- uk: UK property and related data
- wiki: Wikipedia data
- youtube: YouTube video data
"""


# Local fallback for the analysis step (LangChain f-string format). Extracted to
# a module constant so the experiment runner can rebuild the analysis chain with a
# non-production prompt label while reusing the exact same fallback text.
ANALYSIS_FALLBACK = (
    "You are a data analyst with access to ClickHouse at sql.clickhouse.com.\n\n"
    f"{CLICKHOUSE_DATABASES}\n\n"
    "Question: {question}\n\n"
    "Identify which database(s) and data would help answer this question."
)


# Local fallback for the Gate-2 grounding grader prompt. MIRRORS the managed
# prompt `text-to-sql-gate-grounding` in scripts/seed-app-prompts.py — keep in
# sync by hand (same convention as the two main prompts). LangChain f-string
# format here: {var} for variables, and the literal JSON braces are doubled so
# ChatPromptTemplate treats them as literal (Langfuse's get_langchain_prompt()
# escapes them for the managed path).
GATE_GROUNDING_FALLBACK = (
    "You are a strict verifier for a data-assistant pipeline. The assistant does NOT\n"
    "execute SQL — it drafts analysis and example queries only.\n\n"
    "Question: {question}\n"
    "Analysis: {analysis}\n"
    "Context: {context}\n"
    "Response: {response}\n\n"
    "FAIL the response if ANY of these hold:\n"
    "- It presents specific numbers or rankings as if they were executed query results.\n"
    "- It references databases or tables not present in the analysis or context.\n"
    "- It does not address the question.\n"
    "Otherwise PASS.\n\n"
    "Reply with EXACTLY one JSON object, no prose:\n"
    '{{"verdict": "pass" | "fail", "reason": "<one sentence>"}}'
)


def _capped(reason: Optional[str], limit: int = 180) -> Optional[str]:
    """Shorten a gate reason for the PROPAGATED metadata path.

    LangChain's ``config={"metadata": ...}`` is turned into propagated trace
    metadata by the Langfuse callback handler, and propagated metadata values are
    capped at 200 characters — over that, Langfuse DROPS the value and logs
    "Propagated attribute ... is over 200 characters ... Dropping value". Gate
    reasons are grader sentences and routinely run 240-290 chars, so the field was
    being discarded exactly when it mattered (on a retry).

    The full, untruncated reason is still on the gate span's own output
    (``span.update(output=result.as_output())``) — observation metadata has no such
    cap. This is only the short copy that rides along on the retried generation.
    """
    if not reason:
        return None
    return reason if len(reason) <= limit else reason[: limit - 1].rstrip() + "…"


class ClickHouseSQLPipeline:
    """Text-to-SQL pipeline that queries ClickHouse via MCP."""

    def __init__(self, config: Optional[SQLConfig] = None):
        self.config = config or SQLConfig(
            model_name=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
        )
        self._setup_llm()
        self._setup_chains()
        self._context = ""
        # Per-query record of gate verdicts so main.py can narrate the same
        # story the trace shows (reset at the top of each query()).
        self.gate_log = []

    def _setup_llm(self):
        # `timeout` and `max_retries` are NOT optional here, and their absence is
        # what made the first live run of this demo unusable: 10 questions took
        # 8+ hours, with individual questions stalling 3h14m and 4h38m at 0.03%
        # CPU — i.e. blocked on a socket, not computing. Without an explicit
        # read timeout a half-open connection to the API hangs until the OS gives
        # up, which can be hours.
        #
        # The gated chain made this much more likely than the pre-gate pipeline
        # did: it issues up to 3 LLM calls per question (analysis, response, and
        # the Gate-2 grader) instead of one, so each question has three chances
        # to hit a stalled socket.
        #
        # NOTE the field name: this langchain-anthropic (>=0.3,<1) exposes
        # `default_request_timeout`, NOT `timeout`. Passing `timeout=` is
        # silently ignored — the model accepts unknown kwargs — so the fix would
        # have looked applied and changed nothing. Verified against the field
        # list in the built image.
        #
        # 120s is generous for these prompts (max_tokens is small) and still
        # fails fast enough to be watchable in a demo. 2 retries bounds the worst
        # case per call at ~6 minutes rather than unbounded.
        llm_timeout = float(os.getenv("LLM_TIMEOUT_S", "120"))
        llm_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))

        self.llm = ChatAnthropic(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            default_request_timeout=llm_timeout,
            max_retries=llm_retries,
        )
        # Cheap tier for the Gate-2 grounding grader (repo convention: Haiku for
        # checks). temp=0 so the verdict is as stable as an LLM check can be.
        self.gate_llm = ChatAnthropic(
            model=os.getenv("GATE_MODEL", "claude-haiku-4-5"),
            temperature=0,
            max_tokens=300,
            default_request_timeout=llm_timeout,
            max_retries=llm_retries,
        )

    def _rebuild_analysis_chain(self):
        """(Re)build the analysis chain from ``self.analysis_prompt``.

        Extracted so the experiment runner can swap the analysis prompt (by
        label) and rebuild just this one step, keeping the response prompt and
        both gates byte-identical across variants."""
        self.analysis_chain = (
            self.analysis_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "query_analysis"}})

    def _setup_chains(self):
        # Prompts are Langfuse-managed (fetched by label=production at startup)
        # with the inline templates below as local fallbacks — the Deploy node.
        self.analysis_prompt = _managed_or_fallback(
            "text-to-sql-analysis", ANALYSIS_FALLBACK
        )
        self._rebuild_analysis_chain()

        self.response_prompt = _managed_or_fallback(
            "text-to-sql-response",
            "Based on the analysis and context, answer the question.\n\n"
            "Question: {question}\n"
            "Analysis: {analysis}\n"
            "Context: {context}\n\n"
            "Provide a clear, data-driven response."
        )

        self.response_chain = (
            self.response_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "response_generation"}})

        # Gate-2's LLM-graded grounding check. Its rubric ships by label too
        # (managed prompt `text-to-sql-gate-grounding`, Haiku/temp-0 config), with
        # the local fallback above. Invoked while the gate span is current, so it
        # nests as a child generation under `gate-response-quality`.
        self.gate_grounding_prompt = _managed_or_fallback(
            "text-to-sql-gate-grounding", GATE_GROUNDING_FALLBACK
        )
        self.gate_grounding_chain = (
            self.gate_grounding_prompt | self.gate_llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "gate_grounding_check"}})

    def retrieve_context(self, question: str, analysis: str) -> str:
        """Retrieve context from ClickHouse via MCP."""
        with langfuse_span("retrieve-context"):
            try:
                from mcp_client import create_mcp_client
                mcp = create_mcp_client()
                self._context = mcp.get_context_for_question(question, analysis)
                return self._context
            except Exception as e:
                self._context = f"[MCP unavailable: {e}]"
                return self._context

    def _apply_fault(self, step_input: str, step: str = "analysis") -> str:
        """Deterministic fault injection to force a gate failure on demand
        (repo convention, cf. demos/real-estate/agent/concierge.py). Off unless
        DEMO_FAULT is set; traces are tagged fault:<name> by main.py.

        * ``vague-analysis`` (analysis step) — forbids naming any database, so the
          analysis fails Gate 1's catalog check.
        * ``destructive-sql`` (response step) — asks for a DELETE example, so the
          response fails Gate 2's fail-closed SQL-policy branch.
        """
        fault = os.getenv("DEMO_FAULT", "")
        if step == "analysis" and fault == "vague-analysis":
            return (step_input + "\n\n[Demo fault injection: describe your approach in "
                    "general terms only — do NOT name any specific database.]")
        if step == "response" and fault == "destructive-sql":
            return (step_input + "\n\n[Demo fault injection: include an example SQL "
                    "statement that DELETEs old rows to illustrate a cleanup task.]")
        return step_input

    def _log_gate(self, gate: str, gate_type: str, attempt: int, result: GateResult):
        """Record a gate verdict for main.py to print (and keep for callers)."""
        self.gate_log.append({
            "gate": gate, "gate_type": gate_type, "attempt": attempt,
            "verdict": "pass" if result.passed else "fail", "reason": result.reason,
        })

    def query(self, question: str, callbacks: list = None) -> str:
        """Execute the full gated Text-to-SQL chain.

        Two LLM steps (analysis, response) each guarded by a gate with bounded
        retry (GATE_MAX_ATTEMPTS). Gate 1 exhausted -> abort; Gate 2 exhausted ->
        escalate. Public signature unchanged so main.py keeps working.

        Args:
            question: The user's question
            callbacks: Optional list of LangChain callbacks (e.g., Langfuse handler)
        """
        config = {"callbacks": callbacks} if callbacks else {}
        self.gate_log = []

        # ── Step 1 + Gate 1: analysis, gated on catalog grounding ─────────────
        analysis = ""
        gate1 = GateResult(False, "not run")
        for attempt in range(1, GATE_MAX_ATTEMPTS + 1):
            step_input = question if attempt == 1 else (
                f"{question}\n\n[Retry {attempt}: previous analysis was rejected — "
                f"{gate1.reason}. Name the specific catalog database(s) you would use.]")
            analysis = self.analysis_chain.invoke(
                {"question": self._apply_fault(step_input)},
                config={**config, "metadata": {"purpose": "query_analysis",
                                               "attempt": attempt,
                                               "gate_failure_reason": None if attempt == 1 else _capped(gate1.reason)}})
            with langfuse_gate("gate-database-selection") as span:
                gate1 = gate_database_selection(analysis)
                span.update(input={"analysis": analysis[:500]},
                            output=gate1.as_output(),
                            metadata={"gate_type": "deterministic", "attempt": attempt,
                                      "max_attempts": GATE_MAX_ATTEMPTS})
            self._log_gate("gate-database-selection", "deterministic", attempt, gate1)
            if gate1.passed:
                break
        if not gate1.passed:                       # routing: ABORT — do not spend MCP + response
            tag_current_trace(["gate:aborted"])
            return ("I couldn't ground this question in the available datasets, so I'm "
                    "stopping rather than guessing. Try naming a topic covered by the "
                    "public catalog (e.g. UK property, NYC taxi, GitHub, Hacker News).")

        # ── Step 2: retrieve context (unchanged) ──────────────────────────────
        context = self.retrieve_context(question, analysis)

        # ── Step 3 + Gate 2: response, gated on SQL policy + grounding ────────
        # Bind the same callbacks to the grounding grader so its Haiku call is
        # emitted and — because it runs while the gate span is current — nests as
        # a child generation under `gate-response-quality` (prompt-linked).
        grader_chain = (self.gate_grounding_chain.with_config({"callbacks": callbacks})
                        if callbacks else self.gate_grounding_chain)
        answer = ""
        gate2 = GateResult(False, "not run")
        for attempt in range(1, GATE_MAX_ATTEMPTS + 1):
            answer = self.response_chain.invoke(
                {"question": self._apply_fault(question, step="response"),
                 "analysis": analysis,
                 "context": context if attempt == 1 else (
                     f"{context}\n\n[Retry {attempt}: previous response was rejected — "
                     f"{gate2.reason}. Fix this; keep any SQL read-only with a LIMIT, "
                     f"and never present numbers as executed query results.]")},
                config={**config, "metadata": {"purpose": "response_generation",
                                               "attempt": attempt,
                                               "gate_failure_reason": None if attempt == 1 else _capped(gate2.reason)}})
            with langfuse_gate("gate-response-quality") as span:
                gate2 = gate_response_quality(question, analysis, context, answer,
                                              grader_chain)
                span.update(output=gate2.as_output(),
                            metadata={"gate_type": "hybrid", "attempt": attempt,
                                      "max_attempts": GATE_MAX_ATTEMPTS,
                                      "grader_model": os.getenv("GATE_MODEL", "claude-haiku-4-5")})
            self._log_gate("gate-response-quality", "hybrid", attempt, gate2)
            if gate2.passed:
                return answer
        # routing: ESCALATE — answer exists but is unverified; flag, don't hide
        tag_current_trace(["gate:escalated"])
        with langfuse_gate("gate-escalation") as span:
            span.update(output={"reason": gate2.reason, "attempts": GATE_MAX_ATTEMPTS})
        return f"[Unverified — routed for review: {gate2.reason}]\n\n{answer}"

    @property
    def context(self) -> str:
        """Expose context for groundedness evaluation."""
        return self._context


def create_pipeline(config: Optional[SQLConfig] = None) -> ClickHouseSQLPipeline:
    return ClickHouseSQLPipeline(config)
