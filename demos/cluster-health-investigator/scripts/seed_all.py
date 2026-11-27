#!/usr/bin/env python3
"""
Seed everything for the Cluster Health Investigator — the one seed entry point.

    python demos/cluster-health-investigator/scripts/seed_all.py         # from the repo root, on the host
    docker compose --profile langfuse --profile demo run --rm cluster-health \
        python scripts/seed_all.py                                       # in the container (setup.sh)

Steps, each reconciling its own assets (created / updated / unchanged per item):
    1. managed prompts        scripts/seed_prompts.py    (3 prompts, 5 labels)
    2. evaluation datasets    scripts/seed_datasets.py   (8 + 10 golden items)
    3. managed LLM judges     scripts/seed_evaluators.sh (self-hosted Postgres via `docker exec`)
    4. [--with-traces] live demo traffic (scripts/run_live_demo.py; model calls)

Environment. On the host this drops every inherited LANGFUSE_* and loads the root `.env`
before anything runs, so a shell export for another project can never be the target. In
the container (identified positively; see verify_demo.in_container) compose has already
set them from the root `.env`. A missing file or key fails before any request.

Exit code: 0 only when every step that was asked for succeeded. Each step runs as its own
process, so its exit code is what counts, not just whether it raised. The judges need
`docker exec` into `langfuse-postgres`, which only the host can do: in the container that
step fails with the host command to run, unless `--skip-judges` says to leave it out.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, MutableMapping, Optional, Sequence, Tuple

SCRIPTS = Path(__file__).resolve().parent
DEMO_DIR = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from verify_demo import in_container, prepare_env  # noqa: E402  (stdlib only)

HOST_SEED = "python demos/cluster-health-investigator/scripts/seed_all.py"

Step = Tuple[str, Optional[List[str]], str]  # (name, argv or None when it cannot run here, why not)


def plan_steps(*, container: bool, with_traces: bool, skip_judges: bool) -> List[Step]:
    py = sys.executable
    steps: List[Step] = [
        ("Seed prompts", [py, str(SCRIPTS / "seed_prompts.py")], ""),
        ("Seed datasets", [py, str(SCRIPTS / "seed_datasets.py")], ""),
    ]
    if not skip_judges:
        if container:
            steps.append(("Seed managed judges", None,
                          f"needs `docker exec` into langfuse-postgres, which only the host can do. "
                          f"Run `{HOST_SEED}` from the repo root, or pass --skip-judges"))
        else:
            steps.append(("Seed managed judges", ["bash", str(SCRIPTS / "seed_evaluators.sh")], ""))
    if with_traces:
        steps.append(("Seed live traces", [py, str(SCRIPTS / "run_live_demo.py")], ""))
    return steps


def run_steps(steps: Sequence[Step], env: MutableMapping[str, str],
              runner=subprocess.call) -> Dict[str, str]:
    """Run each step with `env`; every step runs even after a failure. Returns name -> outcome."""
    outcomes: Dict[str, str] = {}
    for name, argv, why_not in steps:
        print(f"\n=== {name} ===", flush=True)
        if argv is None:
            print(f"[{name}] FAILED: not run here: {why_not}")
            outcomes[name] = "not run"
            continue
        try:
            rc = runner(argv, cwd=str(DEMO_DIR), env=dict(env))
        except OSError as e:
            rc = None
            print(f"[{name}] FAILED to start: {e}")
        if rc == 0:
            print(f"[{name}] OK")
            outcomes[name] = "ok"
        else:
            if rc is not None:
                print(f"[{name}] FAILED (exit {rc})")
            outcomes[name] = "failed"
    return outcomes


def main(argv: Optional[Sequence[str]] = None, environ: Optional[MutableMapping[str, str]] = None,
         runner=subprocess.call) -> int:
    ap = argparse.ArgumentParser(description="Seed the Cluster Health Investigator (idempotent)")
    ap.add_argument("--with-traces", action="store_true",
                    help="also run scripts/run_live_demo.py to seed varied-shape traces (model calls)")
    ap.add_argument("--skip-judges", action="store_true",
                    help="leave out the managed judges (the container cannot seed them)")
    args = ap.parse_args(argv)
    env = os.environ if environ is None else environ

    container = in_container(env)
    print(f"Cluster Health Investigator — full seed ({'container' if container else 'host'})")
    ok, message = prepare_env(env, container=container)
    print(f"  {message}")
    if not ok:
        print("Seed NOT run: fix the environment first.")
        return 1

    outcomes = run_steps(plan_steps(container=container, with_traces=args.with_traces,
                                    skip_judges=args.skip_judges), env, runner)
    failed = [n for n, o in outcomes.items() if o != "ok"]
    print("\n" + "\n".join(f"  {o:<8} {n}" for n, o in outcomes.items()))
    if args.skip_judges:
        print("  skipped  Seed managed judges (--skip-judges)")
    if failed:
        print(f"\nSeed INCOMPLETE: {', '.join(failed)}.")
        return 1
    print("\nSeed complete. Run the demo:")
    print("  docker compose --profile langfuse --profile demo run --rm cluster-health python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
