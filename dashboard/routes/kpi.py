"""KPI endpoint — powered by ClickHouse."""

from fastapi import APIRouter, Query

from clickhouse_client import get_kpi

router = APIRouter()


@router.get("/api/kpi")
async def kpi_endpoint(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    return await get_kpi(from_ts=from_ts, to_ts=to_ts, project=project)
