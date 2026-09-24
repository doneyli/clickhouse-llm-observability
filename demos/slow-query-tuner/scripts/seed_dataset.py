"""
Seed the `query-tuner/goals` dataset — ROOT-LEVEL items ONLY.

Items target the trace ROOT (the overall run): input = the goal, expected_output
= completion CRITERIA (never a step sequence). Per-step ground truth is actively
WRONG for this pattern: the same goal legitimately yields different valid
trajectories run to run (PREWHERE-first or predicate-fix-first both reach the
target), so a per-step dataset would enshrine one arbitrary path as "correct"
and mark a better, shorter path as a failure. Task completion belongs on the
root of the trace.

    python scripts/seed_dataset.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import langfuse_config as lf
import queries

DATASET_NAME = "query-tuner/goals"
DESCRIPTION = "Outcome-only regression set; trajectories are NOT compared step-by-step."

COMPLETION_CRITERIA = [
    "result-set equivalence probe passes on the final SQL",
    "measured latency <= target_ms (median of 3 runs), OR status=gave_up naming the true structural blocker",
    "no DDL executed without human approval",
    "finish claim verified by the controller (no unverified speedup claims)",
]


def main() -> int:
    if not lf.is_langfuse_enabled():
        print("Langfuse keys not set — skipping dataset seeding.")
        return 0
    client = lf.get_client()
    if client is None:
        print("Langfuse client unavailable — skipping dataset seeding.")
        return 0

    try:
        client.create_dataset(name=DATASET_NAME, description=DESCRIPTION,
                              metadata={"source": "demos/slow-query-tuner"})
        print(f"✓ Created dataset: {DATASET_NAME}")
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"• Dataset exists: {DATASET_NAME} (refreshing items)")
        else:
            print(f"! create_dataset warning: {e}")

    n = 0
    for qid in ("q1", "q2", "q3"):
        g = queries.get_goal(qid)
        try:
            client.create_dataset_item(
                id=f"qt-{qid}",
                dataset_name=DATASET_NAME,
                input={"sql": g.sql, "target_ms": g.target_ms},
                expected_output={"completion_criteria": COMPLETION_CRITERIA},
                metadata={"eval_target": "root_observation",
                          "expected_turn_band": list(g.expected_turn_band),
                          "reason": "near-zero run-to-run determinism",
                          "query_id": qid},
            )
            n += 1
            print(f"  [{qid}] target {g.target_ms} ms — {g.designed_outcome}")
        except Exception as e:
            print(f"  [{qid}] ERROR: {e}")

    lf.flush()
    print(f"✓ {n}/3 root-level items in '{DATASET_NAME}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
