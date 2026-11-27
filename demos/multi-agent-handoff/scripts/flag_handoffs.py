"""
Loop step 2 → 3: send the handoffs the judge flagged to the "Flagged handoffs" review queue.

    .venv/bin/python scripts/flag_handoffs.py                       # judged in the last 2 hours
    .venv/bin/python scripts/flag_handoffs.py --since 2026-11-21T09:00:00Z
    .venv/bin/python scripts/flag_handoffs.py --simulate-reviewer   # also label them (rehearsal, no human)

What goes in: the concierge's FIRST handoff of each production turn, where the online
`handoff-appropriate` judge said false, plus an equal sample it passed (so the review can
calibrate the judge both ways, not only confirm its flags). In the queue, a reviewer labels
`correct-handoff-target`: the agent it should have gone to.

`--simulate-reviewer` stands in for the human when you rehearse alone: it labels each item
from the scenario's ground truth (the conversation goal's expected first handoff), writes the
score on the handoff observation with the comment "simulated reviewer", and completes the
queue item. Items it cannot label (a later turn, an unknown scenario) stay pending for a person.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import datasets  # noqa: E402
from handoff.lfapi import LangfuseAPIError, api, paginate, ui  # noqa: E402

QUEUE = "Flagged handoffs"
LABEL = "correct-handoff-target"
JUDGE = "handoff-appropriate"


def queue() -> dict:
    q = next((q for q in paginate("/api/public/annotation-queues") if q["name"] == QUEUE), None)
    if q is None:
        sys.exit(f'No "{QUEUE}" queue: run scripts/seed_evals.py --queue first')
    return q


def score_config() -> dict:
    c = next((c for c in paginate("/api/public/score-configs") if c["name"] == LABEL), None)
    if c is None:
        sys.exit(f"No {LABEL!r} score config: run scripts/seed_evals.py --queue first")
    return c


def as_obj(value):
    """v2 observations return input/output as JSON text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


def first_handoffs(since: str) -> list[dict]:
    """One row per production turn: its first concierge handoff, the judge's verdict, and the turn."""
    scores = paginate("/api/public/v3/scores", params={"name": JUDGE, "environment": "production",
                                                      "fromTimestamp": since, "fields": "core,subject,details"})
    verdict = {s["subject"]["id"]: s for s in scores if (s.get("subject") or {}).get("kind") == "observation"}
    window = {"environment": "production", "fromStartTime": since}  # two bulk reads, not one per trace
    roots = {o["traceId"]: o for o in paginate("/api/public/v2/observations", limit=1000, params={
        **window, "name": "support-turn", "fields": "core,basic,metadata,io"})}
    hops_by_trace: dict[str, list[dict]] = {}
    for o in paginate("/api/public/v2/observations", limit=1000,
                      params={**window, "name": "handoff", "fields": "core,basic,metadata"}):
        if (o.get("metadata") or {}).get("handoff_from") == "concierge":
            hops_by_trace.setdefault(o["traceId"], []).append(o)
    rows = []
    for trace_id in sorted({s["subject"]["traceId"] for s in verdict.values()}):
        root = roots.get(trace_id)
        hops = sorted(hops_by_trace.get(trace_id, []), key=lambda o: o.get("startTime") or "")
        if root is None or not hops or hops[0]["id"] not in verdict:
            continue
        h, md = hops[0], root.get("metadata") or {}
        rows.append({"observation_id": h["id"], "trace_id": trace_id, "to": h["metadata"].get("handoff_to"),
                     "kind": h["metadata"].get("handoff_kind"), "judge": bool(verdict[h["id"]]["value"]),
                     "judge_comment": verdict[h["id"]].get("comment") or "", "turn": int(md.get("turn") or 0),
                     "scenario": md.get("scenario"), "customer_id": md.get("customer_id"),
                     "message": as_obj(root.get("input")).get("message", ""), "session_id": root.get("sessionId")})
    return rows


def ground_truth(row: dict) -> str | None:
    """The scenario's expected first handoff, for the simulated reviewer (turn 1 only)."""
    if row["turn"] != 1:
        return None
    for item in datasets.conversation_items() + datasets.routing_items():
        if (item.get("metadata") or {}).get("item") == row["scenario"]:
            return item["expected_output"].get("first_handoff") or "none"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO time (default: 2 hours ago)")
    ap.add_argument("--control", type=float, default=1.0,
                    help="judged-right items to add per flagged one, as a calibration sample (default 1)")
    ap.add_argument("--simulate-reviewer", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="list what would be queued; change nothing")
    a = ap.parse_args()
    since = a.since or (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = queue()
    rows = first_handoffs(since)
    flagged = [r for r in rows if not r["judge"]]
    passed = [r for r in rows if r["judge"]]
    random.Random(a.seed).shuffle(passed)
    control = passed[: round(len(flagged) * a.control)] if flagged else []
    print(f"{len(rows)} turns with a judged concierge handoff since {since}: {len(flagged)} flagged, "
          f"{len(control)} of {len(passed)} passed ones as a control sample")
    if a.dry_run:
        for r in flagged + control:
            print(f"  {'FLAGGED' if not r['judge'] else 'control':7s} {r['scenario'] or '?':16s} turn {r['turn']}  → {r['to']:12s} {r['message'][:60]!r}")
        return
    queued = {i["objectId"]: i for i in paginate(f"/api/public/annotation-queues/{q['id']}/items")}
    cfg = score_config() if a.simulate_reviewer else None
    for r in flagged + control:
        tag = "FLAGGED" if not r["judge"] else "control"
        item = queued.get(r["observation_id"])
        if item is None:
            item = api("POST", f"/api/public/annotation-queues/{q['id']}/items",
                       {"objectId": r["observation_id"], "objectType": "OBSERVATION"})
        print(f"  {tag:7s} {r['scenario'] or '?':16s} turn {r['turn']}  → {r['to']:12s} "
              f"{r['message'][:60]!r}")
        if not a.simulate_reviewer or item.get("status") == "COMPLETED":
            continue
        truth = ground_truth(r)
        if truth is None:
            print("          (left for a person: not a turn-1 scenario the simulator knows)")
            continue
        body = {"traceId": r["trace_id"], "observationId": r["observation_id"], "name": LABEL, "value": truth,
                "dataType": "CATEGORICAL", "configId": cfg["id"], "environment": "production",
                "comment": f"simulated reviewer: scenario {r['scenario']} expects {truth}", "queueId": q["id"]}
        try:
            api("POST", "/api/public/scores", body)
        except LangfuseAPIError as e:
            if e.status != 400:
                raise
            body.pop("queueId")  # older servers reject the field; the queue item still records the review
            api("POST", "/api/public/scores", body)
        api("PATCH", f"/api/public/annotation-queues/{q['id']}/items/{item['id']}", {"status": "COMPLETED"})
        print(f"          labelled {truth}{'  (judge agreed)' if (truth == r['to']) == r['judge'] else '  (judge DISAGREES)'}")
    print(f"\nQueue: {ui('/annotation-queues/' + q['id'])}")


if __name__ == "__main__":
    main()
