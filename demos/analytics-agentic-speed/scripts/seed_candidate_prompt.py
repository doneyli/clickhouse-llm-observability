#!/usr/bin/env python3
"""Seed a deliberately WORSE prompt pair under a non-production label: the prompt gate's demo beat.

    .venv/bin/python scripts/seed_candidate_prompt.py             # create / reconcile `candidate-coarse`
    .venv/bin/python scripts/seed_candidate_prompt.py --dry-run   # show what would change, write nothing
    .venv/bin/python scripts/seed_candidate_prompt.py --show      # which version carries which label

`candidate-coarse` is a well-meant policy edit: "to save time, and because schedulers plan in
5-minute increments", the planner sweeps minutes and thresholds in steps of 5, never finer, and the
analyst reports the smallest multiple of 5 that works. The hero question (q-padding) goes wrong: its
answer, LAX at 9 minutes, beats the runner-up ATL at 10 by one minute, so a 5-minute grid lands on 10.
Then:

    .venv/bin/python scripts/prompt_gate.py --prompt-label candidate-coarse   # -> GATE FAILED, exit 1

Both prompts get the label, because the agent fetches them as a pair and the gate refuses a label that
only one of them carries. The candidate text is derived from the production text in
``explorer/prompts.py`` by a few anchored replacements; if an anchor is gone (the production prompt
was rewritten) the script stops instead of seeding a no-op variant.

`production` is never touched: each new version is created with ONLY the candidate label (Langfuse
adds `latest` itself), a production/latest label is refused outright, and the production version of
each prompt is read before and after and must not change. Labels are unique per prompt, so a new
version takes `candidate-coarse` off the older one. Re-running with unchanged text is a no-op.
Every new version fires the project's prompt automation (the CI gate), if one is configured.

Why a firm policy and not a hint: the first version only said a 10-minute step "is precise enough",
and Claude Opus 5.5 overrode it. Its plan noted that a 10-minute step would not give the smallest
whole minute the question asks for, swept 1-minute steps anyway and answered LAX/9 (run
`gate · candidate-coarse v2/v2 · 20261127T034111Z` passed). A coarse grid has to be stated as a rule
that wins over the question's wording before it costs accuracy.

Keys come from the demo's own ``.env`` (``explorer.config.load_env`` overrides the shell), and the
script refuses to write unless they map to the ``analytics-agentic-speed`` project.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

DEMO_DIR = Path(__file__).resolve().parent.parent
for p in (DEMO_DIR, DEMO_DIR / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from explorer import prompts as P  # noqa: E402

EXPECTED_PROJECT = "analytics-agentic-speed"
RESERVED_LABELS = frozenset({"production", "latest", "previous-production"})


def edit(text: str, anchor: str, replacement: str) -> str:
    """Replace ``anchor`` (which must occur exactly once) with ``replacement``."""
    n = text.count(anchor)
    if n != 1:
        raise ValueError(f"anchor found {n} times, expected once (did the production prompt change?): {anchor[:80]!r}")
    return text.replace(anchor, replacement)


def coarse_planner(text: str = P.PLANNER_TEXT) -> str:
    return edit(
        text,
        "ClickHouse answers each scenario in tens of milliseconds,\n"
        "so breadth is cheap: prefer covering the space over guessing.",
        "To save time, keep the grid coarse. Grid policy: sweep minutes, buffers and\n"
        "thresholds in steps of 5 (0, 5, 10, ...), never finer, even when a question asks for\n"
        "whole minutes. Schedulers plan in 5-minute increments, so a finer answer can't be used.",
    )


def coarse_analyst(text: str = P.ANALYST_TEXT) -> str:
    text = edit(
        text,
        "- Refine when the winner is near an edge of the grid, the top results are close, the\n"
        "  question needs a finer step (e.g. 1-minute buffers instead of 5), or a lever you\n"
        "  dropped could change the answer. A refine sweep is cheap; use it.",
        "- Refine only when the winner is at an edge of the grid or a lever you dropped could\n"
        "  change the answer. Grid policy: never refine to a step finer than 5 minutes.\n"
        "  Schedulers plan in 5-minute increments, so report minutes and thresholds as the\n"
        "  smallest multiple of 5 that works, even when the question asks for whole minutes.",
    )
    return edit(text, "(e.g. every\n  whole minute in the interval that matters)", "(e.g. every\n  5 minutes)")


CANDIDATES: dict[str, dict[str, Any]] = {
    "candidate-coarse": {
        "texts": lambda: {P.PLANNER: coarse_planner(), P.ANALYST: coarse_analyst()},
        "commit": "candidate-coarse: 5-minute grid policy, never finer, answers rounded to it "
                  "(deliberately worse: the prompt gate's demo beat)",
    },
}


def labels_for(label: str) -> list[str]:
    """The labels a new candidate version is created with: the candidate label alone."""
    if label in RESERVED_LABELS:
        raise ValueError(f"refusing label {label!r}: this script never assigns {sorted(RESERVED_LABELS)}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", label):
        raise ValueError(f"label {label!r}: use lowercase letters, digits, '.', '_' or '-'")
    return [label]


def variables(text: str) -> set[str]:
    """The ``{{name}}`` template variables a prompt compiles with."""
    return set(re.findall(r"\{\{(\w+)\}\}", text))


# ---------------------------------------------------------------- Langfuse

def _get(api: Any, name: str, **params: Any) -> Optional[dict]:
    from seed_langfuse import LangfuseAPIError  # noqa: PLC0415

    try:
        return api("GET", f"/api/public/v2/prompts/{quote(name, safe='')}", params=params)
    except LangfuseAPIError as e:
        if e.status == 404:
            return None
        raise


def show(api: Any) -> None:
    for name in (P.PLANNER, P.ANALYST):
        latest = _get(api, name, label="latest")
        if latest is None:
            print(f"{name}: not found (run scripts/seed_langfuse.py)")
            continue
        print(f"{name}:")
        for v in range(1, int(latest["version"]) + 1):
            p = _get(api, name, version=v)
            if p is not None:
                print(f"  v{v}: {', '.join(p.get('labels') or []) or '-'}")


def seed(api: Any, label: str, dry_run: bool) -> int:
    spec = CANDIDATES[label]
    new_labels = labels_for(label)
    texts: dict[str, str] = spec["texts"]()
    prod_before: dict[str, int] = {}
    for name, text in texts.items():
        prod = _get(api, name, label="production")
        if prod is None:
            print(f"  ! {name} has no production version: run scripts/seed_langfuse.py first")
            return 1
        prod_before[name] = prod["version"]
        if variables(text) != variables(prod["prompt"]):
            print(f"  ! {name}: candidate variables {sorted(variables(text))} differ from production "
                  f"{sorted(variables(prod['prompt']))}; refusing")
            return 1
        cur = _get(api, name, label=label)
        if cur is not None and cur.get("prompt") == text:
            print(f"  = {name} v{cur['version']} already carries {label!r} with this text")
            continue
        if dry_run:
            print(f"  would create {name} -> {new_labels} (production stays v{prod['version']})")
            continue
        created = api("POST", "/api/public/v2/prompts", {
            "name": name, "type": "text", "prompt": text, "labels": new_labels,
            "tags": ["analytics-agentic-speed", "prompt-gate-demo"], "commitMessage": spec["commit"],
        })
        print(f"  + {name} v{created.get('version')} -> {new_labels}")
    if dry_run:
        return 0
    ok = True
    for name, before in prod_before.items():
        prod = _get(api, name, label="production")
        cand = _get(api, name, label=label)
        after = prod["version"] if prod else None
        ok &= after == before and cand is not None and "production" not in (cand.get("labels") or [])
        print(f"  read back: {name} production = v{after} (was v{before}); {label} = "
              f"v{cand['version'] if cand else '?'}, labels {cand.get('labels') if cand else None}")
    if not ok:
        print("  ! production moved or the candidate did not land: check the prompt history in Langfuse")
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="candidate-coarse", choices=sorted(CANDIDATES))
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--show", action="store_true", help="list each prompt's versions and labels, change nothing")
    ap.add_argument("--project-name", default=EXPECTED_PROJECT, help="project the keys must map to")
    a = ap.parse_args(argv)

    from seed_langfuse import API, verify_project  # noqa: PLC0415

    api = API.from_env()
    verify_project(api, a.project_name)
    if a.show:
        show(api)
        return 0
    print(f"\n== {a.label}{' (dry run)' if a.dry_run else ''}")
    return seed(api, a.label, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
