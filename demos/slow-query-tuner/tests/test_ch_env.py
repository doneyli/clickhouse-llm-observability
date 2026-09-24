"""The app-side SQL-shape allow-list blocks writes/DDL/multi-statement for the
agent path, and result signatures are order-independent. Pure-python (no driver,
no DB) — this is defense-in-depth in FRONT of the tuner_agent grants."""

import ch_env


def test_allows_read_only_shapes():
    for sql in [
        "SELECT 1",
        "  select * from tuning_lab.web_events limit 10",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "EXPLAIN SELECT 1",
        "SHOW CREATE TABLE tuning_lab.web_events",
        "SELECT 1;",                       # a single trailing ';' is fine
    ]:
        ok, reason = ch_env.sql_allowed(sql)
        assert ok, f"should allow: {sql!r} ({reason})"


def test_blocks_writes_and_ddl():
    for sql in [
        "INSERT INTO web_events VALUES (1)",
        "ALTER TABLE web_events ADD PROJECTION p (SELECT 1)",
        "CREATE TABLE t (a Int)",
        "DROP TABLE web_events",
        "TRUNCATE TABLE web_events",
        "DELETE FROM web_events WHERE 1",
        "KILL QUERY WHERE 1",
        "OPTIMIZE TABLE web_events",
    ]:
        ok, reason = ch_env.sql_allowed(sql)
        assert not ok, f"should block: {sql!r}"
        assert reason


def test_blocks_multi_statement_injection():
    ok, reason = ch_env.sql_allowed("SELECT 1; DROP TABLE web_events")
    assert not ok
    assert "single statement" in reason


def test_blocks_empty():
    assert ch_env.sql_allowed("")[0] is False
    assert ch_env.sql_allowed("   ")[0] is False


def test_result_signature_is_order_independent():
    a = ch_env.result_signature([(1, "x"), (2, "y"), (3, "z")])
    b = ch_env.result_signature([(3, "z"), (1, "x"), (2, "y")])
    assert a == b


def test_result_signature_detects_difference():
    a = ch_env.result_signature([(1, "x"), (2, "y")])
    b = ch_env.result_signature([(1, "x"), (2, "DIFFERENT")])
    assert a != b


def test_obs_public_shapes():
    ok = ch_env.Obs(ok=True, kind="query", elapsed_ms=123.456, read_rows=5,
                    signature="abc", rows_preview=[(1, 2)])
    pub = ok.public
    assert pub["kind"] == "query" and pub["elapsed_ms"] == 123.5
    assert pub["result_signature"] == "abc"

    err = ch_env.Obs.error_obs("MEMORY_LIMIT_EXCEEDED", kind="query")
    assert err.is_error and err.public["error"].startswith("MEMORY")
