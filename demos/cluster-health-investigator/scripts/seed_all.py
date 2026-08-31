#!/usr/bin/env python3
"""
Seed everything for the Cluster Health Investigator — the setup.sh entry point.

Idempotent orchestration (each step is non-fatal):
    1. managed prompts        (scripts/seed_prompts.py)
    2. evaluation datasets     (scripts/seed_datasets.py)
    3. managed LLM judges      (scripts/seed_evaluators.sh — self-hosted Postgres)
    4. [--with-traces] live demo traffic (scripts/run_live_demo.py)

Usage:
    python scripts/seed_all.py
    python scripts/seed_all.py --with-traces
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _step(name: str, fn) -> bool:
    print(f"\n=== {name} ===")
    try:
        rc = fn()
        print(f"[{name}] {'OK' if not rc else 'completed with warnings'}")
        return True
    except Exception as e:
        print(f"[{name}] skipped: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-traces", action="store_true",
                    help="Also run run_live_demo.py to seed varied-shape traces")
    args = ap.parse_args()

    print("Cluster Health Investigator — full seed")

    from scripts.seed_prompts import main as seed_prompts
    from scripts.seed_datasets import main as seed_datasets

    _step("Seed prompts", seed_prompts)
    # seed_datasets parses argv; call with no extra flags
    _saved = sys.argv[:]
    sys.argv = ["seed_datasets"]
    _step("Seed datasets", seed_datasets)
    sys.argv = _saved

    # Managed judges are a shell script (Postgres-coupled, self-hosted only)
    def _judges():
        script = ROOT / "scripts" / "seed_evaluators.sh"
        return subprocess.call(["bash", str(script)])
    _step("Seed managed judges", _judges)

    if args.with_traces:
        from scripts.run_live_demo import main as run_live_demo
        sys.argv = ["run_live_demo"]
        _step("Seed live traces", run_live_demo)
        sys.argv = _saved

    print("\nSeed complete. Run the demo:")
    print("  docker compose --profile langfuse --profile demo run --rm cluster-health python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
