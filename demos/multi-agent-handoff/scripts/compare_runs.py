"""
Compare experiment runs on `handoff-routing`, read back from Langfuse (question 3).

    .venv/bin/python scripts/compare_runs.py --since 2026-11-20T04:00:00Z
    .venv/bin/python scripts/compare_runs.py --since ... --markdown docs/TOPOLOGY_COMPARISON.md

Per run: items, the mean of each score, model cost per item (summed from the run's
generations, as Langfuse priced them), and latency per item. Everything comes from the
server (v2 observations, v3 scores), so the table is what the Langfuse UI shows.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config  # noqa: E402

SCORES = ["resolution-correct", "credit-amount-correct", "outage-verified-first", "reply-mentions-facts",
          "handoff-target-correct", "path-match", "handoff-context-complete", "unnecessary-handoff"]


def _get(path: str, params: dict) -> list[dict]:
    auth = (config.setting("LANGFUSE_PUBLIC_KEY"), config.setting("LANGFUSE_SECRET_KEY"))
    rows, cursor = [], None
    while True:
        q = {**params, **({"cursor": cursor} if cursor else {})}
        r = httpx.get(f"{config.LANGFUSE_BASE_URL}{path}", params=q, auth=auth, timeout=60)
        r.raise_for_status()
        body = r.json()
        rows += body["data"]
        cursor = (body.get("meta") or {}).get("cursor")
        if not cursor or not body["data"]:
            return rows


def collect(since: str, until: str | None = None) -> dict:
    window = {"fromStartTime": since, **({"toStartTime": until} if until else {})}
    roots = _get("/api/public/v2/observations",
                 {"name": "experiment-item-run", "fields": "core,basic,metadata", "limit": 100, **window})
    run_of = {}
    for o in roots:
        md = o.get("metadata") or {}
        if md.get("experiment_name") == "handoff-routing":
            run_of[o["traceId"]] = (md.get("experiment_run_name") or "?", o.get("latency"))
    gens = _get("/api/public/v2/observations",
                {"type": "GENERATION", "fields": "core,basic,usage", "limit": 100, **window})
    cost = defaultdict(float)
    for g in gens:
        if g["traceId"] in run_of:
            cost[g["traceId"]] += float(g.get("totalCost") or 0)
    scores = defaultdict(lambda: defaultdict(list))
    for name in SCORES:
        for s in _get("/api/public/v3/scores", {"name": name, "fromTimestamp": since, "fields": "core,subject",
                                                 "limit": 100}):
            tid = (s.get("subject") or {}).get("traceId")
            if tid in run_of and isinstance(s.get("value"), (int, float)):
                scores[run_of[tid][0]][name].append(float(s["value"]))
    runs: dict = defaultdict(lambda: {"items": 0, "cost": [], "latency": []})
    for tid, (run, latency) in run_of.items():
        runs[run]["items"] += 1
        runs[run]["cost"].append(cost[tid])
        if latency is not None:
            runs[run]["latency"].append(float(latency))
    for run, by_name in scores.items():
        runs[run]["scores"] = {n: statistics.mean(v) for n, v in by_name.items()}
    return dict(runs)


def _p95(vals: list[float]) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))] if vals else 0.0


def table(runs: dict) -> str:
    cols = ["Run", "Items", *SCORES, "Cost / item ($)", "Mean latency (s)", "p95 latency (s)"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for run in sorted(runs):
        r = runs[run]
        sc = r.get("scores", {})
        cells = [f"`{run}`", str(r["items"])] + [f"{sc[n]:.2f}" if n in sc else "—" for n in SCORES]
        cells += [f"{statistics.mean(r['cost']):.4f}" if r["cost"] else "—",
                  f"{statistics.mean(r['latency']):.1f}" if r["latency"] else "—",
                  f"{_p95(r['latency']):.1f}" if r["latency"] else "—"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="ISO time; runs started after it")
    ap.add_argument("--until")
    ap.add_argument("--markdown", type=Path)
    args = ap.parse_args()
    from datetime import datetime, timezone

    head = (f"Read back from `{config.LANGFUSE_BASE_URL}` on {datetime.now(timezone.utc):%Y-%m-%d} "
            f"(`scripts/compare_runs.py --since {args.since}`): runs on `handoff-routing`, one row per run. "
            "Scores are means over the items they apply to; cost is the model cost Langfuse computed for "
            "the run's generations.\n\n")
    md = head + table(collect(args.since, args.until))
    print(md)
    if args.markdown:
        args.markdown.write_text(md)


if __name__ == "__main__":
    main()
