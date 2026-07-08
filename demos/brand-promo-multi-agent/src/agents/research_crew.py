"""CrewAI research crew: DataAnalyst, MarketResearcher, HistorianAgent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import BaseTool

from src.config import load_config
from src.prompts.research import PROMPTS
from src.tools.market import get_market_trends
from src.tools.sales import query_inventory, query_sales


def _get_llm(callbacks: list[Any] | None = None) -> LLM:
    # CrewAI 1.x requires its own LLM wrapper (litellm-based). `callbacks` is
    # accepted for signature compatibility with orchestrator callers but is not
    # forwarded - LangChain callbacks do not attach to crewai.LLM.
    del callbacks
    cfg = load_config()
    return LLM(
        model=f"anthropic/{cfg.llm.models.research_crew}",
        max_tokens=cfg.llm.max_tokens_default,
    )


class _LCToolAdapter(BaseTool):
    """Wrap a LangChain @tool-decorated function as a crewai.tools.BaseTool."""

    lc_tool: Any = None

    def __init__(self, lc_tool: Any):
        super().__init__(
            name=lc_tool.name,
            description=lc_tool.description,
            args_schema=lc_tool.args_schema,
        )
        self.lc_tool = lc_tool

    def _run(self, **kwargs: Any) -> Any:
        return self.lc_tool.invoke(kwargs)


def _keyword_search_promos(brand: str | None, region: str | None, query: str) -> dict[str, Any]:
    """Simple keyword search over historical_promos.json."""
    path = Path(__file__).parent.parent / "data" / "historical_promos.json"
    records = json.loads(path.read_text())["records"]

    search_terms = []
    if brand:
        search_terms.append(brand.lower())
    if region:
        search_terms.append(region.lower())
    query_words = [w.lower() for w in query.split() if len(w) > 3]
    search_terms.extend(query_words[:3])

    matches = []
    for rec in records:
        rec_text = json.dumps(rec).lower()
        if any(term in rec_text for term in search_terms):
            matches.append(rec)

    if not matches:
        matches = records[:5]

    avg_lift = round(
        sum(m["observed_lift_pct"] for m in matches) / len(matches), 1
    ) if matches else 0.0

    return {
        "status": "ok",
        "tool.outcome": "ok",
        "match_count": len(matches),
        "avg_observed_lift_pct": avg_lift,
        "top_matches": matches[:5],
    }


def build_research_crew(
    query: str,
    brand: str | None = None,
    region: str | None = None,
    callbacks: list[Any] | None = None,
) -> Crew:
    """Build the research crew for a given query context."""
    llm = _get_llm(callbacks=callbacks)

    data_analyst = Agent(
        role=PROMPTS["data_analyst_role"],
        goal=PROMPTS["data_analyst_goal"].replace("{{brand}}", brand or "all brands").replace(
            "{{region}}", region or "all regions"
        ),
        backstory=PROMPTS["data_analyst_backstory"],
        tools=[_LCToolAdapter(query_sales), _LCToolAdapter(query_inventory)],
        llm=llm,
        verbose=False,
    )

    market_researcher = Agent(
        role=PROMPTS["market_researcher_role"],
        goal=PROMPTS["market_researcher_goal"]
        .replace("{{brand}}", brand or "CPG")
        .replace("{{region}}", region or "all regions"),
        backstory=PROMPTS["market_researcher_backstory"],
        tools=[_LCToolAdapter(get_market_trends)],
        llm=llm,
        verbose=False,
    )

    historian = Agent(
        role=PROMPTS["historian_role"],
        goal=PROMPTS["historian_goal"]
        .replace("{{brand}}", brand or "all brands")
        .replace("{{region}}", region or "all regions"),
        backstory=PROMPTS["historian_backstory"],
        tools=[],
        llm=llm,
        verbose=False,
    )

    task_description = (
        PROMPTS["research_task"]
        .replace("{{query}}", query)
        .replace("{{brand}}", brand or "all brands")
        .replace("{{region}}", region or "all regions")
    )

    # Historical context is pre-fetched and injected to avoid historian needing a tool
    historical_data = _keyword_search_promos(brand, region, query)
    task_description += f"\n\nPre-fetched historical promo data:\n{json.dumps(historical_data, indent=2)}"

    research_task = Task(
        description=task_description,
        expected_output=(
            "A structured ResearchPackage JSON with keys: "
            "sales_summary, inventory_summary, market_trends, historical_context"
        ),
        agent=data_analyst,
    )

    market_task = Task(
        description=f"Research market trends for {brand or 'CPG'} in {region or 'all regions'}.",
        expected_output="3-4 key market trends relevant to the promotional query.",
        agent=market_researcher,
    )

    history_task = Task(
        description=(
            f"Summarize historical promotional performance for {brand or 'all brands'} "
            f"in {region or 'all regions'} using the pre-fetched data above."
        ),
        expected_output=(
            "Key patterns from historical promos: best mechanics, average lift ranges, notable results."
        ),
        agent=historian,
        context=[research_task, market_task],
    )

    return Crew(
        agents=[data_analyst, market_researcher, historian],
        tasks=[research_task, market_task, history_task],
        process=Process.sequential,
        verbose=False,
    )


def run_research_crew(
    query: str,
    brand: str | None = None,
    region: str | None = None,
    callbacks: list[Any] | None = None,
) -> str:
    """Run the research crew and return the result string."""
    crew = build_research_crew(query=query, brand=brand, region=region, callbacks=callbacks)
    result = crew.kickoff()
    return str(result)
