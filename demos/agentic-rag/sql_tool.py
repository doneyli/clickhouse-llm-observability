"""
SQL tool for the Agentic RAG agent.

When the router decides a question is better answered by data than by the
document KB, the agent generates a SELECT against ClickHouse's public demo
datasets (sql.clickhouse.com) and executes it. This is the "tool use" branch of
the agentic loop and demonstrates multi-source retrieval (vectors + live SQL).
"""

import os
from typing import Optional

import clickhouse_connect

# ClickHouse public demo endpoint (read-only `demo` user, no password).
SQL_HOST = os.getenv("PUBLIC_CH_HOST", "sql-clickhouse.clickhouse.com")
SQL_PORT = int(os.getenv("PUBLIC_CH_PORT", "443"))
SQL_USER = os.getenv("PUBLIC_CH_USER", "demo")
SQL_PASSWORD = os.getenv("PUBLIC_CH_PASSWORD", "")


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = clickhouse_connect.get_client(
            host=SQL_HOST,
            port=SQL_PORT,
            username=SQL_USER,
            password=SQL_PASSWORD,
            secure=True,
        )
    return _client


def run_select(sql: str, max_rows: int = 20) -> str:
    """Execute a read-only SELECT and return a compact text table.

    Defensive: rejects non-SELECT statements and caps rows. Returns an error
    string (rather than raising) so the agent can reflect and recover.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped.lower().startswith(("select", "with")):
        return "ERROR: only SELECT/WITH queries are permitted by the SQL tool."

    try:
        result = _get_client().query(stripped, settings={"max_result_rows": max_rows})
    except Exception as e:
        return f"ERROR executing query: {e}"

    cols = result.column_names
    rows = result.result_rows[:max_rows]
    if not rows:
        return "Query returned no rows."

    header = " | ".join(cols)
    body = "\n".join(" | ".join(str(c) for c in row) for row in rows)
    return f"{header}\n{body}"
