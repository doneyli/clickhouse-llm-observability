"""
Seed prompts + dataset + monitors in one shot (idempotent). The demo's one seed entry point.

From the repo root on the host (what `commands.seed` in demo.yaml runs):

    python demos/slow-query-tuner/scripts/seed_all.py

On the host this hands itself to the demo's container, which has the dependencies and the
root `.env`, and returns the container's exit code:

    docker compose --profile demo run --rm slow-query-tuner python scripts/seed_all.py

setup.sh's per-demo seeding block runs that container form directly. Pass `--local` to run
the seeders in the current Python instead (it needs requirements.txt and the keys exported).
Inside the container it
runs the three seeders in order: prompts (a new version only when the text drifted),
dataset items (upserted by id) and monitors (printed as UI fields on a v3 server). Each
seeder prints what it created, updated or left unchanged. Exit 1 when the Langfuse keys
are missing or any seeder fails; the others still run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
DEMO_DIR = _HERE.parent
REPO_ROOT = DEMO_DIR.parent.parent
sys.path.insert(0, str(DEMO_DIR))       # demo root (langfuse_config, queries, prompts)
sys.path.insert(0, str(_HERE))          # this scripts/ dir (sibling seeders)

CONTAINER_CMD = ["docker", "compose", "--profile", "demo", "run", "--rm", "slow-query-tuner",
                 "python", "scripts/seed_all.py"]


def in_container(markers=("/.dockerenv", "/run/.containerenv")) -> bool:
    """True inside the slow-query-tuner container (or any Docker/Podman container)."""
    return any(os.path.exists(m) for m in markers)


def container_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment for the compose call: the shell's, minus LANGFUSE_*.

    The service reads its keys from the root .env (env_file); a shell-exported key for
    another project must not reach compose's ${...} interpolation either.
    """
    env = dict(os.environ if environ is None else environ)
    return {k: val for k, val in env.items() if not k.startswith("LANGFUSE_")}


def run_in_container() -> int:
    print("Running the seed inside the slow-query-tuner container (it has the deps and the root .env):")
    print("  " + " ".join(CONTAINER_CMD))
    try:
        return subprocess.call(CONTAINER_CMD, cwd=str(REPO_ROOT), env=container_env())
    except FileNotFoundError:
        print("ERROR: docker not found. Run the command above where Docker is available.")
        return 1


def run_seeders() -> int:
    import langfuse_config as lf  # noqa: E402 — after sys.path is set
    import seed_dataset  # noqa: E402
    import seed_monitors  # noqa: E402
    import seed_prompts  # noqa: E402

    if not lf.is_langfuse_enabled():
        print("ERROR: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set, so nothing can be seeded. "
              "Run ./setup.sh at the repo root to write them to .env.")
        return 1
    rc = 0
    failed: List[str] = []
    for name, fn in (("prompts", seed_prompts.main),
                     ("dataset", seed_dataset.main),
                     ("monitors", seed_monitors.main)):
        print(f"\n### seed {name} ###")
        try:
            rc |= fn() or 0
        except Exception as e:  # noqa: BLE001 — report, keep seeding the rest, fail at the end
            print(f"ERROR: seeding {name} failed: {e}")
            failed.append(name)
    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        rc = rc or 1
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--local" not in args and not in_container():
        return run_in_container()
    return run_seeders()


if __name__ == "__main__":
    sys.exit(main())
