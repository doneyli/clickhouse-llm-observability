"""
ClickHouse candidate validator + read-only executor for the SQL voting stage.

ClickHouse is the vote counter: candidate SQL is arbitrated by *executing* it
against the public playground (``sql-clickhouse.clickhouse.com``) and hashing the
result set — semantic equivalence decided by the database, not by string match.

Safety posture mirrors ``demos/agentic-rag/sql_tool.py``:
- SELECT/WITH only (statement whitelist + destructive-keyword reject).
- LIMIT enforcement (auto-append ``LIMIT 100`` when absent).
- HTTPS ``clickhouse-connect`` to ``PUBLIC_CH_HOST`` as the read-only ``demo`` user.
- Short timeout so a slow candidate fails fast rather than stalling the vote.

Each ``explain_ok`` call is wrapped in a Langfuse ``tool`` observation named
``explain-candidate`` (low cardinality — the candidate index is not in the name).
``clickhouse_connect`` is imported lazily so this module imports without the
package (pure-logic helpers stay unit-testable offline).
"""

import os
import re
from typing import List, Optional, Tuple

import langfuse_config as lf

PUBLIC_CH_HOST = os.getenv("PUBLIC_CH_HOST", "sql-clickhouse.clickhouse.com")
PUBLIC_CH_PORT = int(os.getenv("PUBLIC_CH_PORT", "443"))
PUBLIC_CH_USER = os.getenv("PUBLIC_CH_USER", "demo")
PUBLIC_CH_PASSWORD = os.getenv("PUBLIC_CH_PASSWORD", "")
QUERY_TIMEOUT_S = int(os.getenv("CH_QUERY_TIMEOUT", "10"))
MAX_ROWS = int(os.getenv("CH_MAX_ROWS", "100"))

_DESTRUCTIVE = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|GRANT|REVOKE|CREATE|RENAME|DETACH|ATTACH|OPTIMIZE|SET)\b",
    re.IGNORECASE,
)

_client = None


def _get_client():
    """Lazily construct a read-only clickhouse-connect client to the playground."""
    global _client
    if _client is None:
        import clickhouse_connect
        _client = clickhouse_connect.get_client(
            host=PUBLIC_CH_HOST,
            port=PUBLIC_CH_PORT,
            username=PUBLIC_CH_USER,
            password=PUBLIC_CH_PASSWORD,
            secure=True,
            connect_timeout=QUERY_TIMEOUT_S,
            send_receive_timeout=QUERY_TIMEOUT_S,
        )
    return _client


def normalize(sql: str) -> str:
    """Strip trailing semicolons / whitespace from a candidate statement."""
    return (sql or "").strip().rstrip(";").strip()


def is_safe_select(sql: str) -> Tuple[bool, str]:
    """Pure guard: SELECT/WITH only, no destructive keywords, single statement.

    No I/O — unit-testable offline. Returns ``(ok, reason)``.
    """
    s = normalize(sql)
    if not s:
        return False, "empty statement"
    if ";" in s:
        return False, "multiple statements are not allowed"
    if not s.lower().startswith(("select", "with")):
        return False, "only SELECT/WITH queries are permitted"
    if _DESTRUCTIVE.search(s):
        return False, "destructive keyword detected"
    return True, "ok"


def enforce_limit(sql: str, limit: int = MAX_ROWS) -> str:
    """Append a ``LIMIT`` when the statement has none (defence in depth)."""
    s = normalize(sql)
    if re.search(r"\blimit\s+\d+", s, re.IGNORECASE):
        return s
    return f"{s} LIMIT {limit}"


def explain_ok(sql: str) -> bool:
    """Validate a candidate by running ``EXPLAIN`` against the playground.

    Wrapped in a Langfuse ``tool`` observation (``explain-candidate``). Invalid
    candidates mark the observation ``level=WARNING`` so failed samples are
    visible in the trace. Returns True iff the statement is safe AND ClickHouse
    can plan it.
    """
    with lf.observe("explain-candidate", as_type="tool", input=sql) as obs:
        safe, reason = is_safe_select(sql)
        if not safe:
            obs.update(level="WARNING", status_message=f"rejected: {reason}",
                       output={"valid": False, "reason": reason})
            return False
        try:
            _get_client().query(f"EXPLAIN {enforce_limit(sql)}",
                                settings={"max_execution_time": QUERY_TIMEOUT_S})
            obs.update(output={"valid": True})
            return True
        except Exception as e:  # invalid SQL / unknown table / timeout
            obs.update(level="WARNING", status_message=f"EXPLAIN failed: {e}",
                       output={"valid": False, "reason": str(e)[:200]})
            return False


def execute_readonly(sql: str) -> Optional[List[tuple]]:
    """Execute a validated candidate read-only and return its rows.

    Returns a list of row tuples (possibly empty) or None on error. SELECT-only +
    LIMIT enforced. Used by the result-signature strategy to let ClickHouse
    arbitrate semantic equivalence.
    """
    safe, _ = is_safe_select(sql)
    if not safe:
        return None
    try:
        result = _get_client().query(
            enforce_limit(sql),
            settings={"max_result_rows": MAX_ROWS, "max_execution_time": QUERY_TIMEOUT_S},
        )
        return [tuple(row) for row in result.result_rows]
    except Exception:
        return None
