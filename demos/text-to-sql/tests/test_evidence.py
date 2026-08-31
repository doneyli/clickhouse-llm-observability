"""Unit tests for the evidence runner (ClickHouse client fully mocked)."""

import sql_evidence
from conftest import FakeClient


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(sql_evidence, "_get_client", lambda: client)


def test_non_select_rejected_without_touching_client(monkeypatch):
    # If the client is called, this blows up — proving non-SELECT never executes.
    def _boom():
        raise AssertionError("client must not be used for non-SELECT")
    monkeypatch.setattr(sql_evidence, "_get_client", _boom)
    ev = sql_evidence.gather_evidence("DELETE FROM nyc_taxi.trips")
    assert ev.checks["read_only"] is False
    assert "only SELECT/WITH permitted" in ev.error


def test_has_limit_detection(monkeypatch):
    _patch_client(monkeypatch, FakeClient())
    with_limit = sql_evidence.gather_evidence("SELECT count() FROM uk.uk_price_paid LIMIT 1")
    assert with_limit.checks["has_limit"] is True
    without = sql_evidence.gather_evidence("SELECT count() FROM uk.uk_price_paid")
    assert without.checks["has_limit"] is False
    # word-boundary detection: trailing/newline LIMIT still counts
    newline_limit = sql_evidence.gather_evidence("SELECT town FROM uk.uk_price_paid\nLIMIT 10")
    assert newline_limit.checks["has_limit"] is True


def test_explain_error_stops_before_execution(monkeypatch):
    _patch_client(monkeypatch, FakeClient(explain_error="UNKNOWN_IDENTIFIER 'price_gbp'"))
    ev = sql_evidence.gather_evidence("SELECT price_gbp FROM uk.uk_price_paid LIMIT 1")
    assert ev.checks["explain_ok"] is False
    assert "exec_ok" not in ev.checks           # never executed
    assert "price_gbp" in ev.error


def test_successful_execution_nonempty(monkeypatch):
    _patch_client(monkeypatch, FakeClient(exec_rows=[[28734000]], cols=["count()"]))
    ev = sql_evidence.gather_evidence("SELECT count() FROM uk.uk_price_paid LIMIT 1")
    assert ev.checks["explain_ok"] is True
    assert ev.checks["exec_ok"] is True
    assert ev.checks["nonempty_result"] is True
    assert ev.row_count == 1
    assert "count()" in ev.rows_preview


def test_empty_result_flags_nonempty_false(monkeypatch):
    _patch_client(monkeypatch, FakeClient(exec_rows=[]))
    ev = sql_evidence.gather_evidence("SELECT town FROM uk.uk_price_paid WHERE 1=0 LIMIT 10")
    assert ev.checks["exec_ok"] is True
    assert ev.checks["nonempty_result"] is False


def test_execution_error_recorded(monkeypatch):
    _patch_client(monkeypatch, FakeClient(exec_error="TIMEOUT_EXCEEDED"))
    ev = sql_evidence.gather_evidence("SELECT count() FROM a, b LIMIT 1")
    assert ev.checks["explain_ok"] is True
    assert ev.checks["exec_ok"] is False
    assert "TIMEOUT_EXCEEDED" in ev.error


def test_as_text_includes_checks_and_error(monkeypatch):
    _patch_client(monkeypatch, FakeClient(explain_error="boom"))
    ev = sql_evidence.gather_evidence("SELECT 1 LIMIT 1")
    text = ev.as_text()
    assert "checks:" in text
    assert "boom" in text
