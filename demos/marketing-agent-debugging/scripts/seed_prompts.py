"""
Seed the per-customer agent instructions into Langfuse Prompt Management (idempotent).

Creates `marketing-agent/<customer>` with the version history in agent/instructions.py,
including Tailspin's v3 regression. A version is only created if the prompt has fewer
versions than the history; labels and commit messages come from the history.

    .venv/bin/python scripts/seed_prompts.py            # create what's missing
    .venv/bin/python scripts/seed_prompts.py --reset-tailspin
        # after a live fix in the UI: move `production` back to v3 so the regression
        # can be demoed again (newer versions you created are left in place)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config  # noqa: E402
from agent.instructions import VERSIONS, prompt_name  # noqa: E402


def existing_versions(name: str) -> list:
    r = config.api("GET", "/v2/prompts", params={"name": name, "limit": 50})
    r.raise_for_status()
    rows = [p for p in r.json().get("data", []) if p["name"] == name]
    return rows[0]["versions"] if rows else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset-tailspin", action="store_true")
    args = ap.parse_args()
    config.verify_project()
    lf = config.get_langfuse()

    if args.reset_tailspin:
        r = config.api("PATCH", f"/v2/prompts/{quote(prompt_name('tailspin'), safe='')}/versions/3",
                       json={"newLabels": ["production"]})
        r.raise_for_status()
        print("= marketing-agent/tailspin: production → v3 (the regression) again")
        return 0

    for customer, history in VERSIONS.items():
        name = prompt_name(customer)
        have = len(existing_versions(name))
        for i, (text, labels, message) in enumerate(history, 1):
            if i <= have:
                continue
            lf.create_prompt(name=name, prompt=text, type="text", labels=labels,
                             tags=[f"customer:{customer}"], commit_message=message)
            print(f"+ {name} v{i} {labels or ''} — {message}")
        if have >= len(history):
            print(f"= {name}: {have} version(s) present")
    lf.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
