"""
Seed the Langfuse project around the traces (idempotent, name-based):

  configs     score configs: reviewer-decision, root-cause, publishing-policy
  queue       annotation queue "PM review — rejected agent runs" (root-cause + reviewer-decision)
  fill-queue  add every trace whose reviewer-decision is `rejected` to that queue
  dashboard   custom dashboard "Agent health — deployment view" (unstable dashboards API)
  all         configs + queue + dataset `regression-cases` + dashboard (run fill-queue after seeding traffic)

    .venv/bin/python scripts/seed_project.py all
    .venv/bin/python scripts/seed_project.py dashboard --dry-run   # validate widget queries only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config  # noqa: E402

QUEUE_NAME = "PM review — rejected agent runs"
DASHBOARD_NAME = "Agent health — deployment view"

SCORE_CONFIGS = [
    {"name": "reviewer-decision", "dataType": "CATEGORICAL",
     "categories": [{"label": "approved", "value": 1}, {"label": "rejected", "value": 0}],
     "description": "The marketer's decision on the agent's output (approve before anything goes live)."},
    {"name": "root-cause", "dataType": "CATEGORICAL",
     "categories": [{"label": "wrong-or-stale-context", "value": 1},
                    {"label": "instructions", "value": 2},
                    {"label": "tool-contract", "value": 3},
                    {"label": "slow-dependency", "value": 4},
                    {"label": "model-reasoning", "value": 5},
                    {"label": "not-a-bug", "value": 6}],
     "description": "Why did this run go wrong? Pick the FIRST thing that would have to change."},
    {"name": "publishing-policy", "dataType": "BOOLEAN",
     "description": "Guardrail: drafts and corrections were fact-checked before publishing, within the step limit."},
]


def _ok(r):
    if r.status_code >= 400:
        raise RuntimeError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else {}


def seed_configs() -> dict:
    have = {c["name"]: c for c in _ok(config.api("GET", "/score-configs", params={"limit": 100})).get("data", [])}
    ids = {}
    for spec in SCORE_CONFIGS:
        if spec["name"] in have:
            ids[spec["name"]] = have[spec["name"]]["id"]
            print(f"= score config {spec['name']}")
            continue
        ids[spec["name"]] = _ok(config.api("POST", "/score-configs", json=spec))["id"]
        print(f"+ score config {spec['name']}")
    return ids


DATASET_NAME = "regression-cases"


def seed_dataset() -> None:
    """Empty dataset the workshop adds traces to live ("Add to dataset" in module 5)."""
    r = config.api("GET", f"/v2/datasets/{DATASET_NAME}")
    if r.status_code == 200:
        print(f"= dataset {DATASET_NAME}")
        return
    _ok(config.api("POST", "/v2/datasets", json={
        "name": DATASET_NAME,
        "description": "Agent runs that went wrong, added from traces during review. The next "
                       "instructions change is checked against these before it gets the production label.",
        "metadata": {"source": "marketing-agent-debugging workshop"}}))
    print(f"+ dataset {DATASET_NAME}")


def _queue():
    queues = _ok(config.api("GET", "/annotation-queues", params={"limit": 100})).get("data", [])
    return next((q for q in queues if q["name"] == QUEUE_NAME), None)


def seed_queue(config_ids: dict) -> str:
    q = _queue()
    if q:
        print(f"= annotation queue {QUEUE_NAME!r}")
        return q["id"]
    q = _ok(config.api("POST", "/annotation-queues", json={
        "name": QUEUE_NAME,
        "description": "Every run a marketer rejected. Open the trace, find the first thing that went "
                       "wrong, and label the root cause. Leave a comment with the observation you used.",
        "scoreConfigIds": [config_ids["root-cause"], config_ids["reviewer-decision"]]}))
    print(f"+ annotation queue {QUEUE_NAME!r}")
    return q["id"]


def rejected_trace_ids() -> list:
    out, cursor = set(), None
    while True:
        params = {"name": "reviewer-decision", "limit": 100, "fields": "core,details,subject"}
        if cursor:
            params["cursor"] = cursor
        page = _ok(config.api("GET", "/v3/scores", params=params))
        for s in page.get("data", []):
            value = s.get("stringValue") or s.get("value")
            subject = s.get("subject") or {}   # v3 scores: {"kind": "trace", "id": <trace id>}
            trace_id = subject.get("id") if subject.get("kind") == "trace" else None
            if value == "rejected" and trace_id:
                out.add(trace_id)
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            return sorted(out)


def fill_queue() -> None:
    q = _queue()
    if not q:
        raise SystemExit("run `seed_project.py queue` first")
    have = set()
    page = 1
    while True:
        res = _ok(config.api("GET", f"/annotation-queues/{q['id']}/items", params={"limit": 100, "page": page}))
        have |= {i["objectId"] for i in res.get("data", [])}
        if page >= (res.get("meta") or {}).get("totalPages", 1):
            break
        page += 1
    added = 0
    for tid in rejected_trace_ids():
        if tid in have:
            continue
        _ok(config.api("POST", f"/annotation-queues/{q['id']}/items", json={"objectId": tid, "objectType": "TRACE"}))
        added += 1
    print(f"+ queue items: {added} added, {len(have)} already there")


# ------------------------------------------------------------------ dashboard
AGENT_ROOT = {"column": "name", "operator": "=", "value": "marketing-agent", "type": "string"}
TOOLS_ONLY = {"column": "type", "operator": "any of", "value": ["TOOL", "RETRIEVER"], "type": "stringOptions"}
GENERATIONS = {"column": "type", "operator": "=", "value": "GENERATION", "type": "string"}
PROBLEMS = {"column": "level", "operator": "any of", "value": ["ERROR", "WARNING"], "type": "stringOptions"}

WIDGETS = [
    dict(name="Agent runs", description="Agent runs (root `marketing-agent` observations).",
         view="observations", chartType="NUMBER", metrics=[{"measure": "count", "agg": "count"}],
         dimensions=[], filters=[AGENT_ROOT], w=3, h=2),
    dict(name="p95 run latency (s)", description="End-to-end latency of an agent run.",
         view="observations", chartType="NUMBER", metrics=[{"measure": "latency", "agg": "p95"}],
         dimensions=[], filters=[AGENT_ROOT], w=3, h=2),
    dict(name="Model spend", description="Sum of generation cost (agent steps + fact-checker).",
         view="observations", chartType="NUMBER", metrics=[{"measure": "totalCost", "agg": "sum"}],
         dimensions=[], filters=[GENERATIONS], w=3, h=2),
    dict(name="Avg model calls per run", description="Mean of the agent-steps score.",
         view="scores-numeric", chartType="NUMBER", metrics=[{"measure": "value", "agg": "avg"}],
         dimensions=[], filters=[{"column": "name", "operator": "=", "value": "agent-steps", "type": "string"}],
         w=3, h=2),
    dict(name="Slowest tools (p95 latency)", description="Which tool is the agent waiting on?",
         view="observations", chartType="HORIZONTAL_BAR", metrics=[{"measure": "latency", "agg": "p95"}],
         dimensions=[{"field": "name"}], filters=[TOOLS_ONLY], w=6, h=5),
    dict(name="Tool calls by tool", description="How often the agent chooses each tool.",
         view="observations", chartType="HORIZONTAL_BAR", metrics=[{"measure": "count", "agg": "count"}],
         dimensions=[{"field": "name"}], filters=[TOOLS_ONLY], w=6, h=5),
    dict(name="Reviewer decisions", description="Approved vs rejected by the marketer.",
         view="scores-categorical", chartType="PIE", metrics=[{"measure": "count", "agg": "count"}],
         dimensions=[{"field": "stringValue"}],
         filters=[{"column": "name", "operator": "=", "value": "reviewer-decision", "type": "string"}], w=4, h=5),
    dict(name="Spend by marketer", description="Model spend per user (each marketer belongs to one customer).",
         view="observations", chartType="HORIZONTAL_BAR", metrics=[{"measure": "totalCost", "agg": "sum"}],
         dimensions=[{"field": "userId"}], filters=[GENERATIONS],
         chartConfig={"type": "HORIZONTAL_BAR", "row_limit": 20}, w=4, h=5),
    dict(name="Errors and warnings by step", description="Observations at WARNING or ERROR level, by name.",
         view="observations", chartType="HORIZONTAL_BAR", metrics=[{"measure": "count", "agg": "count"}],
         dimensions=[{"field": "name"}], filters=[PROBLEMS], w=4, h=5),
    dict(name="Run latency over time (p95)", description="Watch for regressions after a config change.",
         view="observations", chartType="LINE_TIME_SERIES", metrics=[{"measure": "latency", "agg": "p95"}],
         dimensions=[], filters=[AGENT_ROOT], w=12, h=5),
]


def _validate(w) -> tuple:
    now = datetime.now(timezone.utc)
    query = {"view": w["view"], "metrics": [{"measure": m["measure"], "aggregation": m["agg"]} for m in w["metrics"]],
             "dimensions": w["dimensions"], "filters": w["filters"],
             "fromTimestamp": (now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
             "toTimestamp": now.isoformat().replace("+00:00", "Z"), "config": {"row_limit": 100}}
    if any(d["field"] in ("userId", "sessionId", "traceId") for d in w["dimensions"]):
        m0 = w["metrics"][0]
        query["orderBy"] = [{"field": f"{m0['agg']}_{m0['measure']}", "direction": "desc"}]
    r = config.api("GET", "/v2/metrics", params={"query": json.dumps(query)})
    return (r.status_code < 400, f"{len(r.json().get('data', []))} rows" if r.status_code < 400 else r.text[:300])


def seed_dashboard(dry_run: bool = False) -> None:
    U = "/unstable"
    existing_w = {w["name"]: w for w in _ok(config.api("GET", f"{U}/dashboard-widgets", params={"limit": 100})).get("data", [])}
    placed = []
    for w in WIDGETS:
        ok, note = _validate(w)
        print(f"  {'✓' if ok else '✗'} {w['name']}  ({note})")
        if not ok or dry_run:
            continue
        body = {k: w[k] for k in ("name", "description", "view", "chartType", "dimensions", "metrics", "filters")}
        if "chartConfig" in w:
            body["chartConfig"] = w["chartConfig"]
        prior = existing_w.get(w["name"])
        try:
            res = _ok(config.api("PATCH", f"{U}/dashboard-widgets/{prior['id']}", json=body)) if prior \
                else _ok(config.api("POST", f"{U}/dashboard-widgets", json=body))
        except RuntimeError as e:
            print(f"    widget rejected: {e}")
            continue
        placed.append((res["id"], w))
    if dry_run:
        return
    dashes = {d["name"]: d for d in _ok(config.api("GET", f"{U}/dashboards", params={"limit": 100})).get("data", [])}
    desc = ("What a deployment team checks first: volume, latency, which tools are slow or over-used, "
            "reviewer rejections and spend per customer.")
    dash = dashes.get(DASHBOARD_NAME)
    dash = _ok(config.api("PATCH", f"{U}/dashboards/{dash['id']}", json={"name": DASHBOARD_NAME, "description": desc})) \
        if dash else _ok(config.api("POST", f"{U}/dashboards", json={"name": DASHBOARD_NAME, "description": desc}))
    for p in _ok(config.api("GET", f"{U}/dashboards/{dash['id']}")).get("definition", {}).get("widgets", []):
        config.api("DELETE", f"{U}/dashboards/{dash['id']}/placements/{p['id']}")
    x = y = row_h = 0
    for wid, w in placed:
        if x + w["w"] > 12:
            x, y, row_h = 0, y + row_h, 0
        _ok(config.api("POST", f"{U}/dashboards/{dash['id']}/placements",
                       json={"type": "widget", "widgetId": wid, "x": x, "y": y, "width": w["w"], "height": w["h"]}))
        x += w["w"]
        row_h = max(row_h, w["h"])
    print(f"+ dashboard {DASHBOARD_NAME!r}: {len(placed)} widgets → {config.project_url('/dashboards/' + dash['id'])}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["configs", "queue", "fill-queue", "dashboard", "all"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    config.verify_project()
    if args.what in ("configs", "queue", "all"):
        ids = seed_configs()
        if args.what in ("queue", "all"):
            seed_queue(ids)
    if args.what == "all":
        seed_dataset()
    if args.what in ("dashboard", "all"):
        seed_dashboard(dry_run=args.dry_run)
    if args.what == "fill-queue":
        fill_queue()
    return 0


if __name__ == "__main__":
    sys.exit(main())
