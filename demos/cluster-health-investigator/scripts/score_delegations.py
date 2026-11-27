#!/usr/bin/env python3
"""
Per-delegation scoring — the "no native trajectory eval" workaround, demonstrated.

Langfuse's managed judges see ONE observation at a time; they cannot pull sibling
`worker` spans plus the plan into a single evaluation. So the application does the
assembly itself: it reads the run's observations via the public API, and for each
`worker` it asks a judge "given the symptom, the plan, THIS worker's
task/output, and its siblings' tasks — was this delegation appropriate,
non-overlapping, and actually executed?" then pushes the verdict as a
`delegation_quality` score ON that individual worker observation via the Scores API.

Reads use Observations API v2 (the v1 observations list on the stack's 3.x server; see
verify_demo.Langfuse.observations), never the trace endpoints, which Langfuse Cloud no
longer serves. The symptom and the plan come from the run's first `orchestrator`
observation: its input carries the symptom and its output is the plan.

Filter Traces by `delegation_quality < 0.5` to triage individual bad delegations
inside a dynamic fan-out. Exit 1 when no run is found, a run has no workers, or any
read, judge call or score write failed.

Usage (one judge call per worker; the env comes from the root .env):
    python scripts/score_delegations.py                 # most recent run
    python scripts/score_delegations.py --trace-id <id>
    python scripts/score_delegations.py --limit 5       # score the 5 most recent
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_demo import (  # noqa: E402  (stdlib env + read helpers, tested there)
    ApiError, Langfuse, client_from_env, iso, parse_io, prepare_env, recent_trace_ids,
)

MODEL = os.getenv("WORKER_MODEL", "claude-haiku-4-5")
LOOKBACK_DAYS = 7

DELEGATION_RUBRIC = """You judge whether ONE delegation inside a dynamic fan-out was good.

SYMPTOM: {symptom}
FULL PLAN (all tasks the orchestrator chose): {plan}
THIS worker's analysis_type: {this_task}
THIS worker's finding: {this_output}
SIBLING analysis_types dispatched alongside it: {sibling_tasks}

Score 0.0-1.0:
- appropriate given the symptom?
- NON-OVERLAPPING with its siblings (not a duplicate angle)?
- actually executed (the finding is substantive, not an error/empty)?
Return ONLY JSON: {{"score": <0..1>, "reason": "<one sentence>"}}"""


def _window(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"fromStartTime": iso(now - timedelta(days=LOOKBACK_DAYS)), "toStartTime": iso(now + timedelta(minutes=5))}


def recent_runs(lf: Langfuse, limit: int, now: Optional[datetime] = None) -> List[str]:
    """The `limit` most recent runs, by their final `synthesize-diagnosis` observation."""
    return recent_trace_ids(lf.observations(dict(_window(now), name="synthesize-diagnosis")), limit)


def assemble(observations: List[dict]) -> dict:
    """symptom, plan and workers of one run, from its observations."""
    orch = sorted((o for o in observations if o.get("name") == "orchestrator"),
                  key=lambda o: o.get("startTime") or "")
    first = orch[0] if orch else {}
    oin = parse_io(first.get("input"))
    plan = parse_io(first.get("output"))
    workers = []
    for w in (o for o in observations if o.get("name") == "worker"):
        meta = w.get("metadata") or {}
        win = parse_io(w.get("input"))
        win = win if isinstance(win, dict) else {}
        workers.append({"id": w.get("id"), "output": parse_io(w.get("output")),
                        "analysis_type": meta.get("analysis_type") or win.get("analysis_type") or "unknown"})
    return {"symptom": oin.get("symptom", "") if isinstance(oin, dict) else "",
            "plan": plan if plan is not None else {}, "workers": workers}


def _judge(llm, symptom, plan, this_task, this_output, siblings):
    prompt = DELEGATION_RUBRIC.format(
        symptom=symptom, plan=json.dumps(plan)[:1500], this_task=this_task,
        this_output=str(this_output)[:800], sibling_tasks=", ".join(siblings) or "none")
    resp = llm.invoke(prompt)
    text = getattr(resp, "content", str(resp))
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return 0.5, "judge returned no JSON"
    data = json.loads(m.group())
    return float(data.get("score", 0.5)), str(data.get("reason", ""))[:300]


def score_trace(lf: Langfuse, llm, trace_id: str, now: Optional[datetime] = None) -> int:
    """Score every worker of one run. Returns the number of scores written."""
    run = assemble(lf.observations(dict(_window(now), traceId=trace_id), fields="core,basic,io,metadata"))
    if not run["workers"]:
        raise ApiError(None, f"trace {trace_id} has no worker observations")
    scored = 0
    for w in run["workers"]:
        siblings = [x["analysis_type"] for x in run["workers"] if x["id"] != w["id"]]
        score, reason = _judge(llm, run["symptom"], run["plan"], w["analysis_type"], w["output"], siblings)
        lf.post("/api/public/scores", {"traceId": trace_id, "observationId": w["id"],
                                       "name": "delegation_quality", "value": score,
                                       "comment": reason, "dataType": "NUMERIC"})
        print(f"  worker[{w['analysis_type']}] delegation_quality={score:.2f} — {reason[:60]}")
        scored += 1
    return scored


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-id", default=None)
    ap.add_argument("--limit", type=int, default=1, help="Score N most recent runs if no --trace-id")
    args = ap.parse_args(argv)

    ok, message = prepare_env()
    if not ok:
        print(f"Delegations NOT scored: {message}")
        return 1
    lf = client_from_env()

    try:
        ids = [args.trace_id] if args.trace_id else recent_runs(lf, args.limit)
    except ApiError as e:
        print(f"Could not list recent runs: {e}")
        return 1
    if not ids:
        print("No investigate-cluster-symptom runs found. Run main.py first.")
        return 1

    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(model=MODEL, temperature=0, max_tokens=300)

    total, failures = 0, 0
    for tid in ids:
        print(f"Scoring delegations for trace {tid[:12]}…")
        try:
            total += score_trace(lf, llm, tid)
        except (ApiError, ValueError, KeyError) as e:
            failures += 1
            print(f"  ! {e}")
    print(f"\n{'✓' if not failures else '!'} pushed {total} delegation_quality score(s)"
          + (f"; {failures} run(s) failed" if failures else "")
          + ". Filter Traces by delegation_quality < 0.5 to triage bad delegations.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
