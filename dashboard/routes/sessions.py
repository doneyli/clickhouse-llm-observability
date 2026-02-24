"""Session list and detail endpoints."""

from collections import defaultdict

from fastapi import APIRouter, Query

from data_models import (
    SessionDetailResponse,
    SessionListResponse,
    SessionSummary,
    TraceDetail,
)
from langfuse_client import fetch_all_traces, fetch_traces

router = APIRouter()


def _build_session_summaries(traces: list[dict]) -> list[SessionSummary]:
    """Group traces into session summaries."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        sid = t.get("sessionId")
        if sid:
            sessions[sid].append(t)

    summaries = []
    for sid, session_traces in sessions.items():
        # Sort by timestamp
        session_traces.sort(key=lambda t: t.get("timestamp") or "")

        total_cost = sum(t.get("totalCost") or 0 for t in session_traces)
        total_latency = sum(
            (t.get("latency") or 0) * 1000 for t in session_traces
        )
        total_tokens = 0
        for t in session_traces:
            usage = t.get("usage") or {}
            total_tokens += usage.get("totalTokens") or usage.get("total") or 0

        # Use first trace's name as project
        project = session_traces[0].get("name") if session_traces else None

        summaries.append(
            SessionSummary(
                id=sid,
                trace_count=len(session_traces),
                first_trace=session_traces[0].get("timestamp") if session_traces else None,
                last_trace=session_traces[-1].get("timestamp") if session_traces else None,
                project=project,
                total_cost=round(total_cost, 6),
                total_latency_ms=round(total_latency, 1),
                total_tokens=total_tokens,
            )
        )

    # Sort by most recent first
    summaries.sort(key=lambda s: s.last_trace or "", reverse=True)
    return summaries


@router.get("/api/sessions", response_model=SessionListResponse)
async def get_sessions(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts, name=project)

    # Apply search filter
    if search:
        search_lower = search.lower()
        traces = [
            t for t in traces
            if search_lower in (t.get("name") or "").lower()
            or search_lower in (t.get("sessionId") or "").lower()
            or search_lower in str(t.get("input") or "").lower()
        ]

    summaries = _build_session_summaries(traces)
    total = len(summaries)

    # Paginate
    start = (page - 1) * limit
    end = start + limit
    page_summaries = summaries[start:end]

    return SessionListResponse(
        sessions=page_summaries,
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/api/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(session_id: str):
    result = await fetch_traces(session_id=session_id, limit=100)
    traces_data = result.get("data", [])

    traces_data.sort(key=lambda t: t.get("timestamp") or "")

    trace_details = []
    total_cost = 0.0
    total_tokens = 0
    total_latency = 0.0

    for t in traces_data:
        cost = t.get("totalCost") or 0
        latency = (t.get("latency") or 0) * 1000
        usage = t.get("usage") or {}
        tokens = usage.get("totalTokens") or usage.get("total") or 0

        total_cost += cost
        total_tokens += tokens
        total_latency += latency

        # Extract scores
        scores = {}
        for score in (t.get("scores") or []):
            name = score.get("name")
            val = score.get("value")
            if name and val is not None:
                scores[name] = val

        # Truncate input/output for display
        raw_input = t.get("input")
        raw_output = t.get("output")
        input_str = str(raw_input)[:500] if raw_input else None
        output_str = str(raw_output)[:500] if raw_output else None

        trace_details.append(
            TraceDetail(
                id=t.get("id", ""),
                name=t.get("name"),
                timestamp=t.get("timestamp"),
                input=input_str,
                output=output_str,
                latency_ms=round(latency, 1),
                cost=round(cost, 6),
                tokens=tokens,
                scores=scores,
            )
        )

    return SessionDetailResponse(
        id=session_id,
        traces=trace_details,
        total_cost=round(total_cost, 6),
        total_tokens=total_tokens,
        total_latency_ms=round(total_latency, 1),
        trace_count=len(trace_details),
    )
