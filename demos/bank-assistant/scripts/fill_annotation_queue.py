"""Keep a pending SME review backlog (EVA-06), using production answers.

The positional count is the desired number of PENDING items (default: 20).
Re-runs top up that backlog without changing completed reviews or human scores.
Selection, worst first, over the last WINDOW_HOURS of production answers:
customer thumbs-down (`user-feedback` = 0, a trace score, mapped to that trace's
assistant observation), low faithfulness / compliance scores and P1-P5 policy
violations or unknowns, then a random sample. The scan of observations continues
until every flagged answer in the window has been seen, not just the newest page.
Items are the assistant's observations, the same objects the managed judges score,
so each SME label forms a matched pair with a judge score (Scores → Analytics).
--policies selects the P1-P5 review queue.
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402
from northwind.compliance import POLICIES, STATUS_NAME  # noqa: E402

QUEUE = "SME review — assistant answers"
JUDGE_SCORES = ("faithfulness", "banking-compliance")
FEEDBACK_SCORE = "user-feedback"
WINDOW_HOURS = 24  # production answers this recent are candidates


def page_all(path):
    """Queues and their items use page/totalPages pagination."""
    items, page = [], 1
    while True:
        response = config.api("GET", path, params={"limit": 100, "page": page})
        items.extend(response.get("data") or [])
        if page >= ((response.get("meta") or {}).get("totalPages") or 1):
            return items
        page += 1


def cursor_batches(path, **params):
    """Scores and observations use cursor pagination."""
    cursor = None
    while True:
        response = config.api("GET", path, params={
            "limit": 100, **params, **({"cursor": cursor} if cursor else {})})
        batch = response.get("data") or []
        yield batch
        meta = response.get("meta") or {}
        next_cursor = meta.get("cursor") or meta.get("nextCursor")
        if not next_cursor or not batch:
            return
        if next_cursor == cursor:
            raise RuntimeError("Annotation selection pagination did not advance")
        cursor = next_cursor


def scores_to_rank(since: str):
    """Priorities from the scores that rank, in one read (not every score of every turn):
    ({(trace id, observation id): priority}, {observation id: priority} for legacy subjects
    without a trace id, {trace ids with a thumbs-down}). 0 = worst, 1 = nothing flagged."""
    worst, legacy_worst, thumbs_down = {}, {}, set()
    policy_names = {p.name for p in POLICIES}
    for batch in cursor_batches("/api/public/v3/scores", fields="subject", fromTimestamp=since,
                                environment="production",
                                name=",".join([*JUDGE_SCORES, STATUS_NAME, FEEDBACK_SCORE, *policy_names])):
        for s in batch:
            subj = s.get("subject") or {}
            kind = str(subj.get("kind", "")).lower()
            value = s.get("value")
            if s.get("name") == FEEDBACK_SCORE:  # a trace score: the customer rated the whole turn
                tid = subj.get("traceId") or (subj.get("id") if kind == "trace" else None) or s.get("traceId")
                if tid and value in (False, 0):
                    thumbs_down.add(tid)
                continue
            oid = (subj.get("id") if kind == "observation" else None) or subj.get("observationId") \
                or s.get("observationId")
            if not oid:
                continue
            priority = None
            if s.get("name") in JUDGE_SCORES and isinstance(value, (int, float)):
                priority = value
            elif s.get("name") in policy_names and isinstance(value, str):
                priority = {"fail": 0, "unknown": 0.25, "minor": 0.5}.get(value)
            elif s.get("name") == STATUS_NAME and isinstance(value, str):
                priority = {"violation": 0, "review-needed": 0.25, "minor-issue": 0.5}.get(value)
            if priority is None:
                continue
            if subj.get("traceId"):
                key = (subj["traceId"], oid)
                worst[key] = min(worst.get(key, 1.0), priority)
            else:  # compatibility for legacy score subjects lacking traceId
                legacy_worst[oid] = min(legacy_worst.get(oid, 1.0), priority)
    return worst, legacy_worst, thumbs_down


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("count", nargs="?", type=int, default=20,
                        help="desired pending backlog (default: 20)")
    parser.add_argument("--policies", action="store_true", help="Fill the separate P1-P5 conduct review queue")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("count must be at least 1")
    queue = "SME review — conduct policies" if args.policies else QUEUE
    q = next((q for q in page_all("/api/public/annotation-queues") if q["name"] == queue), None)
    if q is None:
        print("SME review queue is missing. Run scripts/seed_evals.py first.", file=sys.stderr)
        return 1
    path = f"/api/public/annotation-queues/{q['id']}/items"
    items = page_all(path)
    existing = {i["objectId"] for i in items if i["objectType"] == "OBSERVATION"}
    pending = sum(i.get("status") == "PENDING" for i in items)
    needed = max(0, args.count - pending)
    added = 0
    if needed:
        since = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()
        worst, legacy_worst, thumbs_down = scores_to_rank(since)
        filters = [{"type": "stringOptions", "column": "name", "operator": "any of", "value": ["northwind-assistant"]},
                   {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
                   {"type": "datetime", "column": "startTime", "operator": ">=", "value": since}]
        # Continue past pages of already reviewed answers, and until every flagged answer
        # has been seen — the worst may be older than the newest page. Include the named
        # assistant inside voice traces; exclude other child operations.
        flagged = ({oid for (_, oid), v in worst.items() if v < 1.0}
                   | {oid for oid, v in legacy_worst.items() if v < 1.0}) - existing
        down = set(thumbs_down)
        roots = {}
        for batch in cursor_batches("/api/public/v2/observations", filter=json.dumps(filters), fields="core,basic"):
            for o in batch:
                flagged.discard(o["id"])
                down.discard(o.get("traceId"))
                if o["id"] not in existing and (not o.get("parentObservationId")
                                                or o.get("name") == "northwind-assistant"):
                    roots[o["id"]] = o
            if len(roots) >= needed and not flagged and not down:
                break
        ranked = sorted(roots, key=lambda oid: (min(
            worst.get((roots[oid].get("traceId"), oid), legacy_worst.get(oid, 1.0)),
            0.0 if roots[oid].get("traceId") in thumbs_down else 1.0), random.random()))
        for oid in ranked[:needed]:
            config.api("POST", path, {"objectId": oid, "objectType": "OBSERVATION", "status": "PENDING"})
            added += 1
        items = page_all(path)
    pending = sum(i.get("status") == "PENDING" for i in items)
    completed = sum(i.get("status") == "COMPLETED" for i in items)
    print(f"+ {added} items → queue '{queue}'  ({pending} pending / {completed} completed / {len(items)} total)")
    print(f"Annotate: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/annotation-queues/{q['id']}")
    if pending < args.count:
        print(f"Pending backlog shortfall: {pending}/{args.count}. Generate fresh production answers with "
              "scripts/generate_traffic.py --scenario all, then re-run this command.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
