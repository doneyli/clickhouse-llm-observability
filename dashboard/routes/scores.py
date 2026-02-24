"""Quality scores — powered by ClickHouse."""

from fastapi import APIRouter

from clickhouse_client import get_scores

router = APIRouter()


@router.get("/api/scores")
async def scores_endpoint():
    scores = await get_scores()
    return {"scores": scores}
