"""
Loop step 3: reviewed handoffs become `handoff-routing` test cases.

    .venv/bin/python scripts/promote_to_dataset.py             # every completed "Flagged handoffs" item
    .venv/bin/python scripts/promote_to_dataset.py --dry-run

For each completed item in the "Flagged handoffs" queue, the reviewer's `correct-handoff-target`
label becomes the item's `expected_output.first_handoff`, and the customer's message (and who
was signed in) becomes its input, linked to the production trace it came from. The routing
evaluators then score it like any other item: `handoff-target-correct` per item, `routing-accuracy`
per run, which is what the CI gate checks. A production failure becomes a regression test.

Only turn-1 handoffs are promoted: the routing dataset is single-turn, and a later turn depends
on the conversation before it (it would need a conversation item instead). Item ids are derived
from the handoff observation, so rerunning updates instead of duplicating; seed_datasets.py leaves
these items alone (metadata.source = production).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config, datasets  # noqa: E402
from handoff.agents.registry import AGENTS  # noqa: E402
from handoff.lfapi import api, paginate, ui  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flag_handoffs import LABEL, QUEUE, as_obj, queue  # noqa: E402


def reviewed() -> list[dict]:
    q = queue()
    items = [i for i in paginate(f"/api/public/annotation-queues/{q['id']}/items") if i.get("status") == "COMPLETED"]
    labels = {}
    for s in paginate("/api/public/v3/scores", params={"name": LABEL, "fields": "core,subject,details"}):
        sub = s.get("subject") or {}
        if sub.get("kind") == "observation":  # newest label wins if an item was relabelled
            prev = labels.get(sub["id"])
            if prev is None or s["timestamp"] > prev["timestamp"]:
                labels[sub["id"]] = {**s, "traceId": sub.get("traceId")}
    out = []
    for i in items:
        s = labels.get(i["objectId"])
        if s is None:
            print(f"  ? {i['objectId']}: completed without a {LABEL} label, skipped")
            continue
        out.append({"observation_id": i["objectId"], "trace_id": s["traceId"], "label": s["value"],
                    "comment": s.get("comment") or "", "source": s.get("source")})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lf = None if a.dry_run else config.get_langfuse("seed")
    rows = reviewed()
    print(f"{len(rows)} reviewed handoffs in {QUEUE!r}")
    made = skipped = 0
    for r in rows:
        obs = paginate("/api/public/v2/observations", params={"traceId": r["trace_id"], "fields": "core,basic,metadata,io"})
        root = next((o for o in obs if (o.get("metadata") or {}).get("role") == "orchestrator"), None)
        hop = next((o for o in obs if o["id"] == r["observation_id"]), None)
        md = (root or {}).get("metadata") or {}
        if root is None or hop is None or int(md.get("turn") or 0) != 1:
            skipped += 1
            print(f"  - {r['observation_id']}: not a turn-1 handoff (needs a conversation item), skipped")
            continue
        target = None if r["label"] == "none" else r["label"]
        actual = hop["metadata"].get("handoff_to")
        item_id = f"prod-{r['observation_id']}"
        message = as_obj(root.get("input")).get("message", "")
        expected = {"first_handoff": target,
                    "required_fields": list(AGENTS[target].required_fields) if target in AGENTS else []}
        meta = {"category": "production", "item": item_id, "source": "production", "trace_id": r["trace_id"],
                "observation_id": r["observation_id"], "routed_to": actual, "reviewer": r["comment"][:200],
                "scenario": md.get("scenario")}
        print(f"  + {item_id}: expected {target or 'no handoff'} (production sent it to {actual})  {message[:60]!r}")
        if lf is not None:
            lf.create_dataset_item(dataset_name=datasets.ROUTING, id=item_id,
                                   input={"message": message, "customer_id": md.get("customer_id")},
                                   expected_output=expected, metadata=meta,
                                   source_trace_id=r["trace_id"], source_observation_id=r["observation_id"])
        made += 1
    if lf is not None:
        lf.flush()
    print(f"\n{made} items {'would be ' if a.dry_run else ''}upserted into {datasets.ROUTING}, {skipped} skipped")
    if made and not a.dry_run:
        ds = next((d for d in paginate("/api/public/v2/datasets") if d["name"] == datasets.ROUTING), None)
        if ds:
            print(ui(f"/datasets/{ds['id']}"))


if __name__ == "__main__":
    main()
