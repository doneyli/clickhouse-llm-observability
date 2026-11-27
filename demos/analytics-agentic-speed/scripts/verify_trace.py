"""Read one trace back from Langfuse and check it has the shape the demo promises.

    .venv/bin/python scripts/verify_trace.py <trace_id> [--expect-scenarios N]

Asserts on what Langfuse holds, not on exit codes: one root, every `clickhouse.query`
span nested (no orphan roots from lost OpenTelemetry context), db_* metadata on every
query span, a db_server_ms score per query, and the trace-level scores. Uses the APIs
Langfuse Cloud still serves: Observations v2 and Scores v3.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from explorer.config import load_env  # noqa: E402

TRACE_SCORES = {"scenarios_evaluated", "db_server_ms_total", "db_p95_ms", "db_wait_ms_total",
                "db_time_share", "scenarios_per_minute", "llm_calls", "rounds", "run_cost_usd", "stop_reason"}


def fetch_all(client: httpx.Client, path: str, params: dict) -> list[dict]:
    rows, cursor = [], None
    while True:
        p = dict(params, **({"cursor": cursor} if cursor else {}))
        r = client.get(path, params=p)
        r.raise_for_status()
        j = r.json()
        rows += j.get("data", [])
        cursor = (j.get("meta") or {}).get("cursor")
        if not cursor or not j.get("data"):
            return rows


def fetch_scores(client: httpx.Client, trace_id: str) -> list[dict]:
    """Scores v3: cursor paging; who a score is about lives in `subject` {kind, id}."""
    rows = fetch_all(client, "/api/public/v3/scores",
                     {"traceId": trace_id, "limit": 100, "fields": "core,subject"})
    for s in rows:
        subj = s.get("subject") or {}
        s["observationId"] = subj.get("id") if subj.get("kind") == "observation" else None
    return rows


def check(trace_id: str, expect_scenarios: int | None = None, wait_s: int = 90) -> int:
    load_env()
    client = httpx.Client(base_url=os.environ["LANGFUSE_BASE_URL"], timeout=60,
                          auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]))
    deadline = time.time() + wait_s
    while True:  # ingestion is staged: wait until observations and trace scores have landed
        obs = fetch_all(client, "/api/public/v2/observations",
                        {"traceId": trace_id, "limit": 1000, "fields": "core,basic,usage,metadata"})
        scores = fetch_scores(client, trace_id)
        have = {s["name"] for s in scores}
        if (obs and TRACE_SCORES <= have) or time.time() > deadline:
            break
        time.sleep(5)

    problems: list[str] = []
    names = collections.Counter(o["name"] for o in obs)
    roots = [o for o in obs if not o.get("parentObservationId")]
    queries = [o for o in obs if o["name"] == "clickhouse.query"]
    print(f"observations {len(obs)}: {dict(names)}")
    if len(roots) != 1 or roots[0]["name"] != "analytics-agentic-speed":
        problems.append(f"expected one root 'analytics-agentic-speed', got {[r['name'] for r in roots]}")
    missing_md = [q["id"] for q in queries if "db_server_ms" not in (q.get("metadata") or {})
                  and q.get("level") != "ERROR"]
    if missing_md:
        problems.append(f"{len(missing_md)} query spans without db_server_ms metadata")
    purposes = collections.Counter((q.get("metadata") or {}).get("purpose") for q in queries)
    print(f"query spans by purpose: {dict(purposes)}")
    if expect_scenarios is not None and purposes.get("scenario", 0) != expect_scenarios:
        problems.append(f"expected {expect_scenarios} scenario spans, found {purposes.get('scenario', 0)}")

    by_name = collections.Counter(s["name"] for s in scores)
    print(f"scores: {dict(by_name)}")
    missing = TRACE_SCORES - set(by_name)
    if missing:
        problems.append(f"missing trace scores: {sorted(missing)}")
    ok_queries = sum(1 for q in queries if q.get("level") != "ERROR")
    if by_name.get("db_server_ms", 0) and by_name["db_server_ms"] != ok_queries:
        problems.append(f"db_server_ms scores {by_name['db_server_ms']} != successful query spans {ok_queries}")
    trace_level = {s["name"]: s.get("value") for s in scores if not s.get("observationId")}
    for k in sorted(TRACE_SCORES & set(trace_level)):
        print(f"  {k:22s} {trace_level[k]}")
    server = sorted(float((q.get("metadata") or {}).get("db_server_ms", 0)) for q in queries
                    if "db_server_ms" in (q.get("metadata") or {}))
    if server:
        p = lambda f: server[min(len(server) - 1, int(f * (len(server) - 1)))]  # noqa: E731
        print(f"db_server_ms on spans: p50 {p(0.5):.1f} · p95 {p(0.95):.1f} · max {server[-1]:.1f} (n={len(server)})")
    for pr in problems:
        print("PROBLEM:", pr)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_id")
    ap.add_argument("--expect-scenarios", type=int)
    ap.add_argument("--wait", type=int, default=90)
    a = ap.parse_args()
    sys.exit(check(a.trace_id, a.expect_scenarios, a.wait))
