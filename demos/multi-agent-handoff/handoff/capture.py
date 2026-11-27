"""
A span exporter that appends each exported span to a JSONL file.

Used by the offline linkage matrix and the tests: with `HANDOFF_CAPTURE_DIR` set, every
process writes exactly what the Langfuse SDK would have sent (after its own filtering,
app-root marking and baggage handling) to `<dir>/<service>.jsonl`, so cross-process
behaviour can be checked without a Langfuse server.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import format_span_id, format_trace_id


class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: Path, service: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.service = service
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = []
        for s in spans:
            rows.append({
                "service": self.service,
                "name": s.name,
                "trace_id": format_trace_id(s.context.trace_id),
                "span_id": format_span_id(s.context.span_id),
                "parent_id": format_span_id(s.parent.span_id) if s.parent else None,
                "scope": s.instrumentation_scope.name if s.instrumentation_scope else None,
                "start": s.start_time,
                "attributes": {k: (list(v) if isinstance(v, tuple) else v) for k, v in (s.attributes or {}).items()},
            })
        with self._lock, self.path.open("a") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def read_capture(directory: Path) -> list[dict]:
    rows: list[dict] = []
    for p in sorted(Path(directory).glob("*.jsonl")):
        rows.extend(json.loads(line) for line in p.read_text().splitlines() if line.strip())
    return rows
