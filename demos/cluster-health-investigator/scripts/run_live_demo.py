#!/usr/bin/env python3
"""
Run all DEMO_SYMPTOMS (one session) to seed varied-shape traces on a fresh stack.

No synthetic-history generator is needed at 50k scale — the live investigation
target (`langfuse-clickhouse`) is already busy because the whole stack runs on
it, so `system.query_log` / `system.parts` / `system.merges` contain real data.

This gives the Agent Graph, the worker_count score chart, and the fan-out
distribution varied data with zero extra seeding. Wired into seed_all.py behind
`--with-traces`.

Usage:
    python scripts/run_live_demo.py
    python scripts/run_live_demo.py --with-fault   # also run one --fault overplan
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph import create_investigator  # noqa: E402
import langfuse_config as lf  # noqa: E402

# 8 symptoms (superset of main.py's headline 6) — matches the plan-quality
# golden dataset so captured traces line up with the seeded criteria.
SYMPTOMS = [
    "One Grafana dashboard query got slow this afternoon; everything else feels fine.",
    "We're seeing occasional query exceptions in the last hour but throughput looks ok.",
    "Mutations look stuck on one table and ALTERs never seem to finish.",
    "Ingest latency crept up over the last day and a couple of merges seem to hang.",
    "Inserts got slow after last night's deploy, CPU is pinned and disk is filling.",
    "The whole cluster feels unhealthy since yesterday — slow, erroring, and bloated.",
    "Is my cluster healthy?",
    "Queries that touch one big table are slow and it keeps growing.",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-fault", action="store_true", help="Also run one --fault overplan")
    args = ap.parse_args()

    inv = create_investigator()
    session = lf.new_session_id()
    print(f"Seeding {len(SYMPTOMS)} varied-shape traces (session {session})...")
    for i, s in enumerate(SYMPTOMS, 1):
        try:
            res = inv.run(s, session_id=session)
            print(f"  [{i}/{len(SYMPTOMS)}] worker×{res['workers_spawned']} "
                  f"rounds={res['rounds']} — {s[:56]}")
        except Exception as e:
            print(f"  [{i}] error: {e}")

    if args.with_fault:
        try:
            res = inv.run("Full health sweep for the demo.", session_id=session, fault="overplan")
            print(f"  [fault:overplan] worker×{res['workers_spawned']} (should hit the cap)")
        except Exception as e:
            print(f"  [fault] error: {e}")

    print("Done. Open Langfuse → Traces → Agent Graph (Aggregated) to see worker (N/N) vary.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
