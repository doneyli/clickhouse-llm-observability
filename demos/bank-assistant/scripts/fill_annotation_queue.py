"""Keep a pending SME review backlog (EVA-06), using production answers.

The positional count is the desired number of PENDING items (default: 20).
Re-runs top up that backlog without changing completed reviews or human scores.
Selection prioritizes low judge scores, policy violations/unknowns and customer
thumbs-down, then samples other answers. --policies selects the P1-P5 review queue.
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
        worst, legacy_worst, thumbs_down = {}, {}, set()
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        policy_names = {p.name for p in POLICIES}
        for batch in cursor_batches("/api/public/v3/scores", fields="subject", fromTimestamp=since,
                                    environment="production", name=",".join([
                                        "faithfulness", "banking-compliance", STATUS_NAME, "user-feedback", *policy_names])):
            for s in batch:
                subj = s.get("subject") or {}
                kind = str(subj.get("kind", "")).lower()
                value = s.get("value")
                if s["name"] == "user-feedback" and kind == "trace" and value in (False, 0):
                    thumbs_down.add(subj.get("id"))
                oid = (subj.get("id") if kind == "observation" else None) or subj.get("observationId") or s.get("observationId")
                if not oid:
                    continue
                priority = None
                if s["name"] in ("faithfulness", "banking-compliance") and isinstance(value, (int, float)):
                    priority = value
                elif s["name"] in policy_names and isinstance(value, str):
                    priority = {"fail": 0, "unknown": 0.25, "minor": 0.5}.get(value)
                elif s["name"] == STATUS_NAME and isinstance(value, str):
                    priority = {"violation": 0, "review-needed": 0.25, "minor-issue": 0.5}.get(value)
                if priority is not None:
                    if subj.get("traceId"):
                        key = (subj["traceId"], oid)
                        worst[key] = min(worst.get(key, 1.0), priority)
                    else:  # compatibility for legacy score subjects lacking traceId
                        legacy_worst[oid] = min(legacy_worst.get(oid, 1.0), priority)
        filters = [{"type": "stringOptions", "column": "name", "operator": "any of", "value": ["northwind-assistant"]},
                   {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
                   {"type": "datetime", "column": "startTime", "operator": ">=", "value": since}]
        # Continue past pages containing already reviewed answers. Include the
        # named assistant inside voice traces; exclude other child operations.
        roots = {}
        for batch in cursor_batches("/api/public/v2/observations", filter=json.dumps(filters), fields="core,basic"):
            roots.update((o["id"], o) for o in batch if o["id"] not in existing
                         and (not o.get("parentObservationId") or o.get("name") == "northwind-assistant"))
            if len(roots) >= needed:
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
