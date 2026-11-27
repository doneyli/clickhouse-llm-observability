#!/usr/bin/env python3
"""
Reconcile the two evaluation datasets for the Cluster Health Investigator.

Bad decomposition is a PLAN defect; bad execution is a WORKER defect. Two
datasets keep them separable:

    cluster-health/plan-quality    input=symptom, expected=plan criteria
                                   (linked to the orchestrator `agent` span)
    cluster-health/worker-quality  input=analysis+focus, expected=finding criteria
                                   (linked to individual `worker` spans)

Golden seed: 8 plan-quality items (one per DEMO_SYMPTOMS entry) and 10
worker-quality items (one per catalog analysis), under stable ids (chp-NN, chw-NN).

Reconcile, not append: each dataset and item is read back first. A dataset or item that
already matches its definition (description and metadata; input, expected output,
metadata, source link and ACTIVE status) is left alone, so an unchanged run writes
nothing and adds no item versions. A changed or archived item is upserted under the same
id (updated); a missing one is created. Items this script does not own are never touched.
Exit 1 if any write or read failed.

`--from-traces N` capture mode finds the N most recent `investigate-cluster-symptom`
runs through their `orchestrator` observations (Observations API v2; the v1 list on the
stack's 3.x server) and creates items whose `source_observation_id` points at the real
orchestrator span (plan-quality) / worker spans (worker-quality), so you can click from a
dataset item straight to the production observation it came from. Captured items get
stable ids from their observation id, so capturing the same run twice is unchanged.

Usage (the env comes from the root .env; see verify_demo.prepare_env):
    python scripts/seed_datasets.py                 # golden items
    python scripts/seed_datasets.py --from-traces 5 # + capture from 5 recent runs
    python scripts/seed_datasets.py --dry-run       # list the golden items, no requests
"""

import argparse
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_catalog import CATALOG  # noqa: E402
from verify_demo import (  # noqa: E402  (stdlib env + read helpers, tested there)
    ApiError, Langfuse, client_from_env, iso, parse_io, prepare_env, recent_trace_ids,
)

PLAN_DATASET = "cluster-health/plan-quality"
WORKER_DATASET = "cluster-health/worker-quality"
DATASETS = {
    PLAN_DATASET: "Plan-quality: does the planner decompose proportionately (no over/under fan-out, no duplication)?",
    WORKER_DATASET: "Worker-quality: does one analysis produce a verdict-bearing, cited finding?",
}
DATASET_METADATA = {"source": "cluster-health-investigator"}
CAPTURE_LOOKBACK_DAYS = 7

PLAN_ITEMS = [
    {"symptom": "One Grafana dashboard query got slow this afternoon; everything else feels fine.",
     "criteria": ["must include slow_queries", "must NOT exceed 2 tasks",
                  "no two tasks may share an analysis_type"],
     "failure_mode": "over-fanout"},
    {"symptom": "We're seeing occasional query exceptions in the last hour but throughput looks ok.",
     "criteria": ["must include query_errors", "must NOT exceed 3 tasks",
                  "no two tasks may share an analysis_type"],
     "failure_mode": "over-fanout"},
    {"symptom": "Mutations look stuck on one table and ALTERs never seem to finish.",
     "criteria": ["must include mutation_status", "must NOT exceed 4 tasks",
                  "no two tasks may share an analysis_type"],
     "failure_mode": "under-coverage"},
    {"symptom": "Ingest latency crept up over the last day and a couple of merges seem to hang.",
     "criteria": ["must include at least one of insert_profile | merge_backlog",
                  "must NOT exceed 5 tasks", "no two tasks may share an analysis_type"],
     "failure_mode": "duplicated-work"},
    {"symptom": "Inserts got slow after last night's deploy, CPU is pinned and disk is filling.",
     "criteria": ["must include insert_profile and parts_pressure",
                  "must include at least one of merge_backlog | memory_pressure",
                  "must include disk_usage", "must NOT exceed 6 tasks",
                  "no two tasks may share an analysis_type"],
     "failure_mode": "over-fanout"},
    {"symptom": "The whole cluster feels unhealthy since yesterday — slow, erroring, and bloated.",
     "criteria": ["must include at least 4 distinct analyses",
                  "must include table_growth or parts_pressure", "must NOT exceed 6 tasks",
                  "no two tasks may share an analysis_type"],
     "failure_mode": "over-fanout"},
    {"symptom": "Is my cluster healthy?",
     "criteria": ["a broad but bounded sweep (3-6 tasks)",
                  "no two tasks may share an analysis_type"],
     "failure_mode": "over-fanout"},
    {"symptom": "Queries that touch one big table are slow and it keeps growing.",
     "criteria": ["must include slow_queries", "must include table_growth or parts_pressure",
                  "must NOT exceed 4 tasks", "no two tasks may share an analysis_type"],
     "failure_mode": "duplicated-work"},
]

# 10 golden worker-quality items — one per catalog analysis.
WORKER_ITEMS = [
    ("slow_queries", "last 24h, ClickHouse SELECTs",
     ["at least one query duration", "a healthy/unhealthy verdict"]),
    ("query_errors", "last 24h, group by error code",
     ["error-code frequency", "a healthy/unhealthy verdict"]),
    ("parts_pressure", "last 24h, all tables",
     ["part count", "at least one named table", "an explicit healthy/unhealthy verdict"]),
    ("merge_backlog", "active merges right now",
     ["merge count or queue depth", "a healthy/unhealthy verdict"]),
    ("insert_profile", "last 24h, insert batch sizes",
     ["avg rows per insert", "a healthy/unhealthy verdict"]),
    ("memory_pressure", "current usage vs limits",
     ["a memory figure", "a healthy/unhealthy verdict"]),
    ("disk_usage", "free vs total per disk",
     ["used percentage or free space", "a healthy/unhealthy verdict"]),
    ("table_growth", "largest tables by size",
     ["at least one named table", "a size figure", "a healthy/unhealthy verdict"]),
    ("mutation_status", "stuck or failed mutations",
     ["mutation state (done/not done)", "a healthy/unhealthy verdict"]),
    ("settings_audit", "non-default settings",
     ["at least one setting name", "a note on whether it's worth flagging"]),
]


def golden_items() -> List[dict]:
    """Every owned golden item, in the request body's shape."""
    out = []
    for i, it in enumerate(PLAN_ITEMS, 1):
        out.append({"id": f"chp-{i:02d}", "datasetName": PLAN_DATASET,
                    "input": {"symptom": it["symptom"]},
                    "expectedOutput": {"criteria": it["criteria"]},
                    "metadata": {"failure_mode": it["failure_mode"], "source": "golden"}})
    for i, (atype, focus, must) in enumerate(WORKER_ITEMS, 1):
        out.append({"id": f"chw-{i:02d}", "datasetName": WORKER_DATASET,
                    "input": {"analysis_type": atype, "focus": focus},
                    "expectedOutput": {"must_mention": must},
                    "metadata": {"catalog_desc": CATALOG[atype].description, "source": "golden"}})
    return out


_COMPARED = ("input", "expectedOutput", "metadata", "sourceTraceId", "sourceObservationId")


def item_matches(existing: Optional[dict], want: dict) -> bool:
    if not existing or existing.get("status") != "ACTIVE":
        return False
    return all((existing.get(k) or None) == (want.get(k) or None) for k in _COMPARED)


def reconcile_datasets(lf: Langfuse) -> Dict[str, str]:
    """Returns dataset name -> created | updated | unchanged | failed."""
    outcomes = {}
    for name, desc in DATASETS.items():
        detail = ""
        try:
            try:
                cur = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(name, safe='')}")
            except ApiError as e:
                if e.status != 404:
                    raise
                cur = None
            if cur and cur.get("description") == desc and (cur.get("metadata") or {}) == DATASET_METADATA:
                outcome = "unchanged"
            else:
                lf.post("/api/public/v2/datasets", {"name": name, "description": desc, "metadata": DATASET_METADATA})
                outcome = "updated" if cur else "created"
        except ApiError as e:
            outcome, detail = "failed", f": {e}"
        outcomes[name] = outcome
        print(f"  {outcome:<9} dataset {name}{detail}")
    return outcomes


def reconcile_items(lf: Langfuse, wanted: List[dict]) -> Dict[str, str]:
    """Upsert only what differs. Returns item id -> created | updated | unchanged | failed."""
    existing: Dict[str, dict] = {}
    outcomes: Dict[str, str] = {}
    for name in sorted({w["datasetName"] for w in wanted}):
        try:
            for row in lf.pages("/api/public/dataset-items", {"datasetName": name}):
                existing[row.get("id")] = row
        except ApiError as e:
            if e.status != 404:  # 404: the dataset does not exist yet, so neither do its items
                for w in wanted:
                    if w["datasetName"] == name:
                        outcomes[w["id"]] = "failed"
                print(f"  failed    items of {name}: {e}")
    for want in wanted:
        if want["id"] in outcomes:
            continue
        cur = existing.get(want["id"])
        if item_matches(cur, want):
            outcomes[want["id"]] = "unchanged"
            continue
        try:
            lf.post("/api/public/dataset-items", dict(want, status="ACTIVE"))
            outcomes[want["id"]] = "updated" if cur else "created"
        except ApiError as e:
            outcomes[want["id"]] = "failed"
            print(f"  failed    item {want['id']}: {e}")
    for o in ("created", "updated"):
        ids = [i for i, v in outcomes.items() if v == o]
        if ids:
            print(f"  {o:<9} {len(ids)} item(s): {', '.join(ids)}")
    print(f"  unchanged {sum(1 for v in outcomes.values() if v == 'unchanged')} item(s)")
    return outcomes


def captured_items(lf: Langfuse, limit: int, now: Optional[datetime] = None) -> List[dict]:
    """Items linked to the orchestrator and worker spans of the `limit` most recent runs."""
    now = now or datetime.now(timezone.utc)
    window = {"fromStartTime": iso(now - timedelta(days=CAPTURE_LOOKBACK_DAYS)),
              "toStartTime": iso(now + timedelta(minutes=5))}
    orchestrators = lf.observations(dict(window, name="orchestrator"), fields="core,basic,io")
    out: List[dict] = []
    for trace_id in recent_trace_ids(orchestrators, limit):
        obs = lf.observations(dict(window, traceId=trace_id), fields="core,basic,io,metadata")
        orch = sorted((o for o in obs if o.get("name") == "orchestrator"), key=lambda o: o.get("startTime") or "")
        symptom = (parse_io(orch[0].get("input")) or {}).get("symptom") if orch else None
        if orch and isinstance(symptom, str):
            out.append({"id": f"chp-src-{orch[0]['id']}", "datasetName": PLAN_DATASET,
                        "input": {"symptom": symptom},
                        "expectedOutput": {"criteria": ["matches the plan-quality rubric"]},
                        "sourceTraceId": trace_id, "sourceObservationId": orch[0]["id"],
                        "metadata": {"failure_mode": "captured", "source": "demo-run"}})
        for w in (o for o in obs if o.get("name") == "worker"):
            meta = w.get("metadata") or {}
            winput = parse_io(w.get("input")) or {}
            winput = winput if isinstance(winput, dict) else {}
            out.append({"id": f"chw-src-{w['id']}", "datasetName": WORKER_DATASET,
                        "input": {"analysis_type": meta.get("analysis_type") or winput.get("analysis_type", "unknown"),
                                  "focus": meta.get("focus") or winput.get("focus", "")},
                        "expectedOutput": {"must_mention": ["a verdict", "specific numbers"]},
                        "sourceTraceId": trace_id, "sourceObservationId": w["id"],
                        "metadata": {"source": "demo-run"}})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-traces", type=int, default=0, metavar="N",
                    help="Also capture source-linked items from the N most recent runs")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(f"[dry-run] would reconcile {len(PLAN_ITEMS)} plan-quality + "
              f"{len(WORKER_ITEMS)} worker-quality items")
        for it in PLAN_ITEMS:
            print(f"  plan  | {it['symptom'][:70]}")
        for at, focus, _ in WORKER_ITEMS:
            print(f"  work  | {at} — {focus}")
        return 0

    ok, message = prepare_env()
    if not ok:
        print(f"Datasets NOT seeded: {message}")
        return 1
    lf = client_from_env()
    print(f"Reconciling cluster-health datasets at {lf.base} ({message}) ...")
    failed = [n for n, o in reconcile_datasets(lf).items() if o == "failed"]
    wanted = golden_items()
    if args.from_traces:
        try:
            captured = captured_items(lf, args.from_traces)
            print(f"  captured  {len(captured)} source-linked item(s) from up to {args.from_traces} run(s)")
            wanted += captured
        except ApiError as e:
            failed.append(f"capture: {e}")
            print(f"  failed    capture from recent runs: {e}")
    outcomes = reconcile_items(lf, wanted)
    failed += [i for i, o in outcomes.items() if o == "failed"]
    if failed:
        print(f"Datasets INCOMPLETE: {len(failed)} failure(s).")
        return 1
    print("Datasets reconciled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
