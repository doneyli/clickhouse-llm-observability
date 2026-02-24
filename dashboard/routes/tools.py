"""Tool/span usage analytics."""

from collections import defaultdict

from fastapi import APIRouter, Query

from data_models import ToolUsage, ToolsResponse
from langfuse_client import fetch_all_observations

router = APIRouter()


@router.get("/api/tools", response_model=ToolsResponse)
async def get_tools(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
):
    observations = await fetch_all_observations(
        obs_type="SPAN", from_ts=from_ts, to_ts=to_ts
    )

    tool_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "total_latency": 0.0}
    )

    for obs in observations:
        name = obs.get("name") or "unknown"
        tool_stats[name]["count"] += 1

        start = obs.get("startTime")
        end = obs.get("endTime")
        if start and end:
            from datetime import datetime

            try:
                s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                tool_stats[name]["total_latency"] += (e - s).total_seconds() * 1000
            except (ValueError, TypeError):
                pass

    tools = []
    for name, stats in tool_stats.items():
        avg_lat = (
            stats["total_latency"] / stats["count"] if stats["count"] > 0 else 0
        )
        tools.append(
            ToolUsage(
                name=name,
                count=stats["count"],
                avg_latency_ms=round(avg_lat, 1),
            )
        )

    tools.sort(key=lambda t: t.count, reverse=True)
    return ToolsResponse(tools=tools)
