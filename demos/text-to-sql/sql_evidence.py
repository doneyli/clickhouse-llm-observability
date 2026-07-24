"""Evidence runner — grounds the SQL critic in real ClickHouse behaviour.

The critic may only judge from this evidence (EXPLAIN + bounded execution),
never from the SQL text alone: that is the anti-collusion guardrail that
distinguishes a real evaluator-optimizer loop from generator/critic collusion.

Modeled on ``demos/agentic-rag/sql_tool.py`` (read-only ``demo`` user against the
public playground at ``sql.clickhouse.com``). Chosen over the demo's own
``mcp_client.execute_query()`` because the loop needs deterministic, low-latency
round-trips; the MCP catalog step is left untouched.

Everything is defensive: connection/query failures are captured as evidence
(``error`` + a failed check) rather than raised, so the loop can critique and
refine instead of crashing.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

# clickhouse_connect is only needed when a real round-trip happens; import lazily
# so unit tests (which mock the client) and no-network environments still import
# this module.
try:  # pragma: no cover - trivial import guard
    import clickhouse_connect
except Exception:  # pragma: no cover - defensive
    clickhouse_connect = None

# ClickHouse public demo endpoint (read-only `demo` user, no password).
SQL_HOST = os.getenv("PUBLIC_CH_HOST", "sql-clickhouse.clickhouse.com")
SQL_PORT = int(os.getenv("PUBLIC_CH_PORT", "443"))
SQL_USER = os.getenv("PUBLIC_CH_USER", "demo")
SQL_PASSWORD = os.getenv("PUBLIC_CH_PASSWORD", "")

# Word-boundary LIMIT check — more robust than a literal " limit " substring
# (matches a trailing/newline-preceded LIMIT too) while still failing when the
# keyword is genuinely absent.
_LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)

_client = None


def _get_client():
    global _client
    if _client is None:
        if clickhouse_connect is None:  # pragma: no cover - defensive
            raise RuntimeError("clickhouse_connect not installed")
        _client = clickhouse_connect.get_client(
            host=SQL_HOST,
            port=SQL_PORT,
            username=SQL_USER,
            password=SQL_PASSWORD,
            secure=True,
        )
    return _client


@dataclass
class Evidence:
    """The critic's only permitted grounds for judgement.

    ``checks`` keys (all deterministic / evidence-derived):
        read_only       — statement is a SELECT/WITH
        has_limit       — a LIMIT clause is present
        explain_ok      — EXPLAIN succeeded (syntax + identifiers resolve)
        exec_ok         — bounded execution succeeded
        nonempty_result — execution returned at least one row
    """

    checks: Dict[str, bool] = field(default_factory=dict)
    explain_excerpt: str = ""
    error: str = ""
    rows_preview: str = ""  # header + first rows, compact text table
    row_count: int = 0

    def as_text(self) -> str:
        """Rendered block for the critic prompt / observation output."""
        lines = ["EVIDENCE (from real ClickHouse — the critic may only judge from this):"]
        lines.append("checks: " + ", ".join(f"{k}={v}" for k, v in self.checks.items()))
        if self.explain_excerpt:
            lines.append("EXPLAIN plan (excerpt):\n" + self.explain_excerpt)
        if self.error:
            lines.append("ERROR: " + self.error)
        if self.rows_preview:
            lines.append(f"Execution result ({self.row_count} row(s)):\n" + self.rows_preview)
        elif self.checks.get("exec_ok"):
            lines.append("Execution result: 0 rows.")
        return "\n".join(lines)


def _format_rows(result, max_rows: int) -> str:
    """Compact text table: header + first ``max_rows`` rows."""
    cols = list(result.column_names)
    rows = result.result_rows[:max_rows]
    if not rows:
        return ""
    header = " | ".join(str(c) for c in cols)
    body = "\n".join(" | ".join(str(c) for c in row) for row in rows)
    return f"{header}\n{body}"


def gather_evidence(sql: str, max_rows: int = 20) -> Evidence:
    """Gather EXPLAIN + bounded read-only execution evidence for ``sql``.

    Never raises: every failure mode (non-SELECT, EXPLAIN error, execution
    error) is recorded as a failed check + ``error`` so the loop can refine.
    """
    ev = Evidence()
    stripped = (sql or "").strip().rstrip(";").strip()

    ev.checks["read_only"] = stripped.lower().startswith(("select", "with"))
    ev.checks["has_limit"] = bool(_LIMIT_RE.search(stripped))

    if not ev.checks["read_only"]:
        ev.error = "rejected: only SELECT/WITH permitted"
        return ev

    try:
        client = _get_client()
    except Exception as e:  # pragma: no cover - network/dependency guard
        ev.error = f"client unavailable: {e}"[:500]
        ev.checks["explain_ok"] = False
        return ev

    # 1) EXPLAIN — free syntax + plan evidence (UNKNOWN_IDENTIFIER, missing table…).
    try:
        plan = client.query(f"EXPLAIN {stripped}")
        ev.explain_excerpt = "\n".join(str(r[0]) for r in plan.result_rows[:12])
        ev.checks["explain_ok"] = True
    except Exception as e:
        ev.error = str(e)[:500]
        ev.checks["explain_ok"] = False
        return ev

    # 2) bounded real execution — the critic's ground truth.
    try:
        res = client.query(
            stripped,
            settings={"max_result_rows": max_rows, "max_execution_time": 10},
        )
        ev.row_count = len(res.result_rows)
        ev.rows_preview = _format_rows(res, max_rows)
        ev.checks["exec_ok"] = True
        ev.checks["nonempty_result"] = ev.row_count > 0
    except Exception as e:
        ev.error = str(e)[:500]
        ev.checks["exec_ok"] = False

    return ev
