"""Projects — powered by ClickHouse."""

from fastapi import APIRouter, Query

from clickhouse_client import get_projects

router = APIRouter()


@router.get("/api/projects")
async def projects_endpoint(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
):
    projects = await get_projects(from_ts=from_ts, to_ts=to_ts)
    return {"projects": projects}
