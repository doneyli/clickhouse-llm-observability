"""
Seed the concierge's system prompts into Langfuse Prompt Management.

This is the **Deploy** node of the AI Engineering loop: the agent fetches these
prompts at runtime by label, so a version labelled `production` is what actually
runs. We seed:

  • property-concierge-plan   [production]  — constraint extractor (no variables)
  • property-concierge-agent  [first-draft] — a deliberately naive first draft,
                                              the "before" in a demo that shows
                                              the loop producing a VISIBLE win
  • property-concierge-agent  [production]  — the baseline concierge prompt
  • property-concierge-agent  [candidate]   — an improved variant to experiment
                                              with, then promote ("deploy")

Idempotent: for each (name, label) we create a new version ONLY if the prompt is
missing or its text differs from what's checked in here. Re-running resets the
prompts to the versions defined in agent/prompts.py (so re-seeding is a clean
"reset to baseline" for a repeatable demo).

Run:
    ./.venv/bin/python scripts/seed_prompts.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project, LANGFUSE_HOST
from agent.prompts import (
    PLAN_PROMPT_NAME, AGENT_PROMPT_NAME,
    PRODUCTION_LABEL, CANDIDATE_LABEL, FIRST_DRAFT_LABEL,
    PLAN_FALLBACK, AGENT_FALLBACK, AGENT_CANDIDATE, AGENT_FIRST_DRAFT,
)


def ensure_prompt(lf, name: str, text: str, label: str, commit_message: str) -> None:
    """Create prompt `name` with `label` unless an identical version already
    carries that label (check-before-create, so re-runs don't spam versions)."""
    try:
        existing = lf.get_prompt(name, label=label, cache_ttl_seconds=0)
    except Exception:
        existing = None

    if existing is not None and (existing.prompt or "").strip() == text.strip():
        print(f"  ✓ {name} [{label}] already up to date (v{getattr(existing, 'version', '?')})")
        return

    created = lf.create_prompt(
        name=name, type="text", prompt=text,
        labels=[label], commit_message=commit_message,
    )
    verb = "updated" if existing is not None else "created"
    print(f"  + {name} [{label}] {verb} (v{getattr(created, 'version', '?')})")


def main() -> None:
    verify_project()
    lf = get_langfuse()

    print("Seeding concierge prompts into Langfuse Prompt Management…")
    ensure_prompt(lf, PLAN_PROMPT_NAME, PLAN_FALLBACK, PRODUCTION_LABEL,
                  "Constraint extractor — baseline")
    # Seed first-draft BEFORE production so the version numbers read like a real
    # history in the Prompts tab (v1 naive first draft → v2 production → v3
    # candidate), which is the story the demo walks through.
    ensure_prompt(lf, AGENT_PROMPT_NAME, AGENT_FIRST_DRAFT, FIRST_DRAFT_LABEL,
                  "Concierge system prompt — naive first draft: no budget "
                  "discipline, English-only output (DEMO 'before' baseline)")
    ensure_prompt(lf, AGENT_PROMPT_NAME, AGENT_FALLBACK, PRODUCTION_LABEL,
                  "Concierge system prompt — baseline (production)")
    ensure_prompt(lf, AGENT_PROMPT_NAME, AGENT_CANDIDATE, CANDIDATE_LABEL,
                  "Concierge system prompt — candidate: tighter grounding, "
                  "budget discipline, strict language, scannable format")

    lf.flush()
    print(f"\n✓ Prompts seeded. View: {LANGFUSE_HOST} → project 'real-estate' → Prompts")
    print("  The agent fetches 'property-concierge-agent' [production] at runtime.")
    print("  Compare [production] vs [candidate] with:")
    print("    ./.venv/bin/python scripts/run_experiment.py --prompt-label candidate")
    print("  For a VISIBLE improvement (deterministic scores move), compare the")
    print("  naive first draft against production:")
    print("    ./.venv/bin/python scripts/run_experiment.py --prompt-label first-draft")


if __name__ == "__main__":
    main()
