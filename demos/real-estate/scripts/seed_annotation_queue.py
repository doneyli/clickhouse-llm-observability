"""
Create the human-annotation setup for the real-estate project — TWO queues,
because a conversation is not a trace:

  A. "Property Concierge - human review"        items are TRACEs
       reviewer-verdict      CATEGORICAL  approve / minor-issues / reject
       expert-usefulness     NUMERIC      1..5
     One turn at a time, fault-injected traces first.

  B. "Property Concierge - conversation review" items are SESSIONs
       conversation-outcome         CATEGORICAL  resolved / partially-resolved / abandoned
       stated-constraint-respected  BOOLEAN      did a constraint stated ONCE, earlier, hold?
       reference-resolved           BOOLEAN      were "that one" / "the second option" resolved right?
     A whole multi-turn conversation at a time, longest first.

Why a *second* queue rather than more items in the first: a queue item's
`objectType` decides what the reviewer is shown. A TRACE item opens one turn; a
SESSION item opens the whole conversation, in order — the only way a human can
judge failures that exist only across turns (a budget stated in turn 3 and
broken in turn 9 looks fine in every single trace). Mixing the two units in one
queue also forces one score schema to serve both.

Annotation queues are the only route to a session-scoped score besides writing
one yourself (`create_score(session_id=…)`, see `simulate_long_session.py`): no
managed evaluator can target a session, because the server never learns that a
conversation ended. Queue B is therefore where the human gold standard for
multi-turn behaviour comes from — and `stated-constraint-respected` /
`reference-resolved` are deliberately the SAME score names the code evaluators
and the conversation judge emit, so human and machine stay comparable.

Idempotent: re-running reuses existing configs/queues and only adds new items.
Full write-up (score schema rationale, reading the labels back, API gotchas):
CONVERSATION_REVIEW.md.

Run:
    ./.venv/bin/python scripts/seed_annotation_queue.py                    # both queues
    ./.venv/bin/python scripts/seed_annotation_queue.py --only sessions    # conversations only
    ./.venv/bin/python scripts/seed_annotation_queue.py --min-turns 5 --max-sessions 3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.config import (  # noqa: E402
    verify_project, LANGFUSE_HOST, root_observations_by_tag,
    list_sessions, root_observations_by_sessions, observation_io,
    langfuse_api as api,
)
from agent.concierge import TRACE_NAME  # noqa: E402  (the concierge turn's root span)

TRACE_QUEUE_NAME = "Property Concierge - human review"
SESSION_QUEUE_NAME = "Property Concierge - conversation review"


def ensure_score_config(name, data_type, **extra):
    _, existing = api("GET", "/api/public/score-configs?limit=100")
    for c in existing.get("data", []):
        if c.get("name") == name and c.get("dataType") == data_type:
            print(f"  • score-config exists: {name} ({data_type})")
            return c["id"]
    body = {"name": name, "dataType": data_type, **extra}
    status, resp = api("POST", "/api/public/score-configs", body)
    if status in (200, 201):
        print(f"  ✓ created score-config: {name} ({data_type})")
        return resp["id"]
    print(f"  ! score-config {name} -> {status} {resp}")
    return None


def ensure_queue(name, description, score_config_ids):
    _, existing = api("GET", "/api/public/annotation-queues?limit=100")
    for qd in existing.get("data", []):
        if qd.get("name") == name:
            print(f"  • annotation queue exists: {name}")
            return qd["id"]
    body = {"name": name, "description": description,
            "scoreConfigIds": score_config_ids}
    status, resp = api("POST", "/api/public/annotation-queues", body)
    if status in (200, 201):
        print(f"  ✓ created annotation queue: {name}")
        return resp["id"]
    print(f"  ! queue create -> {status} {resp}")
    return None


def queued_object_ids(queue_id):
    """objectIds already in the queue, so re-runs don't duplicate items."""
    _, items = api("GET", f"/api/public/annotation-queues/{queue_id}/items?limit=100")
    return {i.get("objectId") for i in items.get("data", [])}


def add_items(queue_id, object_ids, object_type, max_items, already):
    added = 0
    for oid in object_ids:
        if oid in already or added >= max_items:
            continue
        status, resp = api("POST", f"/api/public/annotation-queues/{queue_id}/items",
                           {"objectId": oid, "objectType": object_type,
                            "status": "PENDING"})
        if status in (200, 201):
            added += 1
        else:
            print(f"  ! add {object_type} {oid} -> {status} {resp}")
    return added


# ------------------------------------------------------------ queue A: traces ---
def recent_realestate_traces(limit):
    # Prefer fault-injected traces (more interesting to review), then fill.
    # Tag lookup goes through v2 observations (the tagged-trace list endpoint is
    # deprecated), so de-dupe on traceId: one root observation per trace.
    picks = []
    for tag in ("fault-demo", "real-estate"):
        try:
            rows = root_observations_by_tag(tag, limit=50)
        except RuntimeError as e:
            print(f"  ! tag lookup '{tag}' failed: {e}")
            continue
        for o in rows:
            if o["traceId"] not in picks:
                picks.append(o["traceId"])
            if len(picks) >= limit:
                return picks
    return picks


def seed_trace_queue(args):
    print("\nQueue A — one turn at a time (TRACE items)…")
    verdict_id = ensure_score_config(
        "reviewer-verdict", "CATEGORICAL",
        description="Human reviewer's overall verdict on the concierge answer.",
        categories=[{"label": "approve", "value": 1},
                    {"label": "minor-issues", "value": 0.5},
                    {"label": "reject", "value": 0}])
    rating_id = ensure_score_config(
        "expert-usefulness", "NUMERIC",
        description="How useful was the answer to the buyer? 1 = useless, 5 = excellent.",
        minValue=1, maxValue=5)

    queue_id = ensure_queue(
        TRACE_QUEUE_NAME,
        "Human QA of Property Concierge answers: overall verdict + a 1-5 usefulness rating.",
        [c for c in (verdict_id, rating_id) if c])
    if not queue_id:
        return False

    already = queued_object_ids(queue_id)
    trace_ids = recent_realestate_traces(args.max_items + len(already))
    added = add_items(queue_id, trace_ids, "TRACE", args.max_items, already)
    print(f"  → {added} new trace(s) queued ({len(already)} already there).")
    return True


# ---------------------------------------------------------- queue B: sessions ---
def multi_turn_sessions(min_turns, want, candidate_pool=40):
    """Sessions with at least `min_turns` turns, longest conversation first.

    Turn count is the number of ROOT observations sharing a session_id (one per
    turn — every turn is its own trace). Sorting by length rather than recency is
    deliberate: a 2-turn session cannot contain a cross-turn failure, so it is
    worthless in this queue, and the smoke-test/verification sessions that
    accumulate in a demo project are all short.

    Only counts turns named TRACE_NAME. A demo project also collects sessions
    from verification scripts (`verify-multimodal` and friends) whose spans are
    not concierge turns at all — long enough to qualify, useless to a reviewer.
    """
    # Returns None (not []) if a lookup FAILED, so the caller doesn't tell the user
    # to go generate conversations when the real problem is the read API — this
    # discovery path needs Cloud/v4 (`v2/observations` 404s on a v3 server, and the
    # RuntimeError carries the version hint that says so).
    try:
        sessions = list_sessions(limit=candidate_pool)
    except RuntimeError as e:
        print(f"  ! session lookup failed: {e}")
        return None
    try:
        rows = root_observations_by_sessions([s["id"] for s in sessions],
                                            fields="core,basic,io")
    except RuntimeError as e:
        print(f"  ! turn lookup failed: {e}")
        return None

    by_session = {}
    for o in rows:
        if o.get("name") != TRACE_NAME:
            continue
        by_session.setdefault(o.get("sessionId"), []).append(o)

    convos = []
    for sid, turns in by_session.items():
        if not sid or len(turns) < min_turns:
            continue
        turns.sort(key=lambda o: o.get("startTime") or "")
        opening = observation_io(turns[0], "input")
        query = opening.get("query", "") if isinstance(opening, dict) else ""
        convos.append({"session_id": sid, "turns": len(turns),
                       "last_seen": turns[-1].get("startTime") or "",
                       "opening": query})
    convos.sort(key=lambda c: (c["turns"], c["last_seen"]), reverse=True)
    return convos[:want]


def seed_session_queue(args):
    print("\nQueue B — a whole conversation at a time (SESSION items)…")
    outcome_id = ensure_score_config(
        "conversation-outcome", "CATEGORICAL",
        description="Did the buyer actually get where they were going, over the "
                    "whole conversation? Judged on the session, not one answer.",
        categories=[{"label": "resolved", "value": 1},
                    {"label": "partially-resolved", "value": 0.5},
                    {"label": "abandoned", "value": 0}])
    # Same names the code evaluators (agent/conversation_scoring.py) and the
    # conversation judge emit per turn — reused on purpose so the human label is
    # a gold standard for the machine's, not a parallel vocabulary.
    constraint_id = ensure_score_config(
        "stated-constraint-respected", "BOOLEAN",
        description="Did every constraint the buyer stated ONCE (budget, city, "
                    "bedrooms) hold for the rest of the conversation?")
    reference_id = ensure_score_config(
        "reference-resolved", "BOOLEAN",
        description="Did follow-ups like 'that one' / 'the second option' resolve "
                    "to the listing the buyer meant?")

    queue_id = ensure_queue(
        SESSION_QUEUE_NAME,
        "Human review of MULTI-TURN conversations: does the agent hold constraints "
        "and resolve references across turns, and did the buyer get an outcome? "
        "Scores land on the session — no automated evaluator can target one.",
        [c for c in (outcome_id, constraint_id, reference_id) if c])
    if not queue_id:
        return False

    already = queued_object_ids(queue_id)
    convos = multi_turn_sessions(args.min_turns, args.max_sessions + len(already))
    if convos is None:
        return False
    if not convos:
        print(f"  ! no session has >= {args.min_turns} turns yet. Generate one:\n"
              f"      ./.venv/bin/python scripts/run_live_traffic.py      # 3-turn session\n"
              f"      ./.venv/bin/python scripts/simulate_long_session.py # 12-turn session")
        return True

    for c in convos:
        mark = "•" if c["session_id"] in already else "+"
        print(f"    {mark} {c['session_id']}  {c['turns']} turns  "
              f"{(c['opening'] or '')[:52]}")
    added = add_items(queue_id, [c["session_id"] for c in convos], "SESSION",
                      args.max_sessions, already)
    print(f"  → {added} new conversation(s) queued ({len(already)} already there).")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=("traces", "sessions", "both"), default="both",
                    help="Which queue to seed (default: both).")
    ap.add_argument("--max-items", type=int, default=6,
                    help="New TRACE items to add to queue A.")
    ap.add_argument("--max-sessions", type=int, default=4,
                    help="New SESSION items to add to queue B.")
    ap.add_argument("--min-turns", type=int, default=3,
                    help="Minimum turns for a session to be worth reviewing.")
    args = ap.parse_args()

    verify_project()
    print("\nSetting up human annotation…")

    ok = True
    if args.only in ("traces", "both"):
        ok = seed_trace_queue(args) and ok
    if args.only in ("sessions", "both"):
        ok = seed_session_queue(args) and ok
    if not ok:
        sys.exit(1)

    print(f"\n✓ Annotation queues ready. View: {LANGFUSE_HOST} > Annotation Queues")
    if args.only in ("traces", "both"):
        print(f"    {TRACE_QUEUE_NAME}        (one turn per item)")
    if args.only in ("sessions", "both"):
        print(f"    {SESSION_QUEUE_NAME}  (one conversation per item)")


if __name__ == "__main__":
    main()
