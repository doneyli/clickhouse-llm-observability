"""
Read-only ClickHouse client for the Cluster Health Investigator.

Same guarantees as demos/agentic-rag/sql_tool.py, tightened for this demo:
- accepts a SINGLE SELECT / WITH statement only (rejects everything else),
- forces a LIMIT (appends `LIMIT 50` if none is present),
- caps execution time (`max_execution_time`) and result rows server-side.

The investigation target is env-swappable via TARGET_CH_* — it defaults to the
stack's own `langfuse-clickhouse` (ClickHouse diagnosing the ClickHouse that
stores the traces of the diagnosis), but an SA can point it at a customer's
cluster or ClickHouse Cloud for a PoC.

The safety checks (`is_read_only`, `ensure_limit`) are pure functions so they
can be unit-tested without a live server (tests/test_catalog_safety.py).
"""

from __future__ import annotations

import os
import re
from typing import Optional

TARGET_HOST = os.getenv("TARGET_CH_HOST", "langfuse-clickhouse")
TARGET_PORT = int(os.getenv("TARGET_CH_PORT", "8123"))
TARGET_USER = os.getenv("TARGET_CH_USER", "langfuse")
TARGET_PASSWORD = os.getenv("TARGET_CH_PASSWORD", "langfuse123")
TARGET_SECURE = os.getenv("TARGET_CH_SECURE", "false").lower() in ("1", "true", "yes")

MAX_EXECUTION_TIME = int(os.getenv("TARGET_CH_MAX_EXECUTION_TIME", "10"))
DEFAULT_LIMIT = int(os.getenv("TARGET_CH_DEFAULT_LIMIT", "50"))

# One leading SQL statement, SELECT or WITH only.
_SELECT_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)


def _strip(sql: str) -> str:
    """Drop `-- ...` comment lines and trailing semicolons for validation."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";").strip()


def is_read_only(sql: str) -> bool:
    """True iff the statement is a single read-only SELECT/WITH query.

    Rejects multi-statement input (a `;` mid-body) and any non-SELECT verb
    (INSERT/ALTER/DROP/TRUNCATE/OPTIMIZE/SYSTEM/...).
    """
    body = _strip(sql)
    if not body:
        return False
    if ";" in body:  # no multi-statement batches
        return False
    return bool(_SELECT_RE.match(body))


def ensure_limit(sql: str, default_limit: int = DEFAULT_LIMIT) -> str:
    """Return the query with a LIMIT guaranteed (appends one if absent)."""
    body = sql.rstrip().rstrip(";").rstrip()
    if _LIMIT_RE.search(_strip(body)):
        return body
    return f"{body}\nLIMIT {default_limit}"


class ReadOnlyClickHouse:
    """Thin, defensive wrapper around clickhouse_connect (lazy-imported)."""

    def __init__(self):
        self._client = None

    def _get(self):
        if self._client is None:
            import clickhouse_connect  # lazy: keeps module import light for tests

            self._client = clickhouse_connect.get_client(
                host=TARGET_HOST,
                port=TARGET_PORT,
                username=TARGET_USER,
                password=TARGET_PASSWORD,
                secure=TARGET_SECURE,
            )
        return self._client

    def select(self, sql: str, max_rows: int = DEFAULT_LIMIT) -> list[dict]:
        """Execute a validated read-only SELECT; return rows as list[dict].

        Raises ValueError on a non-read-only statement (there is no code path
        that runs planner-authored or mutating SQL). Query/connection errors are
        surfaced as a single-row `{"error": "..."}` so a worker can still write a
        finding and the run does not crash on one bad analysis.
        """
        if not is_read_only(sql):
            raise ValueError("ch_client.select accepts a single SELECT/WITH query only")

        safe_sql = ensure_limit(sql, max_rows)
        try:
            result = self._get().query(
                safe_sql,
                settings={
                    "max_execution_time": MAX_EXECUTION_TIME,
                    "max_result_rows": max_rows,
                    "result_overflow_mode": "break",
                    "readonly": 1,
                },
            )
        except Exception as e:  # pragma: no cover - needs a live server
            return [{"error": f"{type(e).__name__}: {e}"}]

        cols = result.column_names
        return [dict(zip(cols, row)) for row in result.result_rows[:max_rows]]


def format_rows(rows: list[dict], max_chars: int = 2000) -> str:
    """Compact text table for feeding a worker LLM (bounded)."""
    if not rows:
        return "(no rows)"
    if len(rows) == 1 and "error" in rows[0]:
        return f"ERROR: {rows[0]['error']}"
    cols = list(rows[0].keys())
    header = " | ".join(cols)
    body = "\n".join(" | ".join(str(r.get(c, "")) for c in cols) for r in rows)
    text = f"{header}\n{body}"
    return text[:max_chars]


_default: Optional[ReadOnlyClickHouse] = None


def get_client() -> ReadOnlyClickHouse:
    global _default
    if _default is None:
        _default = ReadOnlyClickHouse()
    return _default


def select(sql: str, max_rows: int = DEFAULT_LIMIT) -> list[dict]:
    """Module-level convenience — the default worker SQL path."""
    return get_client().select(sql, max_rows=max_rows)
