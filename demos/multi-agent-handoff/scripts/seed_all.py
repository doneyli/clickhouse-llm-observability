#!/usr/bin/env python3
"""
Seed everything the demo needs before it runs: the one seed entry point (demo.yaml, commands.seed).

    .venv/bin/python scripts/seed_all.py          # the four seeders, in order; exit 1 at the first failure
    .venv/bin/python scripts/seed_all.py --plan   # print the commands and run nothing

It runs the four seeders below as they are documented in the README. Each one is idempotent
and reconciles drift, and prints what it created, updated or left unchanged. Keys come from
the demo's `.env` (handoff/config.py); shell-exported `LANGFUSE_*` are ignored.

`seed_prompts.py` writes a new version whenever the text a label serves differs from the code,
and the new version takes the label. So after a pass round the loop, seeding puts the seeded
concierge prompt back in production: run `scripts/loop.py status` first if the fix should stay live.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

DEMO = Path(__file__).resolve().parents[1]

# (what it seeds, script and flags): the order matters only for reading the output top to bottom.
STEPS: list[tuple[str, list[str]]] = [
    ("4 agent prompts @production, fault variants @fault-f1 / @fault-f3", ["scripts/seed_prompts.py", "--faults"]),
    ("datasets handoff-routing (40 items) and handoff-conversations (12)", ["scripts/seed_datasets.py"]),
    ("2 code evaluators, 2 LLM judges, their rules, score configs, 2 review queues",
     ["scripts/seed_evals.py", "--judges", "--queue"]),
    ("2 dashboards and their widgets", ["scripts/seed_dashboards.py"]),
]


def _run(argv: Sequence[str]) -> int:
    return subprocess.run(list(argv), cwd=DEMO).returncode


def seed_all(run: Callable[[Sequence[str]], int] = _run, python: str = sys.executable) -> int:
    """Run every step; stop at the first one that fails. Returns the process exit code."""
    for n, (what, cmd) in enumerate(STEPS, 1):
        print(f"\n[{n}/{len(STEPS)}] {what}\n      $ {' '.join(cmd)}", flush=True)
        code = run([python, *cmd])
        if code != 0:
            print(f"\nFAILED at step {n} ({cmd[0]}, exit {code}); the steps after it did not run.", flush=True)
            return 1
    print(f"\nSeeded: {len(STEPS)} of {len(STEPS)} steps. Next: scripts/verify_demo.py", flush=True)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="print the commands and run nothing")
    args = ap.parse_args(argv)
    if args.plan:
        for what, cmd in STEPS:
            print(f".venv/bin/python {' '.join(cmd):45s} # {what}")
        return 0
    return seed_all()


if __name__ == "__main__":
    sys.exit(main())
