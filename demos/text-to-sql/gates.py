"""Gate checks for the text-to-sql chain (Pattern: prompt chaining with gates).

Gate 1 is deterministic (catalog membership). Gate 2 is hybrid:
a deterministic SQL-policy check (fail-closed) followed by an
LLM-graded grounding check (Haiku, temp 0; fail-open on parse error).

Pure functions returning a ``GateResult`` — no Langfuse code here, so the
instrumentation stays in the pipeline/config layer (matches the repo's
separation of concerns and keeps these unit-testable with no external services).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict

# Mirrors evaluators/sql-safety-guard.ts:DESTRUCTIVE — keep in sync by hand.
DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|GRANT|REVOKE|"
    r"CREATE\s+(?!TEMPORARY)|RENAME|DETACH|ATTACH)\b", re.IGNORECASE)

# The 24 database names from CLICKHOUSE_DATABASES in sql_pipeline.py.
CATALOG_DATABASES = frozenset({
    "amazon", "bluesky", "covid", "dns", "environmental", "forex", "geo",
    "git", "github", "hackernews", "imdb", "logs", "mta", "noaa", "nyc_taxi",
    "nypd", "ontime", "pypi", "stackoverflow", "stock", "twitter", "uk",
    "wiki", "youtube",
})


@dataclass
class GateResult:
    passed: bool
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def as_output(self) -> Dict[str, Any]:
        """Shape written to the gate span's output — the code evaluator
        (evaluators/chain-gate-check.ts) reads ``verdict`` from here."""
        return {"verdict": "pass" if self.passed else "fail",
                "reason": self.reason, **self.details}


def gate_database_selection(analysis: str) -> GateResult:
    """Gate 1 — DETERMINISTIC. The analysis must name >=1 catalog database,
    otherwise the response step composes an ungrounded answer."""
    found = sorted(
        db for db in CATALOG_DATABASES
        if re.search(rf"\b{re.escape(db)}\b", analysis or "", re.IGNORECASE))
    if found:
        return GateResult(True, f"Analysis names catalog database(s): {found}",
                          {"databases": found})
    return GateResult(
        False,
        "Analysis names no database from the sql.clickhouse.com catalog — "
        "ungrounded; downstream steps would compound the error.",
        {"databases": []})


def gate_response_quality(question: str, analysis: str, context: str,
                          response: str, grader_chain) -> GateResult:
    """Gate 2 — HYBRID. (a) deterministic SQL policy, fail-closed;
    (b) LLM-graded grounding verdict, fail-open on parse error."""
    # (a) cheap check first: never return destructive SQL
    hits = DESTRUCTIVE_SQL.findall(response or "")
    if hits:
        return GateResult(
            False,
            f"SQL policy: destructive keyword(s) {sorted(set(h.strip().upper() for h in hits))}",
            {"check": "sql-policy"})
    # (b) LLM grade: grounding / no fabricated results
    raw = grader_chain.invoke({"question": question, "analysis": analysis,
                               "context": context, "response": response})
    try:
        verdict = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
        passed = str(verdict.get("verdict", "")).lower() == "pass"
        return GateResult(passed, verdict.get("reason", "(no reason given)"),
                          {"check": "llm-grounding"})
    except (json.JSONDecodeError, AttributeError) as e:
        # Fail OPEN on grader parse errors — gate infra never takes the app down.
        return GateResult(True, f"Grader output unparseable ({e}); failing open.",
                          {"check": "llm-grounding", "verdict_source": "parse-error"})
