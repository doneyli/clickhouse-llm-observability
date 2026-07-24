"""
Seed the two Langfuse Monitors for the tuner (the load-bearing Monitor stage).

  1. "query-tuner runaway cost" — max totalCost per `tune-clickhouse-query` trace
     crosses an absolute-dollar threshold. Normal runs ~$0.05-0.35 (below warn);
     the --runaway run blows through the alert threshold → alert fires on stage.
  2. "query-tuner turn count"  — max of the NUMERIC `turns_used` score (app-side
     instrumentation, because "turns" is not a native measure). The
     self-assessment-failed / backstop-did-the-stopping fleet signal.

Monitors are a Langfuse v4+ feature. This stack pins Langfuse v3, which has NO
Monitors API — so this script attempts the API opportunistically and otherwise
PRINTS THE EXACT UI FIELD VALUES to paste into Monitors → New Monitor. Either
path leaves you with the same two monitors. Idempotent, non-fatal.

    python scripts/seed_monitors.py

NOTE (calibration): thresholds below are the spec defaults. Do one calibration
pass against real Sonnet pricing (a dozen runs) and bake the final numbers in.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import langfuse_config as lf

HOST = os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000").rstrip("/")

MONITORS = [
    {
        "name": "query-tuner runaway cost",
        "dataSource": "Traces",
        "metric": {"measure": "totalCost", "aggregation": "max"},
        "filters": [{"column": "traceName", "operator": "=", "value": "tune-clickhouse-query"}],
        "operator": ">",
        "alertThreshold": 1.00,
        "warningThreshold": 0.40,
        "window": "1 hour",
        "renotify": "every 30 minutes",
    },
    {
        "name": "query-tuner turn count",
        "dataSource": "Scores (numeric)",
        "metric": {"measure": "turns_used", "aggregation": "max"},
        "filters": [{"column": "scoreName", "operator": "=", "value": "turns_used"},
                    {"column": "traceName", "operator": "=", "value": "tune-clickhouse-query"}],
        "operator": ">",
        "alertThreshold": 12,
        "warningThreshold": 8,
        "window": "1 hour",
        "renotify": "off",
    },
]


def _auth() -> str:
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    return base64.b64encode(f"{pk}:{sk}".encode()).decode()


def _api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{HOST}{path}", data=data, method=method,
                                 headers={"Authorization": f"Basic {_auth()}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read(300)
    except Exception as e:  # noqa: BLE001
        return None, str(e).encode()


def _print_ui_fields() -> None:
    print("\nCreate these two Monitors in the Langfuse UI (Monitors → New Monitor):")
    for m in MONITORS:
        print(f"\n  ── {m['name']} ──")
        print(f"     Data source     : {m['dataSource']}")
        print(f"     Metric          : {m['metric']['aggregation']} {m['metric']['measure']}")
        for f in m["filters"]:
            print(f"     Filter          : {f['column']} {f['operator']} {f['value']}")
        print(f"     Operator        : {m['operator']}")
        print(f"     Alert threshold : {m['alertThreshold']}")
        print(f"     Warn threshold  : {m['warningThreshold']}")
        print(f"     Window          : {m['window']}")
        print(f"     Renotify        : {m['renotify']}")


def main() -> int:
    if not lf.is_langfuse_enabled():
        print("Langfuse keys not set — skipping monitor seeding.")
        _print_ui_fields()
        return 0

    # Monitors have no documented public write API and are v4+ only. Probe, then
    # fall back to printing UI fields (the reliable path on this v3 stack).
    status, _ = _api("GET", "/api/public/monitors")
    if status == 200:
        print("Monitors API detected — creating monitors idempotently by name…")
        for m in MONITORS:
            st, body = _api("POST", "/api/public/monitors", m)
            if st in (200, 201):
                print(f"  + {m['name']} created")
            elif st in (409,):
                print(f"  = {m['name']} already exists")
            else:
                print(f"  ! {m['name']} not created via API (status={st}); use the UI fields below.")
                _print_ui_fields()
                break
    else:
        print(f"No Monitors write API on this Langfuse ({HOST}, status={status}). "
              "Monitors are a v4+ feature; this stack pins v3.")
        _print_ui_fields()
    print("\n(Once created, --runaway will cross 'query-tuner runaway cost' → alert.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
