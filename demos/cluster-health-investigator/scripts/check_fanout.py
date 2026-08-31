#!/usr/bin/env python3
"""
Fan-out monitor via the Langfuse Metrics API — the copy-paste artifact the
Wave-2 tooling was missing for the orchestrator-workers pattern (prose only).

Computes, over a time window:
  - total `worker` observations,
  - total `investigate-cluster-symptom` traces,
  - avg workers per trace (the cost / fan-out proxy),
  - avg total cost per trace.

Exits 1 when avg workers/trace exceeds FANOUT_THRESHOLD — the CI-gate convention
(same as run_experiment.py --ci). Run `--fault overplan` traffic and watch this
breach.

Usage:
    python scripts/check_fanout.py
    FANOUT_THRESHOLD=6 python scripts/check_fanout.py --hours 24
"""

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SK = os.getenv("LANGFUSE_SECRET_KEY", "")
THRESHOLD = float(os.getenv("FANOUT_THRESHOLD", "6"))
TRACE_NAME = "investigate-cluster-symptom"


def _metrics(query: dict):
    url = f"{HOST}/api/public/metrics?query={urllib.parse.quote(json.dumps(query))}"
    token = base64.b64encode(f"{PK}:{SK}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("data", [])


def _first_number(rows) -> float:
    if not rows:
        return 0.0
    row = rows[0]
    for v in row.values():
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    if not (PK and SK):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY required.")
        return 1

    frm = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    to = datetime.now(timezone.utc).isoformat()

    workers = _first_number(_metrics({
        "view": "observations",
        "metrics": [{"measure": "count", "aggregation": "count"}],
        "filters": [{"column": "name", "operator": "=", "value": "worker", "type": "string"}],
        "fromTimestamp": frm, "toTimestamp": to,
    }))
    traces = _first_number(_metrics({
        "view": "traces",
        "metrics": [{"measure": "count", "aggregation": "count"}],
        "filters": [{"column": "name", "operator": "=", "value": TRACE_NAME, "type": "string"}],
        "fromTimestamp": frm, "toTimestamp": to,
    }))
    avg_cost = _first_number(_metrics({
        "view": "traces",
        "metrics": [{"measure": "totalCost", "aggregation": "avg"}],
        "filters": [{"column": "name", "operator": "=", "value": TRACE_NAME, "type": "string"}],
        "fromTimestamp": frm, "toTimestamp": to,
    }))

    avg_workers = (workers / traces) if traces else 0.0
    print(f"Window: last {args.hours}h")
    print(f"  traces ({TRACE_NAME}):  {int(traces)}")
    print(f"  worker observations:      {int(workers)}")
    print(f"  avg workers / trace:      {avg_workers:.2f}   (threshold {THRESHOLD})")
    print(f"  avg total cost / trace:   ${avg_cost:.4f}")

    if avg_workers > THRESHOLD:
        print(f"\n✗ FAN-OUT BREACH: {avg_workers:.2f} > {THRESHOLD}")
        return 1
    print("\n✓ fan-out within budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
