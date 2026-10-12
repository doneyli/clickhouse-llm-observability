"""Keep a pending SME review backlog (EVA-06), using production answers.

The positional count is the desired number of PENDING items (default: 20).
Re-runs top up that backlog without changing completed reviews or human scores.
Selection: lowest faithfulness / compliance judge scores first, then a random
sample. Items are ROOT observations, the same objects the managed judges score,
so each SME label forms a matched pair with a judge score (Scores → Analytics).
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

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
        cursor = meta.get("cursor") or meta.get("nextCursor")
        if not cursor or not batch:
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("count", nargs="?", type=int, default=20,
                        help="desired pending backlog (default: 20)")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("count must be at least 1")

    q = next((q for q in page_all("/api/public/annotation-queues") if q["name"] == QUEUE), None)
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
        worst = {}
        for batch in cursor_batches("/api/public/v3/scores", fields="core,subject"):
            for s in batch:
                subj = s.get("subject") or {}
                oid = (subj.get("id") if subj.get("kind") in ("observation", "OBSERVATION") else None) \
                    or subj.get("observationId") or s.get("observationId")
                if (oid and s["name"] in ("faithfulness", "banking-compliance")
                        and isinstance(s.get("value"), (int, float))):
                    worst[oid] = min(worst.get(oid, 1.0), s["value"])
        # Continue past the newest page when its answers have already been reviewed.
        roots = set()
        for batch in cursor_batches("/api/public/v2/observations", name="northwind-assistant",
                                    environment="production", fields="core"):
            roots.update(o["id"] for o in batch if not o.get("parentObservationId")
                         and o["id"] not in existing)
            if len(roots) >= needed:
                break
        ranked = sorted(roots, key=lambda oid: (worst.get(oid, 1.0), random.random()))
        for oid in ranked[:needed]:
            config.api("POST", path, {"objectId": oid, "objectType": "OBSERVATION", "status": "PENDING"})
            added += 1
        items = page_all(path)

    pending = sum(i.get("status") == "PENDING" for i in items)
    completed = sum(i.get("status") == "COMPLETED" for i in items)
    print(f"+ {added} items → queue '{QUEUE}'  ({pending} pending / {completed} completed / {len(items)} total)")
    print(f"Annotate: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/annotation-queues/{q['id']}")
    if pending < args.count:
        print(f"Pending backlog shortfall: {pending}/{args.count}. Generate fresh production answers with "
              "scripts/generate_traffic.py --scenario all, then re-run this command.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
