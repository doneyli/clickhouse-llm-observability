"""
Create/refresh the 'property-concierge-eval' dataset in the real-estate project.

Run:
    ./.venv/bin/python scripts/seed_dataset.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project
from data.dataset import DATASET_NAME, DATASET_DESCRIPTION, ITEMS


def main():
    verify_project()
    lf = get_langfuse()

    try:
        lf.create_dataset(name=DATASET_NAME, description=DATASET_DESCRIPTION,
                          metadata={"source": "real-estate-demo"})
        print(f"✓ Created dataset: {DATASET_NAME}")
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"• Dataset already exists: {DATASET_NAME} (adding/refreshing items)")
        else:
            print(f"! create_dataset warning: {e}")

    created = 0
    for i, item in enumerate(ITEMS, 1):
        try:
            lf.create_dataset_item(
                dataset_name=DATASET_NAME,
                input=item["input"],
                expected_output=item["expected_output"],
                metadata=item.get("metadata", {}),
            )
            created += 1
            q = item["input"]["question"]
            print(f"  [{i:2}] {q[:72]}")
        except Exception as e:
            print(f"  [{i:2}] ERROR: {e}")

    lf.flush()
    print(f"\n✓ {created}/{len(ITEMS)} items in '{DATASET_NAME}'.")
    print("  View: Langfuse UI > Datasets > property-concierge-eval")


if __name__ == "__main__":
    main()
