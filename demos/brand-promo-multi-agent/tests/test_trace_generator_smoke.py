"""Smoke test for trace generator - validates schema without real Langfuse connection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_mock_langfuse():
    """Create a mock Langfuse client that records calls."""
    lf = MagicMock()

    trace_mock = MagicMock()
    trace_mock.id = "trace-mock-id"
    lf.trace.return_value = trace_mock

    span_mock = MagicMock()
    span_mock.id = "span-mock-id"
    lf.span.return_value = span_mock

    gen_mock = MagicMock()
    gen_mock.id = "gen-mock-id"
    lf.generation.return_value = gen_mock

    score_mock = MagicMock()
    lf.score.return_value = score_mock

    lf.flush.return_value = None
    return lf


def test_promo_planner_trace_schema():
    """generate_promo_planner_trace produces expected keys."""
    import random

    from src.synthetic.trace_generator import generate_promo_planner_trace

    lf = _make_mock_langfuse()
    rng = random.Random(42)

    result = generate_promo_planner_trace(lf, rng, days_back=30, business_hours=True)

    assert "trace_id" in result
    assert "failure_mode" in result
    assert "start_time" in result
    assert "agent" in result
    assert result["agent"] == "PromoPlanner"

    # Verify trace was called
    assert lf.trace.call_count == 1
    trace_call = lf.trace.call_args
    assert trace_call.kwargs["name"] == "promo_planner_run"
    assert "query" in trace_call.kwargs["input"]

    # Verify key span names were created
    span_names = [call.kwargs.get("name", "") for call in lf.span.call_args_list]
    gen_names = [call.kwargs.get("name", "") for call in lf.generation.call_args_list]

    assert "research_crew" in span_names
    assert "strategy_crew" in span_names
    assert "compliance_agent" in span_names
    assert "data_analyst" in span_names
    assert "market_researcher" in span_names
    assert "historian" in span_names

    assert "classify_intent" in gen_names
    assert "generation.compose_brief" in gen_names


def test_simple_agent_trace_schema():
    """generate_simple_agent_trace produces expected keys for each fleet agent."""
    import random

    from src.synthetic.trace_generator import generate_simple_agent_trace

    agents = ["CustomerCareBot", "SupplyChainPlanner", "ShelfImageAnalyzer", "FinanceCloseBot"]

    for agent_name in agents:
        lf = _make_mock_langfuse()
        rng = random.Random(99)

        result = generate_simple_agent_trace(
            lf, agent_name, "claude-sonnet-4-6", rng, days_back=30, business_hours=True
        )

        assert "trace_id" in result
        assert result["agent"] == agent_name
        assert lf.trace.call_count == 1
        assert lf.generation.call_count >= 1


def test_generate_100_traces_schema():
    """generate_traces produces 100 results with correct agent distribution."""
    from src.config import load_config

    cfg = load_config()

    results = []

    lf = _make_mock_langfuse()

    # Patch Langfuse constructor to return mock
    with patch("langfuse.Langfuse", return_value=lf):
        from src.synthetic.trace_generator import generate_traces

        results = generate_traces(
            total=100,
            hero_share=cfg.synthetic_history.hero_agent_share,
            days_back=cfg.synthetic_history.days_back,
            business_hours=cfg.synthetic_history.business_hours_weighting,
            seed=42,
        )

    assert len(results) == 100

    hero_results = [r for r in results if r.get("agent") == "PromoPlanner"]
    expected_hero = int(100 * cfg.synthetic_history.hero_agent_share)
    # Allow 5% tolerance
    assert abs(len(hero_results) - expected_hero) <= 5

    for r in results:
        assert "trace_id" in r or "error" in r
        assert "agent" in r


def test_failure_modes_present_in_results():
    """Failure modes appear with expected rough frequency over large sample."""
    from src.config import load_config

    load_config()

    with patch("langfuse.Langfuse", return_value=_make_mock_langfuse()):
        from src.synthetic.trace_generator import generate_traces

        results = generate_traces(total=500, hero_share=1.0, days_back=30, business_hours=False, seed=1)

    failure_modes = [r.get("failure_mode") for r in results if r.get("failure_mode") and r["failure_mode"] != "none"]
    # At 20% total failure rate, expect at least some
    assert len(failure_modes) > 0
