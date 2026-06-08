"""Regression tests for the hang fix and the SQL/vector helpers (issue #15)."""

import graph
from clickhouse_store import ClickHouseVectorStore
from sql_tool import run_select


def test_llm_has_finite_timeout_and_retries():
    """Guards the hang fix: a stalled LLM call must be bounded, not infinite."""
    llm = graph._llm()
    timeout = getattr(llm, "default_request_timeout", None)
    assert timeout is not None and timeout > 0, "ChatAnthropic must set a request timeout"
    assert getattr(llm, "max_retries", 0) >= 1


def test_vec_literal_formatting():
    # static method -> no DB connection needed
    assert ClickHouseVectorStore._vec_literal([0.1, 0.2]) == "[0.10000000,0.20000000]"


def test_sql_tool_rejects_non_select():
    # guard returns before any DB connection (fully offline)
    assert run_select("DROP TABLE x").startswith("ERROR")
    assert run_select("UPDATE t SET a=1").startswith("ERROR")
    assert run_select("delete from t").startswith("ERROR")
