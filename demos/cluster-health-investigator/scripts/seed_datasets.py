#!/usr/bin/env python3
"""
Seed the two evaluation datasets for the Cluster Health Investigator.

Bad decomposition is a PLAN defect; bad execution is a WORKER defect. Two
datasets keep them separable:

    cluster-health/plan-quality    input=symptom, expected=plan criteria
                                   (linked to the orchestrator `agent` span)
    cluster-health/worker-quality  input=analysis+focus, expected=finding criteria
                                   (linked to individual `worker` spans)

Golden seed: 8 plan-quality items (one per DEMO_SYMPTOMS entry) and 10
worker-quality items (one per catalog analysis). Idempotent via stable item ids.

`--from-traces` capture mode walks recent `investigate-cluster-symptom` traces
via the public API and creates items whose `source_observation_id` points at the
real orchestrator span (plan-quality) / worker spans (worker-quality), so you can
click from a dataset item straight to the production observation it came from.

Usage:
    python scripts/seed_datasets.py                 # golden items
    python scripts/seed_datasets.py --from-traces 5 # + capture from 5 recent traces
    python scripts/seed_datasets.py --dry-run
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis_catalog import CATALOG  # noqa: E402

PLAN_DATASET = "cluster-health/plan-quality"
WORKER_DATASET = "cluster-health/worker-quality"

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SK = os.getenv("LANGFUSE_SECRET_KEY", "")

# 8 golden plan-quality items — one per DEMO_SYMPTOMS.md entry.
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


def _auth_header():
    token = base64.b64encode(f"{PK}:{SK}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _api_get(path: str):
    req = urllib.request.Request(f"{HOST}{path}", headers=_auth_header())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _capture_from_traces(lf, limit: int):
    """Create items linked to real orchestrator/worker observations."""
    print(f"\nCapturing source observations from {limit} recent traces...")
    try:
        traces = _api_get(
            f"/api/public/traces?name=investigate-cluster-symptom&limit={limit}"
        ).get("data", [])
    except Exception as e:
        print(f"  ! could not list traces: {e}")
        return
    captured = 0
    for t in traces:
        try:
            full = _api_get(f"/api/public/traces/{t['id']}")
        except Exception:
            continue
        obs = full.get("observations", [])
        symptom = (full.get("input") or {}).get("symptom") if isinstance(full.get("input"), dict) else None
        orch = next((o for o in obs if o.get("name") == "orchestrator"), None)
        if orch and symptom:
            lf.create_dataset_item(
                dataset_name=PLAN_DATASET,
                input={"symptom": symptom},
                expected_output={"criteria": ["matches the plan-quality rubric"]},
                source_trace_id=t["id"], source_observation_id=orch["id"],
                metadata={"failure_mode": "captured", "source": "demo-run"})
            captured += 1
        for w in [o for o in obs if o.get("name") == "worker"]:
            atype = (w.get("metadata") or {}).get("analysis_type", "unknown")
            lf.create_dataset_item(
                dataset_name=WORKER_DATASET,
                input={"analysis_type": atype, "focus": (w.get("metadata") or {}).get("focus", "")},
                expected_output={"must_mention": ["a verdict", "specific numbers"]},
                source_trace_id=t["id"], source_observation_id=w["id"],
                metadata={"source": "demo-run"})
            captured += 1
    print(f"  ✓ captured {captured} source-linked items")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-traces", type=int, default=0, metavar="N",
                    help="Also capture source-linked items from N recent traces")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"[dry-run] would seed {len(PLAN_ITEMS)} plan-quality + "
              f"{len(WORKER_ITEMS)} worker-quality items")
        for it in PLAN_ITEMS:
            print(f"  plan  | {it['symptom'][:70]}")
        for at, focus, _ in WORKER_ITEMS:
            print(f"  work  | {at} — {focus}")
        return 0

    if not (PK and SK):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — cannot seed datasets.")
        return 1

    from langfuse import get_client
    lf = get_client()

    for name, desc in [
        (PLAN_DATASET, "Plan-quality: does the planner decompose proportionately (no over/under fan-out, no duplication)?"),
        (WORKER_DATASET, "Worker-quality: does one analysis produce a verdict-bearing, cited finding?"),
    ]:
        try:
            lf.create_dataset(name=name, description=desc, metadata={"source": "cluster-health-investigator"})
            print(f"✓ dataset: {name}")
        except Exception as e:
            print(f"• dataset {name}: {e}")

    for i, it in enumerate(PLAN_ITEMS, 1):
        lf.create_dataset_item(
            id=f"chp-{i:02d}", dataset_name=PLAN_DATASET,
            input={"symptom": it["symptom"]},
            expected_output={"criteria": it["criteria"]},
            metadata={"failure_mode": it["failure_mode"], "source": "golden"})
        print(f"  plan  [{i:2}] {it['symptom'][:66]}")

    for i, (atype, focus, must) in enumerate(WORKER_ITEMS, 1):
        lf.create_dataset_item(
            id=f"chw-{i:02d}", dataset_name=WORKER_DATASET,
            input={"analysis_type": atype, "focus": focus},
            expected_output={"must_mention": must},
            metadata={"catalog_desc": CATALOG[atype].description, "source": "golden"})
        print(f"  work  [{i:2}] {atype}")

    if args.from_traces:
        _capture_from_traces(lf, args.from_traces)

    lf.flush()
    print(f"\n✓ {len(PLAN_ITEMS)} plan-quality + {len(WORKER_ITEMS)} worker-quality items seeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
