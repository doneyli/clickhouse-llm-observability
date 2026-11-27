"""
Seed the `query-tuner/goals` dataset — ROOT-LEVEL items ONLY.

Items target the trace ROOT (the overall run): input = the goal, expected_output
= completion CRITERIA (never a step sequence). Per-step ground truth is actively
WRONG for this pattern: the same goal legitimately yields different valid
trajectories run to run (PREWHERE-first or predicate-fix-first both reach the
target), so a per-step dataset would enshrine one arbitrary path as "correct"
and mark a better, shorter path as a failure. Task completion belongs on the
root of the trace.

Idempotent and drift-reconciling: the dataset (description, metadata) and each item
(stable id qt-q1..q3: input, expected output, metadata, ACTIVE status) are compared
with what is stored, and only a missing or different one is written. An archived item
is restored to ACTIVE. After writing it reads the dataset back and checks every item.
Exit 1 if any write fails or the read-back does not match.

    python scripts/seed_dataset.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import langfuse_config as lf
import queries

DATASET_NAME = "query-tuner/goals"
DESCRIPTION = "Outcome-only regression set; trajectories are NOT compared step-by-step."
DATASET_METADATA = {"source": "demos/slow-query-tuner"}
QUERY_IDS = ("q1", "q2", "q3")

COMPLETION_CRITERIA = [
    "result-set equivalence probe passes on the final SQL",
    "measured latency <= target_ms (median of 3 runs), OR status=gave_up naming the true structural blocker",
    "no DDL executed without human approval",
    "finish claim verified by the controller (no unverified speedup claims)",
]


def desired_items() -> List[Dict[str, Any]]:
    out = []
    for qid in QUERY_IDS:
        g = queries.get_goal(qid)
        out.append({
            "id": f"qt-{qid}",
            "input": {"sql": g.sql, "target_ms": g.target_ms},
            "expected_output": {"completion_criteria": COMPLETION_CRITERIA},
            "metadata": {"eval_target": "root_observation",
                         "expected_turn_band": list(g.expected_turn_band),
                         "reason": "near-zero run-to-run determinism",
                         "query_id": qid},
            "designed_outcome": g.designed_outcome,
            "target_ms": g.target_ms,
        })
    return out


def is_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 404 or type(exc).__name__ in ("NotFoundError", "NotFound")


def status_of(item: Any) -> str:
    s = getattr(item, "status", None)
    return str(getattr(s, "value", s) or "").upper()


def item_matches(stored: Any, want: Dict[str, Any]) -> bool:
    return (stored is not None and status_of(stored) == "ACTIVE"
            and getattr(stored, "input", None) == want["input"]
            and getattr(stored, "expected_output", None) == want["expected_output"]
            and getattr(stored, "metadata", None) == want["metadata"])


def get_dataset_or_none(client) -> Optional[Any]:
    try:
        return client.get_dataset(DATASET_NAME)
    except Exception as e:  # noqa: BLE001 — only a 404 means "not created yet"
        if is_not_found(e):
            return None
        raise


def reconcile(client) -> Dict[str, List[str]]:
    """Write what differs; return {"created"|"updated"|"unchanged"|"failed": [names]}."""
    result: Dict[str, List[str]] = {"created": [], "updated": [], "unchanged": [], "failed": []}
    ds = get_dataset_or_none(client)
    if ds is None or (getattr(ds, "description", None), getattr(ds, "metadata", None)) != (DESCRIPTION, DATASET_METADATA):
        verb = "created" if ds is None else "updated"
        try:
            client.create_dataset(name=DATASET_NAME, description=DESCRIPTION, metadata=DATASET_METADATA)
            result[verb].append(f"dataset {DATASET_NAME}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! dataset {DATASET_NAME}: {verb[:-1]} FAILED: {e}")
            result["failed"].append(f"dataset {DATASET_NAME}")
    else:
        result["unchanged"].append(f"dataset {DATASET_NAME}")

    stored = {getattr(i, "id", None): i for i in (getattr(ds, "items", None) or [])}
    for want in desired_items():
        current = stored.get(want["id"])
        if item_matches(current, want):
            result["unchanged"].append(want["id"])
            print(f"  = [{want['id']}] unchanged — target {want['target_ms']} ms")
            continue
        verb = "created" if current is None else "updated"
        try:
            client.create_dataset_item(id=want["id"], dataset_name=DATASET_NAME, input=want["input"],
                                       expected_output=want["expected_output"], metadata=want["metadata"],
                                       status="ACTIVE")
            result[verb].append(want["id"])
            print(f"  + [{want['id']}] {verb} — target {want['target_ms']} ms, {want['designed_outcome']}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! [{want['id']}] {verb[:-1]} FAILED: {e}")
            result["failed"].append(want["id"])
    return result


def read_back(client) -> List[str]:
    """Names of the expected items that are not stored, ACTIVE and as defined."""
    ds = get_dataset_or_none(client)
    stored = {getattr(i, "id", None): i for i in (getattr(ds, "items", None) or [])}
    return [w["id"] for w in desired_items() if not item_matches(stored.get(w["id"]), w)]


def main() -> int:
    if not lf.is_langfuse_enabled():
        print("ERROR: Langfuse keys not set — dataset not seeded.")
        return 1
    client = lf.get_client()
    if client is None:
        print("ERROR: Langfuse client unavailable — dataset not seeded.")
        return 1

    print(f"Seeding dataset {DATASET_NAME} (root-level items only)…")
    try:
        result = reconcile(client)
        lf.flush()
        bad = read_back(client) if (result["created"] or result["updated"]) else []
    except Exception as e:  # noqa: BLE001 — a failed read is a failed seed
        print(f"  ! dataset {DATASET_NAME}: read FAILED: {e}")
        return 1
    if bad:
        print(f"  ! read-back: {', '.join(bad)} not stored ACTIVE as defined")
    print("Dataset: " + ", ".join(f"{len(v)} {k}" for k, v in result.items()))
    return 1 if (result["failed"] or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
