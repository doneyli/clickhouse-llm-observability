"""CSV export endpoint."""

import csv
import io

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from langfuse_client import fetch_all_traces

router = APIRouter()


@router.get("/api/export/csv")
async def export_csv(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts, name=project)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "trace_id", "session_id", "name", "timestamp",
        "latency_s", "total_cost", "input_tokens", "output_tokens",
        "total_tokens", "input", "output",
    ])

    for t in traces:
        usage = t.get("usage") or {}
        raw_input = t.get("input")
        raw_output = t.get("output")
        writer.writerow([
            t.get("id", ""),
            t.get("sessionId", ""),
            t.get("name", ""),
            t.get("timestamp", ""),
            t.get("latency", ""),
            t.get("totalCost", ""),
            usage.get("inputTokens") or usage.get("input") or "",
            usage.get("outputTokens") or usage.get("output") or "",
            usage.get("totalTokens") or usage.get("total") or "",
            str(raw_input)[:1000] if raw_input else "",
            str(raw_output)[:1000] if raw_output else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=traces.csv"},
    )
