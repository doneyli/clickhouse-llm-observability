"""CrewAI strategy crew: PromoStrategist, LiftEstimator."""

from __future__ import annotations

from typing import Any

from crewai import LLM, Agent, Crew, Process, Task

from src.config import load_config
from src.prompts.strategy import PROMPTS


def _get_llm(callbacks: list[Any] | None = None) -> LLM:
    del callbacks
    cfg = load_config()
    return LLM(
        model=f"anthropic/{cfg.llm.models.strategy_crew}",
        max_tokens=cfg.llm.max_tokens_default,
    )


def build_strategy_crew(
    query: str,
    brand: str | None = None,
    region: str | None = None,
    research_summary: str = "",
    callbacks: list[Any] | None = None,
) -> Crew:
    """Build the strategy crew for a given research context."""
    llm = _get_llm(callbacks=callbacks)

    promo_strategist = Agent(
        role=PROMPTS["promo_strategist_role"],
        goal=PROMPTS["promo_strategist_goal"]
        .replace("{{brand}}", brand or "the brand")
        .replace("{{region}}", region or "all regions"),
        backstory=PROMPTS["promo_strategist_backstory"],
        tools=[],
        llm=llm,
        max_iter=5,
        verbose=False,
    )

    lift_estimator = Agent(
        role=PROMPTS["lift_estimator_role"],
        goal=PROMPTS["lift_estimator_goal"],
        backstory=PROMPTS["lift_estimator_backstory"],
        tools=[],
        llm=llm,
        verbose=False,
    )

    task_description = (
        PROMPTS["strategy_task"]
        .replace("{{query}}", query)
        .replace("{{brand}}", brand or "the brand")
        .replace("{{region}}", region or "all regions")
        .replace("{{research_summary}}", research_summary[:2000] if research_summary else "No research data provided.")
    )

    strategy_task = Task(
        description=task_description,
        expected_output=(
            "2-3 promotional strategy options with mechanic, depth, duration, retailer targeting, and rationale."
        ),
        agent=promo_strategist,
    )

    lift_task = Task(
        description=(
            "For each promotional option proposed, estimate the expected sales lift percentage "
            "with a confidence level (HIGH/MEDIUM/LOW). "
            "Use historical benchmarks: Price 15-25% off = ~25-35% lift; "
            "Price 25-40% off = ~35-55% lift; BOGO = ~40-60%; Bundle = ~25-40%."
        ),
        expected_output=(
            "Lift estimates for each option with confidence levels and brief justification."
        ),
        agent=lift_estimator,
        context=[strategy_task],
    )

    return Crew(
        agents=[promo_strategist, lift_estimator],
        tasks=[strategy_task, lift_task],
        process=Process.sequential,
        verbose=False,
    )


def run_strategy_crew(
    query: str,
    brand: str | None = None,
    region: str | None = None,
    research_summary: str = "",
    callbacks: list[Any] | None = None,
) -> str:
    """Run the strategy crew and return the result string."""
    crew = build_strategy_crew(
        query=query,
        brand=brand,
        region=region,
        research_summary=research_summary,
        callbacks=callbacks,
    )
    result = crew.kickoff()
    return str(result)
