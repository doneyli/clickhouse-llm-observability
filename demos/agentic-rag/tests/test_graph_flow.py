"""End-to-end CRAG loop tests with the LLM, embeddings and store mocked.

Exercises every path through the graph deterministically and asserts the loop
always terminates (no infinite rewrite/regenerate loops).
"""

import graph
from conftest import FakeStore, make_ask


def _run(monkeypatch, ask, store=None):
    monkeypatch.setattr(graph, "embed_query", lambda q: [0.0] * 384)
    monkeypatch.setattr(graph, "_ask", ask)
    agent = graph.AgenticRAG(store=store or FakeStore())
    return agent.graph.invoke({"question": "What is ClickHouse?"})


def test_happy_path_kb_single_pass(monkeypatch):
    out = _run(monkeypatch, make_ask(route="kb", grades=("yes",), reflect=("yes",)))
    assert out["route"] == "kb"
    assert out["retrieve_attempts"] == 1
    assert out["grounded"] is True
    assert out["answer"]


def test_self_correction_reretrieves_once(monkeypatch):
    # first retrieval graded not relevant -> rewrite -> retrieve again -> relevant
    out = _run(monkeypatch, make_ask(route="kb", grades=("no", "yes"), reflect=("yes",)))
    assert out["retrieve_attempts"] == 2
    assert any("rewrite" in s for s in out["trace"])
    assert out["answer"]


def test_grade_cap_terminates_when_always_irrelevant(monkeypatch):
    # grade always 'no' must NOT loop forever; capped at MAX_RETRIEVE_ATTEMPTS
    out = _run(monkeypatch, make_ask(route="kb", grades=("no",), reflect=("yes",)))
    assert out["retrieve_attempts"] == graph.MAX_RETRIEVE_ATTEMPTS
    assert out["answer"]


def test_direct_route_skips_retrieval(monkeypatch):
    out = _run(monkeypatch, make_ask(route="direct"))
    assert out["route"] == "direct"
    assert out.get("retrieve_attempts", 0) == 0
    assert out["grounded"] is True  # no context -> reflect skipped
    assert out["answer"]


def test_sql_route_uses_tool(monkeypatch):
    monkeypatch.setattr(graph, "run_select", lambda sql, max_rows=20: "n\n42")
    out = _run(monkeypatch, make_ask(route="sql", reflect=("yes",)))
    assert out["route"] == "sql"
    assert "42" in out["sql_result"]
    assert out["answer"]


def test_regeneration_when_ungrounded_then_terminates(monkeypatch):
    # reflect 'no' then 'yes': should regenerate exactly once and finish
    out = _run(monkeypatch, make_ask(route="kb", grades=("yes",), reflect=("no", "yes")))
    assert out["reflect_attempts"] == 2
    assert out["grounded"] is True
    assert out["answer"]


def test_regeneration_capped_when_persistently_ungrounded(monkeypatch):
    # reflect always 'no' must still terminate (bounded regeneration)
    out = _run(monkeypatch, make_ask(route="kb", grades=("yes",), reflect=("no",)))
    assert out["reflect_attempts"] == graph.MAX_REFLECT_ATTEMPTS + 1
    assert out["grounded"] is False
    assert out["answer"]
