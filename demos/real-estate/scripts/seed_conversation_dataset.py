"""
Create/refresh the 'property-concierge-conversations' dataset (N+1 multi-turn eval).

Run:
    ./.venv/bin/python scripts/seed_conversation_dataset.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project
from data.conversations import DATASET_NAME, DATASET_DESCRIPTION, ITEMS


def main():
    verify_project()
    lf = get_langfuse()

    try:
        lf.create_dataset(name=DATASET_NAME, description=DATASET_DESCRIPTION,
                          metadata={"source": "demos/real-estate", "method": "n-plus-1"})
        print(f"✓ Created dataset: {DATASET_NAME}")
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"• Dataset already exists: {DATASET_NAME} (adding/refreshing items)")
        else:
            print(f"! create_dataset warning: {e}")

    created = 0
    for i, item in enumerate(ITEMS, 1):
        try:
            # Stable id → create_dataset_item UPSERTS, so re-running refreshes the
            # items in place instead of duplicating them. Prefix `pcc-` (property
            # concierge conversations) keeps these distinct from the single-turn
            # dataset's `pce-` ids.
            lf.create_dataset_item(
                id=f"pcc-{i:02d}",
                dataset_name=DATASET_NAME,
                input=item["input"],
                expected_output=item["expected_output"],
                metadata=item.get("metadata", {}),
            )
            created += 1
            meta = item.get("metadata", {})
            turns = meta.get("turns_of_context", len(item["input"].get("history", [])) // 2)
            print(f"  [{i:2}] {meta.get('failure_mode', '?'):32} "
                  f"(+{turns} turns) {item['input']['question'][:44]}")
        except Exception as e:
            print(f"  [{i:2}] ERROR: {e}")

    lf.flush()
    print(f"\n✓ {created}/{len(ITEMS)} items in '{DATASET_NAME}'.")
    print("  View: Langfuse UI > Datasets > property-concierge-conversations")


if __name__ == "__main__":
    main()
