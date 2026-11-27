#!/usr/bin/env python3
"""
Seed everything the workshop needs, in order. The one seed entry point (`commands.seed`).

    .venv/bin/python scripts/seed_all.py                # definitions, then traffic only if the corpus is missing
    .venv/bin/python scripts/seed_all.py --no-traffic   # definitions only: no model calls, under a minute
    .venv/bin/python scripts/seed_all.py --traffic      # also add ~26 agent runs (about $6-8 of Claude usage, ~5 min)
    .venv/bin/python scripts/seed_all.py --dry-run      # print the plan; run nothing, call nothing

It runs the existing seeders as subprocesses, so each one's own output shows:

  prompts     seed_prompts.py             per-customer instructions and Tailspin's version history
  project     seed_project.py all         score configs, annotation queue, dataset, dashboard
  evaluators  seed_evaluators.py          two LLM judges and the Anthropic LLM connection
  traffic     seed_traffic.py             ~26 real agent runs (model calls: the only step that costs money)
  queue       seed_project.py fill-queue  rejected runs into the annotation queue

The order matters: the judges only score runs made after their rules exist, and the queue
is filled from the reviewer scores the traffic writes.

Traffic is the one step that is not idempotent: every call adds runs. Without a flag it
runs only when the project has fewer than MIN_RUNS production agent runs, which is the
number scripts/verify_demo.py needs for READY.

Exits 1 when a step fails or reports a problem line (`!`, `✗`, `FAILED`); the steps after
a failed one are not run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Tuple

SCRIPTS = Path(__file__).resolve().parent
DEMO = SCRIPTS.parent

MIN_RUNS = 20   # verify_demo.py: "N agent runs in the project" passes at 20
TRAFFIC_COST = "~26 real agent runs: about $6-8 of Claude usage, ~5 minutes"


class Step(NamedTuple):
    key: str
    argv: Tuple[str, ...]   # script name, then its arguments
    what: str


STEPS = [
    Step("prompts", ("seed_prompts.py",), "per-customer instructions and Tailspin's version history"),
    Step("project", ("seed_project.py", "all"), "score configs, annotation queue, dataset, dashboard"),
    Step("evaluators", ("seed_evaluators.py",), "two LLM judges and the Anthropic LLM connection"),
    Step("traffic", ("seed_traffic.py",), TRAFFIC_COST),
    Step("queue", ("seed_project.py", "fill-queue"), "rejected runs into the annotation queue"),
]


def tally(lines: List[str]) -> dict:
    """Count the seeders' own markers: `+` created or updated, `=` unchanged, `!`/`✗`/FAILED problem."""
    counts = {"changed": 0, "unchanged": 0, "problems": 0}
    for line in lines:
        s = line.strip()
        if s.startswith(("!", "✗", "FAILED")) or "widget rejected" in s:
            counts["problems"] += 1
        elif s.startswith("+"):
            counts["changed"] += 1
        elif s.startswith("="):
            counts["unchanged"] += 1
    return counts


def run_script(argv: Tuple[str, ...]) -> Tuple[int, List[str]]:
    """Run one seeder from the demo folder, echoing its output as it arrives."""
    proc = subprocess.Popen([sys.executable, "-u", str(SCRIPTS / argv[0]), *argv[1:]], cwd=DEMO,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        print(f"    {lines[-1]}", flush=True)
    return proc.wait(), lines


def production_runs() -> int:
    """Agent runs already in the project (this environment), read back from Langfuse."""
    sys.path.insert(0, str(SCRIPTS))
    import verify_demo   # imports agent.config, which loads this folder's .env
    verify_demo.config.verify_project()
    return len(verify_demo.agent_runs())


def traffic_decision(mode: str, count_runs: Callable[[], int]) -> Tuple[bool, str]:
    """(run it?, why). `mode` is "auto", "always" or "never"; the count is read only in auto."""
    if mode == "never":
        return False, "--no-traffic"
    if mode == "always":
        return True, "--traffic"
    have = count_runs()
    if have >= MIN_RUNS:
        return False, f"{have} agent runs already in the project (needs {MIN_RUNS})"
    return True, f"{have} agent runs in the project, fewer than the {MIN_RUNS} the workshop needs"


def main(argv: Optional[List[str]] = None, run: Callable = run_script,
         count_runs: Callable[[], int] = production_runs) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    traffic = ap.add_mutually_exclusive_group()
    traffic.add_argument("--traffic", action="store_const", const="always", dest="traffic_mode",
                         help=f"run seed_traffic.py even if the corpus exists ({TRAFFIC_COST})")
    traffic.add_argument("--no-traffic", action="store_const", const="never", dest="traffic_mode",
                         help="definitions only: no model calls")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; run nothing, call nothing")
    ap.set_defaults(traffic_mode="auto")
    args = ap.parse_args(argv)

    if args.dry_run:
        for step in STEPS:
            note = ""
            if step.key == "traffic":
                note = {"auto": f"  [only if fewer than {MIN_RUNS} agent runs exist]",
                        "always": "  [forced by --traffic]", "never": "  [skipped by --no-traffic]"}[args.traffic_mode]
            print(f"{step.key:<11} {' '.join(step.argv):<27} {step.what}{note}")
        return 0

    ran = skipped = 0
    totals = {"changed": 0, "unchanged": 0, "problems": 0}
    for step in STEPS:
        if step.key == "traffic":
            go, why = traffic_decision(args.traffic_mode, count_runs)
            if not go:
                print(f"- {step.key}: skipped, {why}")
                skipped += 1
                continue
            print(f"> {step.key}: {' '.join(step.argv)}  ({why}; {TRAFFIC_COST})")
        else:
            print(f"> {step.key}: {' '.join(step.argv)}  ({step.what})")
        code, lines = run(step.argv)
        counts = tally(lines)
        for k in totals:
            totals[k] += counts[k]
        ran += 1
        if code != 0 or counts["problems"]:
            reason = f"exit code {code}" if code != 0 else f"{counts['problems']} problem line(s)"
            print(f"\nFAILED at {step.key} ({reason}); later steps were not run. Fix it and re-run: "
                  "every step except traffic is safe to repeat.")
            return 1

    print(f"\n{ran} step(s) run, {skipped} skipped: {totals['changed']} created or updated, "
          f"{totals['unchanged']} unchanged. Next: .venv/bin/python scripts/verify_demo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
