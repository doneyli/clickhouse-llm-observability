"""
Seed the evaluation datasets into Langfuse (SPEC §7).

    .venv/bin/python scripts/seed_datasets.py            # handoff-routing (40 items)
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
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    items = datasets.routing_items()
    if args.dry_run:
        for it in items:
            print(it["id"], it["expected_output"]["first_handoff"], it["expected_output"]["outcome"])
        return
    lf = config.get_langfuse("seed")
    lf.create_dataset(name=datasets.ROUTING, description=DESCRIPTIONS[datasets.ROUTING],
                      metadata={"demo": "multi-agent-handoff", "items": len(items)})
    for it in items:
        lf.create_dataset_item(dataset_name=datasets.ROUTING, id=it["id"], input=it["input"],
                               expected_output=it["expected_output"], metadata=it["metadata"])
    lf.flush()
    print(f"{datasets.ROUTING}: {len(items)} items upserted")


if __name__ == "__main__":
    main()
