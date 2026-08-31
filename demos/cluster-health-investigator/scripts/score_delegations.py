#!/usr/bin/env python3
"""
Per-delegation scoring — the "no native trajectory eval" workaround, demonstrated.

Langfuse's managed judges see ONE observation at a time; they cannot pull sibling
`worker` spans plus the plan into a single evaluation. So the application does the
assembly itself: it walks the trace's full observation tree via the public API,
and for each `worker` it asks a judge "given the symptom, the plan, THIS worker's
task/output, and its siblings' tasks — was this delegation appropriate,
non-overlapping, and actually executed?" then pushes the verdict as a
`delegation_quality` score ON that individual worker observation via the Scores API.

Filter Traces by `delegation_quality < 0.5` to triage individual bad delegations
inside a dynamic fan-out.

Usage:
    python scripts/score_delegations.py                 # most recent trace
    python scripts/score_delegations.py --trace-id <id>
    python scripts/score_delegations.py --limit 5       # score the 5 most recent
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.request

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SK = os.getenv("LANGFUSE_SECRET_KEY", "")
MODEL = os.getenv("WORKER_MODEL", "claude-haiku-4-5")

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


def _auth():
    return {"Authorization": f"Basic {base64.b64encode(f'{PK}:{SK}'.encode()).decode()}",
            "Content-Type": "application/json"}


def _get(path: str):
    req = urllib.request.Request(f"{HOST}{path}", headers=_auth())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _post_score(trace_id, observation_id, name, value, comment):
    body = json.dumps({"traceId": trace_id, "observationId": observation_id,
                       "name": name, "value": value, "comment": comment,
                       "dataType": "NUMERIC"}).encode()
    req = urllib.request.Request(f"{HOST}/api/public/scores", data=body,
                                 headers=_auth(), method="POST")
    urllib.request.urlopen(req, timeout=15).read()


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


def _score_trace(llm, trace_id: str) -> int:
    full = _get(f"/api/public/traces/{trace_id}")
    obs = full.get("observations", [])
    tin = full.get("input") if isinstance(full.get("input"), dict) else {}
    symptom = tin.get("symptom", "")
    orch = next((o for o in obs if o.get("name") == "orchestrator"), None)
    plan = (orch or {}).get("output", {})
    workers = [o for o in obs if o.get("name") == "worker"]
    if not workers:
        print(f"  trace {trace_id[:12]}… has no worker observations — skipped")
        return 0
    scored = 0
    for w in workers:
        this_task = (w.get("metadata") or {}).get("analysis_type", "unknown")
        siblings = [(x.get("metadata") or {}).get("analysis_type", "?")
                    for x in workers if x.get("id") != w.get("id")]
        score, reason = _judge(llm, symptom, plan, this_task, w.get("output"), siblings)
        _post_score(trace_id, w["id"], "delegation_quality", score, reason)
        print(f"  worker[{this_task}] delegation_quality={score:.2f} — {reason[:60]}")
        scored += 1
    return scored


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-id", default=None)
    ap.add_argument("--limit", type=int, default=1, help="Score N most recent traces if no --trace-id")
    args = ap.parse_args()

    if not (PK and SK):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY required.")
        return 1

    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(model=MODEL, temperature=0, max_tokens=300)

    if args.trace_id:
        ids = [args.trace_id]
    else:
        traces = _get(f"/api/public/traces?name=investigate-cluster-symptom&limit={args.limit}").get("data", [])
        ids = [t["id"] for t in traces]
        if not ids:
            print("No investigate-cluster-symptom traces found. Run main.py first.")
            return 1

    total = 0
    for tid in ids:
        print(f"Scoring delegations for trace {tid[:12]}…")
        total += _score_trace(llm, tid)
    print(f"\n✓ pushed {total} delegation_quality score(s). "
          f"Filter Traces by delegation_quality < 0.5 to triage bad delegations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
