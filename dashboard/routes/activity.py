"""Activity heatmap and hourly breakdown endpoints."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from data_models import HeatmapDay, HeatmapResponse, HourlyActivity
from langfuse_client import fetch_all_traces

router = APIRouter()


@router.get("/api/activity/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts, name=project)

    day_counts: dict[str, int] = defaultdict(int)
    for t in traces:
        ts = t.get("timestamp")
        if ts:
            try:
                day = ts[:10]
                day_counts[day] += 1
            except (IndexError, TypeError):
                pass

    days = [HeatmapDay(date=d, count=c) for d, c in sorted(day_counts.items())]
    max_count = max((d.count for d in days), default=0)

    return HeatmapResponse(days=days, max_count=max_count)


@router.get("/api/activity/by-hour", response_model=HourlyActivity)
async def get_hourly_activity(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts, name=project)

    # 7 rows (Mon=0..Sun=6) x 24 cols (hours)
    matrix = [[0] * 24 for _ in range(7)]

    for t in traces:
        ts = t.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                weekday = dt.weekday()  # 0=Mon, 6=Sun
                hour = dt.hour
                matrix[weekday][hour] += 1
            except (ValueError, TypeError):
                pass

    day_totals = [sum(row) for row in matrix]
    max_count = max(max(row) for row in matrix) if any(any(row) for row in matrix) else 0

    return HourlyActivity(
        matrix=matrix,
        day_totals=day_totals,
        max_count=max_count,
    )
