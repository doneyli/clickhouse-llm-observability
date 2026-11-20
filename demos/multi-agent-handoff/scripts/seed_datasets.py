"""
Seed the evaluation datasets into Langfuse (SPEC §7).

    .venv/bin/python scripts/seed_datasets.py            # handoff-routing (40) + handoff-conversations (12)
    .venv/bin/python scripts/seed_datasets.py --dry-run

Items have stable ids (`routing-<item>`), so re-running updates them in place instead of
adding duplicates. Expected values are computed from the back-office data and the credit
oracle (handoff/datasets.py), not typed by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config, datasets  # noqa: E402

DESCRIPTIONS = {
    datasets.ROUTING: ("Single-turn Northwind Mobile support requests. expected_output: the first handoff, the "
                       "ideal agent path, the fields the first handoff must carry, the back-office outcome "
                       "(from the credit oracle), and facts the reply must state."),
    datasets.CONVERSATIONS: ("Multi-turn goals for a simulated customer (persona, goal, what they know). "
                             "expected_output: the first specialist, the back-office outcome (from the credit "
                             "oracle), and the turn budget."),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    all_items = {datasets.ROUTING: datasets.routing_items(), datasets.CONVERSATIONS: datasets.conversation_items()}
    if args.dry_run:
        for name, items in all_items.items():
            for it in items:
                print(name, it["id"], it["expected_output"]["first_handoff"], it["expected_output"]["outcome"])
        return
    lf = config.get_langfuse("seed")
    for name, items in all_items.items():
        lf.create_dataset(name=name, description=DESCRIPTIONS[name],
                          metadata={"demo": "multi-agent-handoff", "items": len(items)})
        for it in items:
            lf.create_dataset_item(dataset_name=name, id=it["id"], input=it["input"],
                                   expected_output=it["expected_output"], metadata=it["metadata"])
        wanted = {it["id"] for it in items}
        retired = [i for i in lf.get_dataset(name).items if i.id not in wanted and i.status != "ARCHIVED"]
        for old in retired:  # items dropped from the code are archived, so old runs keep their links
            lf.create_dataset_item(dataset_name=name, id=old.id, input=old.input,
                                   expected_output=old.expected_output, metadata=old.metadata, status="ARCHIVED")
        lf.flush()
        print(f"{name}: {len(items)} items upserted, {len(retired)} archived"
              + (f" ({', '.join(i.id for i in retired)})" if retired else ""))


if __name__ == "__main__":
    main()
