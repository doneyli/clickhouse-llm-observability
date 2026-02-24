"""CSV export — powered by ClickHouse."""

import csv
import io

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from clickhouse_client import get_export_traces

router = APIRouter()


@router.get("/api/export/csv")
async def export_csv(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    project: str | None = Query(None),
):
    traces = await get_export_traces(from_ts=from_ts, to_ts=to_ts, project=project)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "trace_id", "session_id", "name", "timestamp",
        "latency_s", "total_cost", "input_tokens", "output_tokens",
        "total_tokens", "input", "output",
    ])

    for t in traces:
        writer.writerow([
            t.get("trace_id", ""),
            t.get("session_id", ""),
            t.get("name", ""),
            t.get("timestamp", ""),
            t.get("latency_s", ""),
            t.get("total_cost", ""),
            t.get("input_tokens", ""),
            t.get("output_tokens", ""),
            t.get("total_tokens", ""),
            t.get("input", ""),
            t.get("output", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=traces.csv"},
    )
