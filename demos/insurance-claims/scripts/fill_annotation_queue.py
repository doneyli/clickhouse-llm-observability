"""
Fill the examiner QA annotation queue, worst first, with the SOP-rules step of production cases (death claims
and annuity service requests). Each item is the `sop-rules` observation, so the reviewer sees the case file,
the SOP text the model saw and the rules it returned — and labels `sme-rule-set-complete` + `sme-failure-mode`.

Priority: the critical-rule guard fired → the rule-completeness judge said "incomplete" → the examiner
overrode → canary traffic → a random sample of the rest.

--stand-in N labels the first N items with STAND-IN SME labels computed from the codified answer key (the
oracle), each with a comment saying so, and marks them completed — so the judge-vs-human view has data for the
demo. In a POC, examiners label these; the stand-in is never presented as a human label.

    .venv/bin/python scripts/fill_annotation_queue.py --n 30 --stand-in 16
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import claimsdb, config, evals, lfread  # noqa: E402

QUEUE = "Claims examiner QA — rule completeness"
SINCE = "2026-10-01T00:00:00Z"


def score_map(name: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for s in lfread.paged("/api/public/v3/scores", {"name": name, "fields": "core,subject", "fromTimestamp": SINCE}):
        tid = (s.get("subject") or {}).get("traceId")
        if tid:
            out.setdefault(tid, []).append(s.get("value"))
    return out


def truthy(vals) -> bool:
    return any(v in (1, 1.0, True) for v in vals or [])


def falsy(vals) -> bool:
    return any(v in (0, 0.0, False) for v in vals or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--stand-in", type=int, default=16)
    ap.add_argument("--random-labelled", type=int, default=0,
                    help="also add N random ordinary cases WITH stand-in labels (calibration needs both outcomes)")
    a = ap.parse_args()
    queues = {q["name"]: q for q in config.api("GET", "/api/public/annotation-queues", params={"limit": 50})["data"]}
    if QUEUE not in queues:
        print("queue missing — run scripts/seed_evals.py first")
        return 1
    q = queues[QUEUE]
    configs = {c["name"]: c for c in config.api("GET", "/api/public/score-configs", params={"limit": 100})["data"]}

    f = [{"type": "stringOptions", "column": "name", "operator": "any of", "value": ["sop-rules"]},
         {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
         {"type": "stringOptions", "column": "traceName", "operator": "any of",
          "value": ["claims-orchestrator", "annuity-service"]},
         {"type": "datetime", "column": "startTime", "operator": ">=", "value": SINCE}]
    obs = lfread.paged("/api/public/v2/observations", {"filter": json.dumps(f), "fields": "core,basic,io,metadata"})
    guard, judge, override = score_map("guard-triggered"), score_map("rule-completeness"), score_map("examiner-override")

    def prio(o) -> tuple:
        t = o["traceId"]
        return (not truthy(guard.get(t)), not falsy(judge.get(t)), not truthy(override.get(t)),
                "canary" not in (o.get("tags") or []), random.Random(t).random())

    existing = {i["objectId"] for i in config.api(
        "GET", f"/api/public/annotation-queues/{q['id']}/items", params={"limit": 100}).get("data") or []}
    ranked = sorted(obs, key=prio)
    picked = ranked[:a.n]
    if a.random_labelled:
        rest = [o for o in ranked[a.n:] if o["id"] not in existing]
        picked = random.Random(7).sample(rest, min(a.random_labelled, len(rest)))
        a.stand_in = len(picked)
    added, labelled = 0, 0
    for i, o in enumerate(picked):
        if o["id"] in existing:
            continue
        item = config.api("POST", f"/api/public/annotation-queues/{q['id']}/items",
                          {"objectId": o["id"], "objectType": "OBSERVATION"})
        added += 1
        if i >= a.stand_in:
            continue
        meta = o.get("metadata") or {}
        case_id = meta.get("claim_id") or meta.get("request_id")
        try:
            case = claimsdb.get(case_id) if str(case_id).startswith("CLM") else claimsdb.get_service(case_id)
        except KeyError:
            continue
        out = o.get("output")
        out = json.loads(out) if isinstance(out, str) else (out or {})
        got = evals.cited_ids(out.get("rules") or [])
        want = set(case["expected"]["rule_ids"])
        missed, extra = sorted(want - got), sorted(got - want)
        complete = not missed
        mode = "none" if complete and not extra else ("missed-rule" if missed else "wrong-rule")
        note = (f"STAND-IN SME label from the codified answer key (demo, not a human label). "
                f"missed: {', '.join(missed) or '-'}; not applicable: {', '.join(extra) or '-'}")
        config.get_langfuse().create_score(trace_id=o["traceId"], observation_id=o["id"], name="sme-rule-set-complete",
                                           value=1 if complete else 0, data_type="BOOLEAN", comment=note,
                                           config_id=configs["sme-rule-set-complete"]["id"])
        config.get_langfuse().create_score(trace_id=o["traceId"], observation_id=o["id"], name="sme-failure-mode",
                                           value=mode, data_type="CATEGORICAL", comment=note,
                                           config_id=configs["sme-failure-mode"]["id"])
        config.api("PATCH", f"/api/public/annotation-queues/{q['id']}/items/{item['id']}", {"status": "COMPLETED"})
        labelled += 1
    config.flush()
    print(f"queue '{QUEUE}': +{added} items ({labelled} with stand-in SME labels, {added - labelled} pending)")
    print(f"  {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/annotation-queues/{q['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
