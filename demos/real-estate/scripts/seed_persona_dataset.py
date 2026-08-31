"""
Create/refresh the 'property-concierge-personas' dataset in the real-estate project.

The persona dataset for the SIMULATED MULTI-TURN eval: each item is a buyer an
LLM role-plays for a whole conversation (see data/personas.py), so this dataset
feeds scripts/run_simulation_experiment.py, not scripts/run_experiment.py.

Run:
    ./.venv/bin/python scripts/seed_persona_dataset.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project  # noqa: E402
from data.personas import DATASET_NAME, DATASET_DESCRIPTION, ITEMS  # noqa: E402


def main():
    verify_project()
    lf = get_langfuse()

    try:
        lf.create_dataset(name=DATASET_NAME, description=DATASET_DESCRIPTION,
                          metadata={"source": "demos/real-estate",
                                    "eval_kind": "simulated-multi-turn"})
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
            # personas in place instead of duplicating them. The `pcp-` prefix
            # (property-concierge-personas) keeps them distinguishable from the
            # single-turn dataset's `pce-` items in the UI and in run diffs.
            #
            # No expected_output: an improvised conversation has no reference
            # transcript to diff against, so the trajectory evaluators judge the
            # dialogue on its own terms (see data/personas.py).
            lf.create_dataset_item(
                id=f"pcp-{i:02d}",
                dataset_name=DATASET_NAME,
                input=item["input"],
                metadata=item.get("metadata", {}),
            )
            created += 1
            trait = item.get("metadata", {}).get("awkward_trait", "?")
            print(f"  [{i:2}] {trait[:72]}")
        except Exception as e:
            print(f"  [{i:2}] ERROR: {e}")

    lf.flush()
    print(f"\n✓ {created}/{len(ITEMS)} personas in '{DATASET_NAME}'.")
    print(f"  View: Langfuse UI > Datasets > {DATASET_NAME}")
    print("  Then: ./.venv/bin/python scripts/run_simulation_experiment.py --yes")


if __name__ == "__main__":
    main()
