"""Activity heatmap and hourly — powered by ClickHouse."""

from fastapi import APIRouter, Query

from clickhouse_client import get_heatmap, get_hourly

router = APIRouter()


@router.get("/api/activity/heatmap")
async def heatmap_endpoint(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    return await get_heatmap(from_ts=from_ts, to_ts=to_ts, project=project)


@router.get("/api/activity/by-hour")
async def hourly_endpoint(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    return await get_hourly(from_ts=from_ts, to_ts=to_ts, project=project)
