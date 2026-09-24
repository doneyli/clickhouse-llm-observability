"""
Managed prompts — the Deploy node of the loop.

Two prompts live in Langfuse Prompt Management (seeded idempotently by
scripts/seed_prompts.py), each with a hard-coded local fallback so the agent
runs on a fresh clone / with Langfuse down:

  query-tuner-system   v1-naive          — no plateau/give-up doctrine (the
                                            Experiment's losing arm + runaway fuel)
                       v2-disciplined     — adds re-measure + plateau give-up
                       (label `production` == v2-disciplined)
  query-tuner-goal     v1                 — per-run user message ({{sql}},
                                            {{target_ms}}, {{schema_hint}}, {{baseline_ms}})

The running app fetches by LABEL and links the fetched version to the plan
generations, so promoting a label is a deploy with no code change.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import langfuse_config as lf

SYSTEM_PROMPT_NAME = "query-tuner-system"
GOAL_PROMPT_NAME = "query-tuner-goal"

PRODUCTION_LABEL = "production"       # -> v2-disciplined
V1_LABEL = "v1-naive"
V2_LABEL = "v2-disciplined"

# --- shared tool doctrine (both system versions) -----------------------------
_COMMON = (
    "You are a ClickHouse query-tuning agent. You are given a slow SQL query and a target "
    "latency in milliseconds. Your job: produce a query that returns the SAME result set and "
    "meets the target — or conclude it cannot be met with query rewrites alone.\n"
    "\n"
    "You act on a LIVE ClickHouse instance through tools. Doctrine:\n"
    "- NEVER claim a speedup you have not measured. Run every candidate with run_query and read "
    "the actual elapsed_ms — the database is the ground truth, not your reasoning.\n"
    "- Every run_query candidate is auto-checked for result-set equivalence; verify equivalence "
    "before you call finish. A query that got fast by returning different rows is WRONG.\n"
    "- The table has ORDER BY tuple() (no sort key). Common wins: use the properly-typed "
    "event_date instead of parsing ts_raw, PREWHERE on selective predicates, drop needless "
    "SELECT * subqueries / global ORDER BY, and swap count(DISTINCT x) for uniq(x).\n"
    "- Call finish(status, final_sql, claimed_speedup, summary) when you are done. Your claim "
    "is independently re-executed by the controller and rejected if it does not verify.\n"
)

# --- v1-naive: no self-discipline -> keeps trying forever on hard queries ----
V1_NAIVE = _COMMON + (
    "\nKeep trying rewrites until the target is met.\n"
)

# --- v2-disciplined (production): re-measure + plateau give-up ---------------
V2_DISCIPLINED = _COMMON + (
    "\nDiscipline (this is what makes stopping GOOD):\n"
    "- Before calling finish(status=success), re-run the final query at least twice to confirm "
    "the timing is stable, and confirm equivalence.\n"
    "- If improvement_delta is ~0 for 3 consecutive turns you are plateaued: either propose_ddl "
    "with a concrete schema change and rationale, or call finish(status=gave_up) naming the true "
    "structural blocker. Do NOT thrash on rewrites that don't help.\n"
    "- Prefer the fewest changes that reach the target; stop as soon as it is met.\n"
)

# --- goal template (user message) --------------------------------------------
GOAL_TEMPLATE = (
    "Optimize this query to run in <= {{target_ms}} ms while returning the identical result set.\n"
    "\n"
    "Original query:\n{{sql}}\n"
    "\n"
    "Measured baseline: {{baseline_ms}} ms.\n"
    "\n"
    "{{schema_hint}}\n"
    "\n"
    "Investigate with the tools, then finish when the target is verifiably met or you can prove "
    "it needs a schema change."
)


def _local_compile(text: str, **vars: Any) -> str:
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _managed_or_fallback(name: str, label: str, fallback: str,
                         **vars: Any) -> Tuple[str, Optional[Any]]:
    """Return (compiled_text, prompt_obj_or_None). The obj (when non-None) is
    passed to the plan generation so the version links to the trace."""
    obj = lf.get_prompt(name, label=label)
    if obj is not None:
        try:
            return obj.compile(**vars), obj
        except Exception:
            pass
    return _local_compile(fallback, **vars), None


_SYSTEM_FALLBACK = {
    V1_LABEL: V1_NAIVE,
    V2_LABEL: V2_DISCIPLINED,
    PRODUCTION_LABEL: V2_DISCIPLINED,
}


def get_system_prompt(version: str = PRODUCTION_LABEL) -> Tuple[str, Optional[Any]]:
    """Fetch the system prompt for a version/label. `version` is one of
    'production', 'v2-disciplined', 'v1-naive'."""
    fallback = _SYSTEM_FALLBACK.get(version, V2_DISCIPLINED)
    return _managed_or_fallback(SYSTEM_PROMPT_NAME, version, fallback)


def get_goal_prompt(sql: str, target_ms: int, schema_hint: str,
                    baseline_ms) -> Tuple[str, Optional[Any]]:
    return _managed_or_fallback(
        GOAL_PROMPT_NAME, "production", GOAL_TEMPLATE,
        sql=sql.strip(), target_ms=target_ms, schema_hint=schema_hint,
        baseline_ms=("unknown" if baseline_ms is None else round(baseline_ms, 1)),
    )
