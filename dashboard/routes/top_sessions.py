"""Top sessions — powered by ClickHouse."""

from fastapi import APIRouter, Query

from clickhouse_client import get_top_sessions

router = APIRouter()


@router.get("/api/top-sessions")
async def top_sessions_endpoint(
    sort: str = Query("traces"),
    limit: int = Query(10, ge=1, le=50),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    sessions = await get_top_sessions(
        sort=sort, limit=limit, from_ts=from_ts, to_ts=to_ts, project=project,
    )
    return {"sessions": sessions, "sort_by": sort}
