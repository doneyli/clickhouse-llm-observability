"""Quality score analytics."""

import statistics
from collections import defaultdict

from fastapi import APIRouter, Query

from data_models import ScoreDistribution, ScoresResponse
from langfuse_client import fetch_all_scores

router = APIRouter()


@router.get("/api/scores", response_model=ScoresResponse)
async def get_scores():
    all_scores = await fetch_all_scores()

    grouped: dict[str, list[float]] = defaultdict(list)
    for s in all_scores:
        name = s.get("name")
        val = s.get("value")
        if name and val is not None:
            grouped[name].append(float(val))

    distributions = []
    for name, values in sorted(grouped.items()):
        distributions.append(
            ScoreDistribution(
                name=name,
                count=len(values),
                avg=round(statistics.mean(values), 3),
                min=round(min(values), 3),
                max=round(max(values), 3),
                values=values,
            )
        )

    return ScoresResponse(scores=distributions)
