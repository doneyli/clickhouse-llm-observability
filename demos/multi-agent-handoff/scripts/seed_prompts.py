"""
Seed the agent prompts into Langfuse prompt management (SPEC §8).

One text prompt per agent: `concierge`, `tech-support`, `billing`, `single-agent`. The code
texts in handoff/prompts.py are the source and the agents' offline fallback. A new version
is created only when the text (or config) differs from what the label points at, so
re-running is a no-op.

    .venv/bin/python scripts/seed_prompts.py                    # label production
    .venv/bin/python scripts/seed_prompts.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config, prompts  # noqa: E402
from handoff.agents.registry import AGENTS  # noqa: E402


def seed(label: str = "production", dry_run: bool = False) -> list[tuple[str, str]]:
    lf = config.get_langfuse("seed")
    report = []
    for agent, text in prompts.FALLBACKS.items():
        name = prompts.prompt_name(agent)
        cfg = {"agent": agent, "model": AGENTS[agent].model, "demo": "multi-agent-handoff"}
        try:
            current = lf.get_prompt(name, label=label, type="text", cache_ttl_seconds=0)
        except Exception:  # noqa: BLE001 — 404: not seeded yet
            current = None
        if current is not None and current.prompt == text and (current.config or {}) == cfg:
            report.append((name, f"unchanged (v{current.version})"))
            continue
        if dry_run:
            report.append((name, "would create"))
            continue
        created = lf.create_prompt(name=name, type="text", prompt=text, labels=[label], config=cfg,
                                   tags=["multi-agent-handoff"], commit_message="seed_prompts.py")
        report.append((name, f"created v{created.version} → {label}"))
    lf.flush()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="production")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    for name, what in seed(args.label, args.dry_run):
        print(f"{name:14s} {what}")


if __name__ == "__main__":
    main()
