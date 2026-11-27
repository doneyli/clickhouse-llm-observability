"""
Render captured spans (HANDOFF_CAPTURE_DIR) as Langfuse-style trees, one per trace.

    .venv/bin/python scripts/show_trace.py <capture-dir>
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

from handoff.capture import read_capture  # noqa: E402

APP_ROOT = "langfuse.internal.is_app_root"


def _decode(v):
    """SDK v5 JSON-encodes metadata values on the span ('"transfer"' for the string transfer)."""
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return v


def _label(r: dict) -> str:
    a = r["attributes"]
    t = a.get("langfuse.observation.type", "span")
    meta = {k.split("langfuse.observation.metadata.", 1)[1]: _decode(v) for k, v in a.items()
            if k.startswith("langfuse.observation.metadata.")}
    bits = []
    if t == "tool" and r["name"] == "handoff":
        bits.append(f"{meta.get('handoff_kind')} {meta.get('handoff_from')}→{meta.get('handoff_to')}")
        if meta.get("a2a_state"):
            bits.append(f"state={meta['a2a_state']}")
    if t == "agent" and meta.get("linked_by"):
        bits.append(f"linked_by={meta['linked_by']}")
    if t == "generation":
        bits.append(str(a.get("langfuse.observation.model.name", "")))
    if a.get(APP_ROOT):
        bits.append("APP-ROOT")
    sess = a.get("session.id")
    return f"{r['name']:<20} {t:<10} [{r['service']}] {'session=' + sess if sess else 'NO SESSION'}  {' '.join(bits)}"


def render(rows: list[dict]) -> str:
    by_trace = defaultdict(list)
    for r in rows:
        by_trace[r["trace_id"]].append(r)
    out = []
    for tid, spans in sorted(by_trace.items(), key=lambda kv: min(s["start"] for s in kv[1])):
        ids = {s["span_id"] for s in spans}
        children = defaultdict(list)
        roots = []
        for s in sorted(spans, key=lambda s: s["start"]):
            (children[s["parent_id"]] if s["parent_id"] in ids else roots).append(s)
        out.append(f"trace {tid}")

        def walk(s, depth):
            out.append("  " * (depth + 1) + _label(s))
            for c in children[s["span_id"]]:
                walk(c, depth + 1)
        for r in roots:
            walk(r, 0)
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(render(read_capture(Path(sys.argv[1]))))
