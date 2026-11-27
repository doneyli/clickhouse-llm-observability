#!/usr/bin/env python3
"""
Seed everything the Support Triage Parallel demo needs in Langfuse in one shot:
the 7 managed prompts and the 2 hosted datasets. The local aggregator cases are
printed, not hosted.

The independent managed judge is a separate root-level shell script
(``scripts/seed-support-triage-evaluators.sh``) because it upserts Postgres
job_configurations like the other evaluator seeds.

Idempotent and drift-reconciling (see ``seed_prompts.py`` and ``seed_datasets.py``):
an unchanged project gets no writes. Prints created / updated / unchanged per
artifact and exits 1 if any part failed; a failure in one seeder still lets the
other run.

Where the keys come from:
- On the host (``commands.seed``, run from the repo root), the root ``.env`` is the
  only source of ``LANGFUSE_*``: inherited shell exports are cleared first, and a
  missing file or a missing key fails before any Langfuse call.
      python demos/support-triage-parallel/scripts/seed_all.py
- Inside the ``support-triage-parallel`` container (what ``./setup.sh`` runs), the
  compose environment is used as it is: ``env_file: .env`` plus the internal
  ``LANGFUSE_HOST``. The container is recognised positively: the demo folder is
  mounted at ``/app`` and Docker's ``/.dockerenv`` marker is present.
      docker compose --profile langfuse --profile demo run --rm support-triage-parallel python scripts/seed_all.py
"""

import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # demo root (for sql_voting etc.)
sys.path.insert(0, _HERE)                    # scripts dir (for sibling imports)

import verify_demo  # noqa: E402  (stdlib only: the .env rule)

CONTAINER_DEMO_DIR = Path("/app")             # docker-compose.yaml mounts the demo folder here
DOCKER_MARKER = Path("/.dockerenv")
REQUIRED = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


def in_container(demo_dir: Path = verify_demo.DEMO_DIR, marker: Path = DOCKER_MARKER) -> bool:
    """True only inside the compose service: the demo folder is /app and Docker's marker exists."""
    return demo_dir == CONTAINER_DEMO_DIR and marker.exists()


def prepare_env(environ=None, container=None) -> str:
    """Settle the env the seeders read, or raise SystemExit before any Langfuse call.

    Returns where it came from: "compose" or the root .env path.
    """
    env = os.environ if environ is None else environ
    container = in_container() if container is None else container
    if container:
        source = "compose"
    else:
        path = verify_demo.REPO_ROOT / ".env"
        if not verify_demo.load_env(path, env):  # clears inherited LANGFUSE_* either way
            raise SystemExit(f"No root .env at {path}: run ./setup.sh at the repo root first.")
        source = str(path)
    base = env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST")
    missing = [k for k in REQUIRED if not env.get(k)] + ([] if base else ["LANGFUSE_BASE_URL"])
    if missing:
        raise SystemExit(f"{', '.join(missing)} not set in {source}.")
    # One target for both seeders: seed_prompts.py reads LANGFUSE_HOST, the SDK prefers
    # LANGFUSE_BASE_URL.
    env["LANGFUSE_HOST"] = env["LANGFUSE_BASE_URL"] = base
    return source


def run(step, name) -> bool:
    """Run one seeder's main(); report its failure instead of stopping the other."""
    try:
        step()
    except SystemExit as e:
        if e.code in (None, 0):
            return True
        print(f"  {name} failed: {e.code}")
        return False
    except Exception as e:  # noqa: BLE001 - surface it, keep going, fail at the end
        print(f"  {name} failed: {type(e).__name__}: {e}")
        return False
    return True


def main() -> int:
    source = prepare_env()
    print(f"Using {source} for LANGFUSE_*" + ("" if source == "compose" else " (shell exports ignored)"))
    # Imported after the env is settled: both read LANGFUSE_* when they load.
    import seed_prompts   # noqa: E402
    import seed_datasets  # noqa: E402

    print("== Seeding managed prompts ==")
    ok = run(seed_prompts.main, "prompts")
    print("\n== Seeding datasets ==")
    ok = run(seed_datasets.main, "datasets") and ok
    if not ok:
        print("\nSeeding incomplete: fix what failed above and run again (it is idempotent).")
        return 1
    print("\nAll done. Next: ./scripts/seed-support-triage-evaluators.sh (managed judge).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
