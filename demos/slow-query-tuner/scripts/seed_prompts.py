"""
Seed the tuner's managed prompts into Langfuse Prompt Management (Deploy node).

  query-tuner-system  [v1-naive]                    — no plateau/give-up doctrine
  query-tuner-system  [production, v2-disciplined]   — re-measure + give-up rules
  query-tuner-goal    [production]                   — per-run user-message template

Idempotent: a new version is created only if the labelled prompt is missing or
its text differs from what's checked into prompts.py, so re-running is a clean
"reset to baseline" for a repeatable demo.

    python scripts/seed_prompts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import langfuse_config as lf
import prompts as P


def ensure_prompt(client, name: str, text: str, labels, commit: str) -> None:
    primary = labels[0]
    try:
        existing = client.get_prompt(name, label=primary, cache_ttl_seconds=0)
    except Exception:
        existing = None
    if existing is not None and (getattr(existing, "prompt", "") or "").strip() == text.strip():
        print(f"  = {name} [{','.join(labels)}] already up to date (v{getattr(existing, 'version', '?')})")
        return
    created = client.create_prompt(name=name, type="text", prompt=text,
                                   labels=labels, commit_message=commit)
    verb = "updated" if existing is not None else "created"
    print(f"  + {name} [{','.join(labels)}] {verb} (v{getattr(created, 'version', '?')})")


def main() -> int:
    if not lf.is_langfuse_enabled():
        print("Langfuse keys not set — skipping prompt seeding (loop still runs with fallbacks).")
        return 0
    client = lf.get_client()
    if client is None:
        print("Langfuse client unavailable — skipping prompt seeding.")
        return 0

    print("Seeding slow-query-tuner prompts into Langfuse Prompt Management…")
    ensure_prompt(client, P.SYSTEM_PROMPT_NAME, P.V1_NAIVE, [P.V1_LABEL],
                  "System prompt — v1 naive (no plateau/give-up; runaway fuel)")
    ensure_prompt(client, P.SYSTEM_PROMPT_NAME, P.V2_DISCIPLINED, [P.PRODUCTION_LABEL, P.V2_LABEL],
                  "System prompt — v2 disciplined (re-measure + plateau give-up); production")
    ensure_prompt(client, P.GOAL_PROMPT_NAME, P.GOAL_TEMPLATE, [P.PRODUCTION_LABEL],
                  "Per-run goal/user message template")
    lf.flush()
    print("✓ Prompts seeded. The agent fetches query-tuner-system [production] (== v2-disciplined).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
