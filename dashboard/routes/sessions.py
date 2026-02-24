"""Session list and detail — powered by ClickHouse."""

from fastapi import APIRouter, Query

from clickhouse_client import get_session_detail, get_sessions

router = APIRouter()


@router.get("/api/sessions")
async def sessions_endpoint(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    search: str | None = Query(None),
):
    sessions = await get_sessions(
        from_ts=from_ts, to_ts=to_ts, project=project,
        search=search, limit=limit,
    )
    return {"sessions": sessions, "total": len(sessions), "page": 1, "limit": limit}


@router.get("/api/sessions/{session_id}")
async def session_detail_endpoint(session_id: str):
    return await get_session_detail(session_id)
