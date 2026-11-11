"""Check the user-facing trace and preserve complete promotion-planning context."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from crewai.crews.crew_output import CrewOutput
from crewai.tasks.task_output import TaskOutput
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.agents.crew_output import format_crew_output
from src.langfuse_callbacks import PromoPlannerCallbackHandler


def test_planner_trace_has_use_case_name_and_user_facing_io(monkeypatch):
    from src.agents import orchestrator

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    public_key = f"pk-lf-test-{uuid4()}"
    client = Langfuse(
        public_key=public_key,
        secret_key="sk-lf-test",
        tracer_provider=provider,
        span_exporter=exporter,
    )
    handler = PromoPlannerCallbackHandler(public_key=public_key)
    query = "Plan a Brand A campaign in Southeast."
    brief = "Campaign brief: 20% off for two weeks."
    llm = MagicMock()
    llm.invoke.side_effect = [
        MagicMock(content=json.dumps({"intent": "plan_promo", "brand": "Brand A"})),
        MagicMock(content=brief),
    ]
    research = "Research details. " * 200 + "END_OF_RESEARCH"
    strategy = "Strategy details. " * 200 + "END_OF_STRATEGY"
    monkeypatch.setattr(orchestrator, "_orchestrator", None)
    with (
        patch("src.agents.orchestrator.ChatAnthropic", return_value=llm),
        patch("src.agents.research_crew.run_research_crew", return_value=research),
        patch("src.agents.strategy_crew.run_strategy_crew", return_value=strategy),
        patch(
            "src.agents.orchestrator.run_compliance_check",
            return_value={
                "status": "APPROVED",
                "all_findings": [],
            },
        ) as compliance,
    ):
        result = orchestrator.run_orchestrator(query, config={"callbacks": [handler]})
    client.flush()

    spans = exporter.get_finished_spans()
    roots = [s for s in spans if s.parent is None]
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "PromoPlanner"
    assert json.loads(root.attributes["langfuse.observation.input"]) == {"query": query}
    assert root.attributes["langfuse.observation.output"] == brief
    assert not any(s.name == "LangGraph" for s in spans)
    assert result["research_summary"] == research
    assert result["strategy_summary"] == strategy
    assert result["final_brief"] == brief
    composition_prompt = llm.invoke.call_args.args[0][-1].content
    assert research in composition_prompt and strategy in composition_prompt
    assert strategy in compliance.call_args.kwargs["brief"]
    assert handler._planner_runs == set()
    provider.shutdown()


def test_crew_output_preserves_all_agent_results():
    result = CrewOutput(
        raw="History only",
        tasks_output=[
            TaskOutput(
                name="SalesAndInventory",
                description="Analyze sales",
                agent="DataAnalyst",
                raw="Sales: 216 rows",
            ),
            TaskOutput(
                name="MarketTrends",
                description="Research trends",
                agent="MarketResearcher",
                raw="Market: value packs",
            ),
            TaskOutput(
                name="PromoHistory",
                description="Find comparables",
                agent="PromoHistorian",
                raw="History only",
            ),
        ],
    )
    formatted = format_crew_output(result)
    assert "## SalesAndInventory\nSales: 216 rows" in formatted
    assert "## MarketTrends\nMarket: value packs" in formatted
    assert "## PromoHistory\nHistory only" in formatted


def test_strategy_task_receives_complete_research(monkeypatch, tmp_path):
    from src.agents.strategy_crew import build_strategy_crew

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "true")
    monkeypatch.setenv("CREWAI_DISABLE_TRACKING", "true")
    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(tmp_path))
    research = "Complete research context. " * 150 + "FINAL_RESEARCH_BENCHMARK"
    crew = build_strategy_crew("Plan a promotion", research_summary=research)
    assert research in crew.tasks[0].description
    assert crew.name == "PromotionStrategy"
    assert all(len(agent.role) < 30 for agent in crew.agents)
    assert all(task.name and len(task.name) < 30 for task in crew.tasks)
