"""Fill the SME review queue (EVA-06) with production answers, worst-first.

Selection: lowest faithfulness / compliance judge scores and thumbs-down first,
then a random sample — the queue an SME would get each week. Items are the ROOT
observations, the same object the managed judges score, so every SME label forms
a matched pair with a judge score (Scores → Analytics computes agreement).
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

QUEUE = "SME review — assistant answers"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20


def main():
    q = next(x for x in config.api("GET", "/api/public/annotation-queues", params={"limit": 100})["data"]
             if x["name"] == QUEUE)
    existing = {i["objectId"] for i in config.api("GET", f"/api/public/annotation-queues/{q['id']}/items",
                                                  params={"limit": 100})["data"]}
    scores = config.api("GET", "/api/public/v3/scores", params={"limit": 100, "fields": "core,subject"})["data"]
    worst: dict = {}
    for s in scores:
        subj = s.get("subject") or {}
        oid = (subj.get("id") if subj.get("kind") in ("observation", "OBSERVATION") else None) \
            or subj.get("observationId") or s.get("observationId")
        if oid and s["name"] in ("faithfulness", "banking-compliance") and isinstance(s.get("value"), (int, float)):
            worst[oid] = min(worst.get(oid, 1.0), s["value"])
    obs = config.api("GET", "/api/public/v2/observations",
                     params={"name": "northwind-assistant", "environment": "production", "limit": 100,
                             "fields": "core"})["data"]
    roots = [o["id"] for o in obs if not o.get("parentObservationId")]
    ranked = sorted(roots, key=lambda i: (worst.get(i, 1.0), random.random()))
    added = 0
    for oid in ranked:
        if added >= N:
            break
        if oid in existing:
            continue
        config.api("POST", f"/api/public/annotation-queues/{q['id']}/items",
                   {"objectId": oid, "objectType": "OBSERVATION"})
        added += 1
    print(f"+ {added} items → queue '{QUEUE}'  ({len(existing)} already there)")
    print(f"Annotate: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/annotation-queues/{q['id']}")


if __name__ == "__main__":
    main()
