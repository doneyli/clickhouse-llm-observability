"""
Seed the tuner's managed prompts into Langfuse Prompt Management (Deploy node).

  query-tuner-system  [v1-naive]                    — no plateau/give-up doctrine
  query-tuner-system  [production, v2-disciplined]   — re-measure + give-up rules
  query-tuner-goal    [production]                   — per-run user-message template

Idempotent and drift-reconciling, per prompt definition (type, text, config, labels):

  * unchanged — every requested label already serves one version whose type, text and
    config match prompts.py. Nothing is written.
  * updated   — a version that matches exists but a requested label is missing or points
    elsewhere: the labels are (re)attached to that version, keeping its other labels. No
    new version.
  * created / updated — no labelled version matches: a new version is created with all
    the requested labels ("created" when none of the labels existed yet).

Re-running is a clean "reset to baseline" for a repeatable demo. Exit 1 if any prompt
failed; the others are still reconciled.

    python scripts/seed_prompts.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import langfuse_config as lf
import prompts as P

# The owned definitions: (name, text, labels, commit message). Config is {} for all three.
DEFINITIONS = [
    (P.SYSTEM_PROMPT_NAME, P.V1_NAIVE, [P.V1_LABEL],
     "System prompt — v1 naive (no plateau/give-up; runaway fuel)"),
    (P.SYSTEM_PROMPT_NAME, P.V2_DISCIPLINED, [P.PRODUCTION_LABEL, P.V2_LABEL],
     "System prompt — v2 disciplined (re-measure + plateau give-up); production"),
    (P.GOAL_PROMPT_NAME, P.GOAL_TEMPLATE, [P.PRODUCTION_LABEL],
     "Per-run goal/user message template"),
]
RESERVED_LABELS = {"latest"}  # managed by Langfuse; never sent in new_labels


def is_not_found(exc: Exception) -> bool:
    """True for the SDK's 404 (no version carries the label), never for other errors."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 404 or type(exc).__name__ in ("NotFoundError", "NotFound")


def matches(version: Any, text: str, config: Dict[str, Any]) -> bool:
    """A served version matches when it is a text prompt with the same text and config."""
    served = getattr(version, "prompt", None)
    return (isinstance(served, str) and served.strip() == text.strip()
            and (getattr(version, "config", None) or {}) == config)


def ensure_prompt(client, name: str, text: str, labels: List[str], commit: str,
                  config: Optional[Dict[str, Any]] = None) -> str:
    """Reconcile one prompt definition; return "created", "updated" or "unchanged"."""
    config = config or {}
    served: Dict[str, Any] = {}
    for label in labels:
        try:
            served[label] = client.get_prompt(name, label=label, type="text", cache_ttl_seconds=0)
        except Exception as e:  # noqa: BLE001 — only a 404 means "label not there yet"
            if not is_not_found(e):
                raise
            served[label] = None

    good = {label: v for label, v in served.items() if v is not None and matches(v, text, config)}
    if len(good) == len(labels) and len({getattr(v, "version", None) for v in good.values()}) == 1:
        version = next(iter(good.values()))
        print(f"  = {name} [{','.join(labels)}] unchanged (v{getattr(version, 'version', '?')})")
        return "unchanged"

    target = next((good[label] for label in labels if label in good), None)
    if target is not None:
        keep = [x for x in (getattr(target, "labels", None) or []) if x not in RESERVED_LABELS]
        new_labels = keep + [x for x in labels if x not in keep]
        client.update_prompt(name=name, version=target.version, new_labels=new_labels)
        moved = [x for x in labels if x not in good]
        print(f"  ~ {name} [{','.join(labels)}] updated: {', '.join(moved)} attached to "
              f"v{target.version} (no new version)")
        return "updated"

    created = client.create_prompt(name=name, type="text", prompt=text, labels=labels,
                                   config=config, commit_message=commit)
    verb = "created" if all(v is None for v in served.values()) else "updated"
    print(f"  + {name} [{','.join(labels)}] {verb} (new v{getattr(created, 'version', '?')})")
    return verb


def main() -> int:
    if not lf.is_langfuse_enabled():
        print("ERROR: Langfuse keys not set — no prompts seeded.")
        return 1
    client = lf.get_client()
    if client is None:
        print("ERROR: Langfuse client unavailable — no prompts seeded.")
        return 1

    print("Seeding slow-query-tuner prompts into Langfuse Prompt Management…")
    results: List[str] = []
    for name, text, labels, commit in DEFINITIONS:
        try:
            results.append(ensure_prompt(client, name, text, labels, commit))
        except Exception as e:  # noqa: BLE001 — report it, reconcile the rest, fail at the end
            print(f"  ! {name} [{','.join(labels)}] FAILED: {e}")
            results.append("failed")
    lf.flush()
    counts = {k: results.count(k) for k in ("created", "updated", "unchanged", "failed")}
    print("Prompts: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
