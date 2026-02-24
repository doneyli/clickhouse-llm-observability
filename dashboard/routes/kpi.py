"""KPI endpoint — aggregate stats across all traces."""

import statistics
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Query

from data_models import KPIResponse
from langfuse_client import fetch_all_scores, fetch_all_traces

router = APIRouter()


@router.get("/api/kpi", response_model=KPIResponse)
async def get_kpi(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts, name=project)

    if not traces:
        return KPIResponse()

    # Group by session
    sessions: dict[str, list[dict]] = defaultdict(list)
    projects: set[str] = set()
    days: set[str] = set()
    total_cost = 0.0
    latencies = []
    all_scores = []

    for t in traces:
        sid = t.get("sessionId") or "no-session"
        sessions[sid].append(t)

        name = t.get("name")
        if name:
            projects.add(name)

        ts = t.get("timestamp")
        if ts:
            try:
                day = ts[:10]
                days.add(day)
            except (IndexError, TypeError):
                pass

        # Cost from usage
        usage = t.get("usage") or {}
        cost = t.get("totalCost") or 0
        total_cost += cost

        # Latency
        latency = t.get("latency")
        if latency is not None:
            latencies.append(latency * 1000)  # seconds to ms

    # Fetch scores separately (trace.scores is a list of IDs, not objects)
    all_score_objs = await fetch_all_scores()
    for s in all_score_objs:
        val = s.get("value")
        if val is not None:
            all_scores.append(float(val))

    traces_per_session = [len(v) for v in sessions.values()]
    median_tps = statistics.median(traces_per_session) if traces_per_session else 0
    p90_tps = (
        sorted(traces_per_session)[int(len(traces_per_session) * 0.9)]
        if traces_per_session
        else 0
    )

    return KPIResponse(
        sessions_count=len(sessions),
        traces_count=len(traces),
        projects_count=len(projects),
        active_days=len(days),
        median_traces_per_session=median_tps,
        p90_traces_per_session=p90_tps,
        avg_latency_ms=(
            statistics.mean(latencies) if latencies else 0
        ),
        total_cost=round(total_cost, 6),
        avg_score=(
            round(statistics.mean(all_scores), 3) if all_scores else None
        ),
    )
