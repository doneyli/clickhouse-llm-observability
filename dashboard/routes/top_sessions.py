"""Top sessions ranked by various metrics."""

from collections import defaultdict

from fastapi import APIRouter, Query

from data_models import TopSession, TopSessionsResponse
from langfuse_client import fetch_all_traces

router = APIRouter()


@router.get("/api/top-sessions", response_model=TopSessionsResponse)
async def get_top_sessions(
    sort: str = Query("traces", regex="^(traces|duration|cost)$"),
    limit: int = Query(10, ge=1, le=50),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts, name=project)

    sessions: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        sid = t.get("sessionId")
        if sid:
            sessions[sid].append(t)

    top = []
    for sid, session_traces in sessions.items():
        session_traces.sort(key=lambda t: t.get("timestamp") or "")
        total_cost = sum(t.get("totalCost") or 0 for t in session_traces)
        total_duration = sum((t.get("latency") or 0) * 1000 for t in session_traces)
        project_name = session_traces[0].get("name") if session_traces else None

        top.append(
            TopSession(
                id=sid,
                project=project_name,
                trace_count=len(session_traces),
                total_duration_ms=round(total_duration, 1),
                total_cost=round(total_cost, 6),
                first_trace=session_traces[0].get("timestamp") if session_traces else None,
            )
        )

    sort_key = {
        "traces": lambda s: s.trace_count,
        "duration": lambda s: s.total_duration_ms,
        "cost": lambda s: s.total_cost,
    }
    top.sort(key=sort_key[sort], reverse=True)

    return TopSessionsResponse(
        sessions=top[:limit],
        sort_by=sort,
    )
