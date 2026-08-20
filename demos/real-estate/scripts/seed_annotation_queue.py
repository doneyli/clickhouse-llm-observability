"""
Create a human-annotation setup for the real-estate project:

  1. Score configs (the label schema humans use):
       - reviewer-verdict   CATEGORICAL  approve / minor-issues / reject
       - expert-usefulness  NUMERIC      1..5
  2. An annotation queue "Property Concierge - human review" bound to them.
  3. Fill the queue with recent 'real-estate' traces to review (fault-injected
     ones first, so the reviewer has interesting cases).

Idempotent: re-running reuses existing configs/queue and only adds new items.

Run:
    ./.venv/bin/python scripts/seed_annotation_queue.py
    ./.venv/bin/python scripts/seed_annotation_queue.py --max-items 8
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.config import (  # noqa: E402
    verify_project, LANGFUSE_HOST, root_observations_by_tag,
    langfuse_api as api,
)

QUEUE_NAME = "Property Concierge - human review"


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


def ensure_queue(score_config_ids):
    _, existing = api("GET", "/api/public/annotation-queues?limit=100")
    for qd in existing.get("data", []):
        if qd.get("name") == QUEUE_NAME:
            print(f"  • annotation queue exists: {QUEUE_NAME}")
            return qd["id"]
    body = {"name": QUEUE_NAME,
            "description": "Human QA of Property Concierge answers: overall verdict + a 1-5 usefulness rating.",
            "scoreConfigIds": score_config_ids}
    status, resp = api("POST", "/api/public/annotation-queues", body)
    if status in (200, 201):
        print(f"  ✓ created annotation queue: {QUEUE_NAME}")
        return resp["id"]
    print(f"  ! queue create -> {status} {resp}")
    return None


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=6)
    args = ap.parse_args()

    verify_project()
    print("\nSetting up human annotation…")

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

    config_ids = [c for c in (verdict_id, rating_id) if c]
    queue_id = ensure_queue(config_ids)
    if not queue_id:
        sys.exit(1)

    # existing items (avoid duplicates)
    _, items = api("GET", f"/api/public/annotation-queues/{queue_id}/items?limit=100")
    already = {i.get("objectId") for i in items.get("data", [])}

    trace_ids = recent_realestate_traces(args.max_items + len(already))
    added = 0
    for tid in trace_ids:
        if tid in already:
            continue
        status, resp = api("POST", f"/api/public/annotation-queues/{queue_id}/items",
                           {"objectId": tid, "objectType": "TRACE", "status": "PENDING"})
        if status in (200, 201):
            added += 1
        if added >= args.max_items:
            break

    print(f"\n✓ Annotation queue ready with {added} new item(s) to review "
          f"({len(already)} already queued).")
    print(f"  View: {LANGFUSE_HOST} > Annotation Queues > {QUEUE_NAME}")


if __name__ == "__main__":
    main()
