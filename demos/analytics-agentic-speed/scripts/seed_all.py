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

Environment: the demo's ``.env`` is required, and every key the planned steps need must be
filled in (a ``<placeholder>`` from ``.env.example`` counts as missing). Every inherited
``LANGFUSE_*`` variable is dropped from the environment the seeders get, so a shell export
for another project can never fill a gap in the file. This is checked before any seeder
runs. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence

DEMO_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = DEMO_DIR / ".env"


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

# What each step reads from the .env. "A|B" means either key will do.
CLICKHOUSE_AGENT = ["CH_HOST", "CH_USER", "CH_PASSWORD"]
LANGFUSE = ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL|LANGFUSE_HOST"]
REQUIRED: Dict[str, List[str]] = {
    "data": CLICKHOUSE_AGENT + ["CH_ADMIN_USER", "CH_ADMIN_PASSWORD"],
    "langfuse": LANGFUSE,
    "dataset": CLICKHOUSE_AGENT + LANGFUSE,
}

Runner = Callable[[List[str], Mapping[str, str]], int]


def parse_env_file(text: str) -> Dict[str, str]:
    """KEY=VALUE lines as python-dotenv reads them: comments, ``export``, quotes."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


def _filled(value: Optional[str]) -> bool:
    v = (value or "").strip()
    return bool(v) and not v.startswith("<")


def missing_keys(env: Mapping[str, str], step_names: Sequence[str]) -> List[str]:
    """The required keys, in order, that ``env`` (the parsed .env) leaves empty."""
    playground = (os.environ.get("EXPLORER_TARGET") or env.get("EXPLORER_TARGET")) == "playground"
    out: List[str] = []
    for name in step_names:
        for spec in REQUIRED[name]:
            if playground and name == "dataset" and spec in CLICKHOUSE_AGENT:
                continue  # the playground supplies its own connection
            if not any(_filled(env.get(k)) for k in spec.split("|")) and spec not in out:
                out.append(spec)
    return out


def child_env(parent: Mapping[str, str]) -> Dict[str, str]:
    """The environment the seeders get: the parent's, minus every LANGFUSE_* variable."""
    return {k: v for k, v in parent.items() if not k.startswith("LANGFUSE_")}


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


def run_subprocess(cmd: List[str], env: Mapping[str, str]) -> int:
    return subprocess.run(cmd, cwd=DEMO_DIR, env=dict(env)).returncode


def run(steps: List[tuple[Step, List[str]]], runner: Runner = run_subprocess,
        env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Run each step until one fails. Returns {step name: "ok" | "failed" | "not run"}."""
    env = child_env(os.environ) if env is None else env
    results: dict[str, str] = {}
    failed = False
    for step, cmd in steps:
        if failed:
            results[step.name] = "not run"
            continue
        print(f"\n== {step.name}: {step.what}\n   {Path(cmd[1]).relative_to(DEMO_DIR)}"
              + (" " + " ".join(cmd[2:]) if len(cmd) > 2 else ""), flush=True)
        code = runner(cmd, env)
        results[step.name] = "ok" if code == 0 else "failed"
        if code != 0:
            print(f"   {step.name} failed (exit {code})", flush=True)
            failed = True
    return results


def summary(results: dict[str, str]) -> str:
    return ", ".join(f"{name} {state}" for name, state in results.items()) or "nothing to run"


def main(argv: Optional[Sequence[str]] = None, runner: Runner = run_subprocess,
         env_file: Optional[Path] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=STEP_NAMES, help="run only these steps (still in seed order)")
    ap.add_argument("--skip-data", action="store_true",
                    help="skip the ClickHouse load; seed the Langfuse side only")
    ap.add_argument("--dry-run", action="store_true", help="pass --dry-run to every seeder: report, write nothing")
    args = ap.parse_args(argv)

    steps = plan(args.only, args.skip_data, args.dry_run)
    if steps:
        path = env_file or ENV_FILE
        if not path.is_file():
            print(f"seed_all: no .env at {path}: copy .env.example to .env and fill it in "
                  "(shell-exported LANGFUSE_* keys are never used)", file=sys.stderr)
            return 1
        missing = missing_keys(parse_env_file(path.read_text()), [s.name for s, _ in steps])
        if missing:
            print(f"seed_all: {path} leaves these empty: {', '.join(missing)}. Nothing was run.",
                  file=sys.stderr)
            return 1
    results = run(steps, runner)
    print(f"\nseed_all: {summary(results)}" + (" (dry run)" if args.dry_run else ""))
    return 1 if "failed" in results.values() else 0


if __name__ == "__main__":
    sys.exit(main())
