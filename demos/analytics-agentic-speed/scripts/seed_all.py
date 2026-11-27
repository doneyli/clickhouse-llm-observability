#!/usr/bin/env python3
"""Seed everything Analytics at Agentic Speed needs, in order. Idempotent.

    .venv/bin/python scripts/seed_all.py               # data, then Langfuse, then the dataset
    .venv/bin/python scripts/seed_all.py --skip-data   # Langfuse side only (prompts, configs, dashboard, dataset)
    .venv/bin/python scripts/seed_all.py --only langfuse
    .venv/bin/python scripts/seed_all.py --dry-run     # each seeder's own dry run: report, write nothing

A thin wrapper over the demo's three seeders, run as subprocesses with this interpreter
from the demo folder. Each one is idempotent and reconciles drift on its own, and prints
what it created, updated or left unchanged:

1. ``data``: ``scripts/load_data.py``. Schema, the flights load (a year that already
   matches the source is skipped; the first full load takes ~6 min), the read-only agent
   user, and a read-back of row counts and grants.
2. ``langfuse``: ``scripts/seed_langfuse.py``. Score configs (drifted ones are PATCHed),
   the planner and analyst prompts (a new ``production`` version only when the text
   differs), and the dashboard (drifted widgets updated, layout rebuilt when it differs).
   Refuses to write unless the keys map to the ``analytics-agentic-speed`` project.
3. ``dataset``: ``scripts/seed_dataset.py``. Ground truth from each question's reference
   SQL, items upserted by id, stale items archived, then read back.

The order matters: the dataset's ground truth is computed on the loaded table. The run
stops at the first failure and exits 1; the steps after it are reported as not run.
Standard library only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Sequence

DEMO_DIR = Path(__file__).resolve().parent.parent


class Step(NamedTuple):
    name: str
    script: str  # relative to the demo folder
    what: str


STEPS: List[Step] = [
    Step("data", "scripts/load_data.py", "ClickHouse table, rows and the read-only agent user"),
    Step("langfuse", "scripts/seed_langfuse.py", "score configs, prompts and the dashboard"),
    Step("dataset", "scripts/seed_dataset.py", "the 15-question dataset with ground truth"),
]
STEP_NAMES = [s.name for s in STEPS]

Runner = Callable[[List[str]], int]


def plan(only: Optional[Sequence[str]] = None, skip_data: bool = False,
         dry_run: bool = False) -> List[tuple[Step, List[str]]]:
    """The steps to run, in seed order, each with its command line."""
    chosen = set(only or STEP_NAMES)
    if skip_data:
        chosen.discard("data")
    out = []
    for step in STEPS:
        if step.name in chosen:
            cmd = [sys.executable, str(DEMO_DIR / step.script)]
            if dry_run:
                cmd.append("--dry-run")  # all three seeders take it
            out.append((step, cmd))
    return out


def run_subprocess(cmd: List[str]) -> int:
    return subprocess.run(cmd, cwd=DEMO_DIR).returncode


def run(steps: List[tuple[Step, List[str]]], runner: Runner = run_subprocess) -> dict[str, str]:
    """Run each step until one fails. Returns {step name: "ok" | "failed" | "not run"}."""
    results: dict[str, str] = {}
    failed = False
    for step, cmd in steps:
        if failed:
            results[step.name] = "not run"
            continue
        print(f"\n== {step.name}: {step.what}\n   {Path(cmd[1]).relative_to(DEMO_DIR)}"
              + (" " + " ".join(cmd[2:]) if len(cmd) > 2 else ""), flush=True)
        code = runner(cmd)
        results[step.name] = "ok" if code == 0 else "failed"
        if code != 0:
            print(f"   {step.name} failed (exit {code})", flush=True)
            failed = True
    return results


def summary(results: dict[str, str]) -> str:
    return ", ".join(f"{name} {state}" for name, state in results.items()) or "nothing to run"


def main(argv: Optional[Sequence[str]] = None, runner: Runner = run_subprocess) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=STEP_NAMES, help="run only these steps (still in seed order)")
    ap.add_argument("--skip-data", action="store_true",
                    help="skip the ClickHouse load; seed the Langfuse side only")
    ap.add_argument("--dry-run", action="store_true", help="pass --dry-run to every seeder: report, write nothing")
    args = ap.parse_args(argv)

    steps = plan(args.only, args.skip_data, args.dry_run)
    results = run(steps, runner)
    print(f"\nseed_all: {summary(results)}" + (" (dry run)" if args.dry_run else ""))
    return 1 if "failed" in results.values() else 0


if __name__ == "__main__":
    sys.exit(main())
