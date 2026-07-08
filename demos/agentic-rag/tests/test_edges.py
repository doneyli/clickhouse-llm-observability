"""Pure edge-logic tests for the CRAG graph (no LLM, no DB, no mocking needed)."""

from langgraph.graph import END

import graph
from conftest import FakeStore


def _agent():
    return graph.AgenticRAG(store=FakeStore())


def test_route_edge_dispatch():
    a = _agent()
    assert a._route_edge({"route": "kb"}) == "retrieve"
    assert a._route_edge({"route": "sql"}) == "sql_tool"
    assert a._route_edge({"route": "direct"}) == "generate"


def test_grade_edge_relevant_goes_to_generate():
    a = _agent()
    assert a._grade_edge({"relevant": True, "retrieve_attempts": 1}) == "generate"


def test_grade_edge_not_relevant_rewrites_until_cap():
    a = _agent()
    # below the cap -> rewrite (self-correct)
    assert a._grade_edge({"relevant": False, "retrieve_attempts": 1}) == "rewrite"
    # at the cap -> give up correcting, answer anyway (no infinite loop)
    assert a._grade_edge(
        {"relevant": False, "retrieve_attempts": graph.MAX_RETRIEVE_ATTEMPTS}
    ) == "generate"


def test_reflect_edge_grounded_ends():
    a = _agent()
    assert a._reflect_edge({"grounded": True, "reflect_attempts": 1}) == END


def test_reflect_edge_allows_exactly_one_regeneration():
    a = _agent()
    # 1st reflect, ungrounded -> regenerate
    assert a._reflect_edge({"grounded": False, "reflect_attempts": 1}) == "generate"
    # 2nd reflect, still ungrounded -> END (bounded; guards the off-by-one)
    assert a._reflect_edge({"grounded": False, "reflect_attempts": 2}) == END
