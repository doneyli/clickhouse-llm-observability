"""
Seed the two Langfuse Monitors for the tuner (the load-bearing Monitor stage).

  1. "query-tuner runaway cost" — max totalCost per `tune-clickhouse-query` trace
     crosses an absolute-dollar threshold. Normal runs ~$0.05-0.35 (below warn);
     the --runaway run blows through the alert threshold → alert fires on stage.
  2. "query-tuner turn count"  — max of the NUMERIC `turns_used` score (app-side
     instrumentation, because "turns" is not a native measure). The
     self-assessment-failed / backstop-did-the-stopping fleet signal.

Monitors are a Langfuse v4+ feature, and the current public API reference documents no
Monitors endpoint. This script reads the server version from /api/public/health first:

  * v3 (this stack pins 3.221.1): there are no Monitors to create. It says so, prints the
    field values for a v4+ server, and exits 0. Act 4 runs on its trace-cost Fallback.
  * v4+: it probes /api/public/monitors. With an API it creates the missing monitors by
    name and reports the existing ones unchanged (by name only; their definitions are not
    compared). Without one (404) it prints the UI fields to paste into Monitors → New Monitor.

Exit 1 when the keys are missing, the health read fails, or a monitor write fails.

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
    print("\nMonitors → New Monitor fields (Langfuse v4+ only):")
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


def server_major(version: str) -> int:
    """Major version from a health response such as '3.221.1' or 'v4.30.0'; 0 if unknown."""
    digits = (version or "").lstrip("vV").split(".")[0]
    return int(digits) if digits.isdigit() else 0


def main() -> int:
    if not lf.is_langfuse_enabled():
        print("ERROR: Langfuse keys not set — monitors not checked.")
        return 1

    status, body = _api("GET", "/api/public/health")
    try:
        version = json.loads(body).get("version", "") if status == 200 else ""
    except ValueError:
        version = ""
    if status != 200 or not version:
        print(f"ERROR: could not read the Langfuse version from {HOST}/api/public/health (status={status}).")
        return 1
    if server_major(version) < 4:
        print(f"Langfuse {version} at {HOST} has no Monitors (a v4+ feature): nothing to create. "
              "Act 4 runs on its trace-cost Fallback on this server.")
        print("For a v4+ server, these are the two monitors:")
        _print_ui_fields()
        return 0

    status, body = _api("GET", "/api/public/monitors")
    if status == 404:
        print(f"Langfuse {version} has no public Monitors API: create them by hand.")
        _print_ui_fields()
        return 0
    if status != 200:
        print(f"ERROR: GET /api/public/monitors returned status={status}: {body[:200]!r}")
        return 1
    try:
        data = json.loads(body)
        have = {m.get("name") for m in (data.get("data") if isinstance(data, dict) else data) or []}
    except (ValueError, AttributeError):
        print("ERROR: unreadable Monitors list response.")
        return 1
    rc = 0
    for m in MONITORS:
        if m["name"] in have:
            print(f"  = {m['name']} unchanged (exists by name; definition not compared)")
            continue
        st, out = _api("POST", "/api/public/monitors", m)
        if st in (200, 201):
            print(f"  + {m['name']} created")
        else:
            print(f"  ! {m['name']} create FAILED (status={st}): {out[:200]!r}")
            rc = 1
    if rc:
        _print_ui_fields()
    print("\n(Once created, --runaway will cross 'query-tuner runaway cost' → alert.)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
