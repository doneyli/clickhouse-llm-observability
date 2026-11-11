"""CrewAI strategy crew: PromoStrategist, LiftEstimator."""

from __future__ import annotations

import os
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task

from src.agents.crew_output import format_crew_output
from src.config import load_config
from src.prompts.strategy import PROMPTS


def _get_llm(callbacks: list[Any] | None = None) -> LLM:
    del callbacks
    cfg = load_config()
    return LLM(
        model=f"anthropic/{cfg.llm.models.strategy_crew}",
        max_tokens=cfg.llm.max_tokens_crew,
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

    # --system-prompt-file experiment hook: run_experiment.py sets this env var
    # from the alternative prompt file. Appending it to the strategist's backstory
    # is what makes the A/B run (e.g. prompts/strategy_v2.md's margin-protection
    # constraints) actually change behavior — otherwise the flag only altered a
    # metadata label and the "v2" run was identical to baseline.
    strategist_backstory = PROMPTS["promo_strategist_backstory"]
    prompt_override = os.getenv("PROMO_SYSTEM_PROMPT_OVERRIDE", "").strip()
    if prompt_override:
        strategist_backstory += "\n\nAdditional strategy constraints:\n" + prompt_override

    promo_strategist = Agent(
        role=PROMPTS["promo_strategist_role"],
        goal=PROMPTS["promo_strategist_goal"]
        .replace("{{brand}}", brand or "the brand")
        .replace("{{region}}", region or "all regions"),
        backstory=strategist_backstory,
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
        .replace("{{research_summary}}", research_summary or "No research data provided.")
    )
    task_description += (
        "\nReturn all options and your recommendation in at most 350 words. "
        "Use a compact table or JSON; avoid long introductions and repeated research."
    )

    strategy_task = Task(
        name="PromoOptions",
        description=task_description,
        expected_output=(
            "2-3 complete options and a recommendation, with mechanic, depth, duration, retailers, "
            "and rationale; at most 350 words total."
        ),
        agent=promo_strategist,
    )

    lift_task = Task(
        name="LiftForecast",
        description=(
            "For each promotional option proposed, estimate the expected sales lift percentage "
            "with a confidence level (HIGH/MEDIUM/LOW). "
            "Use historical benchmarks: Price 15-25% off = ~25-35% lift; "
            "Price 25-40% off = ~35-55% lift; BOGO = ~40-60%; Bundle = ~25-40%. "
            "Return a compact forecast table and short caveats in at most 250 words. "
            "Do not repeat the full strategy or invent observed measurements."
        ),
        expected_output=(
            "Complete lift estimates, confidence, and caveats for each option, under 250 words."
        ),
        agent=lift_estimator,
        context=[strategy_task],
    )

    return Crew(
        name="PromotionStrategy",
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
    return format_crew_output(result)
