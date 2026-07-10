"""Smoke tests for the synthetic trace generator.

The generator builds Langfuse *batch ingestion* events (not per-trace
lf.trace()/span()/generation() calls): `_build_promo_planner_events` /
`_build_simple_agent_events` return lists of typed IngestionEvent objects, and
`generate_traces` ingests them via `lf.api.ingestion.batch` and returns one
result dict per trace keyed by `agent` (no `trace_id` — ids live inside the
events). These tests validate that event/result shape without a real Langfuse.
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch


def _make_mock_langfuse():
    """Mock Langfuse whose `api.ingestion.batch` reports no errors."""
    lf = MagicMock()
    batch_resp = MagicMock()
    batch_resp.errors = []  # empty -> generate_traces records no ingestion errors
    lf.api.ingestion.batch.return_value = batch_resp
    lf.flush.return_value = None
    return lf


def _by_type(events, event_type):
    return [e for e in events if e.type == event_type]


def test_promo_planner_events_schema():
    """_build_promo_planner_events emits the expected trace/span/generation events."""
    from src.synthetic.trace_generator import _build_promo_planner_events

    events = _build_promo_planner_events(random.Random(42), days_back=30, business_hours=True)

    traces = _by_type(events, "trace-create")
    assert len(traces) == 1
    root = traces[0].body
    assert root.name == "promo_planner_run"
    assert "query" in root.input
    assert "PromoPlanner" in root.tags  # tags are hardcoded ["synthetic", "PromoPlanner"]

    span_names = {e.body.name for e in _by_type(events, "span-create")}
    for expected in (
        "research_crew", "strategy_crew", "compliance_agent",
        "data_analyst", "market_researcher", "historian",
    ):
        assert expected in span_names, f"missing span {expected}"

    gen_names = {e.body.name for e in _by_type(events, "generation-create")}
    assert "classify_intent" in gen_names
    assert "generation.compose_brief" in gen_names


def test_simple_agent_events_schema():
    """_build_simple_agent_events emits a named trace + >=1 generation per fleet agent."""
    from src.synthetic.trace_generator import _build_simple_agent_events

    for agent_name in ("CustomerCareBot", "SupplyChainPlanner", "ShelfImageAnalyzer", "FinanceCloseBot"):
        events = _build_simple_agent_events(
            agent_name, "claude-sonnet-4-6", random.Random(99), days_back=30, business_hours=True
        )
        traces = _by_type(events, "trace-create")
        assert len(traces) == 1
        assert traces[0].body.name == f"{agent_name.lower()}_run"
        assert agent_name in traces[0].body.tags
        assert len(_by_type(events, "generation-create")) >= 1


def test_generate_100_traces_schema():
    """generate_traces returns 100 well-formed results with the right hero distribution."""
    from src.config import load_config

    cfg = load_config()

    with patch("langfuse.Langfuse", return_value=_make_mock_langfuse()):
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
    assert abs(len(hero_results) - expected_hero) <= 5
    for r in results:
        assert "agent" in r  # each result records its agent (plus failure_mode or error)


def test_failure_modes_present_in_results():
    """PromoPlanner results carry a populated failure_mode field over a large sample."""
    from src.config import load_config

    load_config()

    with patch("langfuse.Langfuse", return_value=_make_mock_langfuse()):
        from src.synthetic.trace_generator import generate_traces

        results = generate_traces(total=500, hero_share=1.0, days_back=30, business_hours=False, seed=1)

    failure_modes = [
        r.get("failure_mode") for r in results
        if r.get("failure_mode") and r["failure_mode"] != "none"
    ]
    assert len(failure_modes) > 0
