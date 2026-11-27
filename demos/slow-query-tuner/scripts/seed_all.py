"""
Seed prompts + dataset + monitors in one shot (idempotent). The demo's one seed entry point.

From the repo root on the host (what `commands.seed` in demo.yaml runs):

    python demos/slow-query-tuner/scripts/seed_all.py

On the host it first checks that the root `.env` exists and sets LANGFUSE_PUBLIC_KEY and
LANGFUSE_SECRET_KEY, then hands itself to the demo's container, which has the
dependencies and reads the root `.env` through compose's `env_file`, and returns the
container's exit code:

    docker compose --profile demo run --rm slow-query-tuner python scripts/seed_all.py

setup.sh's per-demo seeding block runs that container form directly. Pass `--local` to run
the seeders in the current Python instead (it needs requirements.txt): shell-exported
LANGFUSE_* are dropped and the root `.env` is loaded first.

The container is recognised positively: the compose service sets TUNING_CH_HOST, and the
runtime writes /.dockerenv (Docker) or /run/.containerenv (Podman). Both must be present.

Inside the container it runs the three seeders in order: prompts (type, text, config and
labels reconciled), dataset (items upserted by stable id only when they differ, then read
back) and monitors (none on a v3 server). Each prints what it created, updated or left
unchanged. Exit 1 when the keys are missing or any seeder fails; the others still run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

_HERE = Path(__file__).resolve().parent
DEMO_DIR = _HERE.parent
REPO_ROOT = DEMO_DIR.parent.parent
ROOT_ENV = REPO_ROOT / ".env"
sys.path.insert(0, str(DEMO_DIR))       # demo root (langfuse_config, queries, prompts)
sys.path.insert(0, str(_HERE))          # this scripts/ dir (sibling seeders, verify_demo)

import verify_demo as v  # noqa: E402  (stdlib only: .env parsing)

CONTAINER_CMD = ["docker", "compose", "--profile", "demo", "run", "--rm", "slow-query-tuner",
                 "python", "scripts/seed_all.py"]
REQUIRED_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def in_container(environ: Optional[Mapping[str, str]] = None, markers=CONTAINER_MARKERS) -> bool:
    """True only inside the slow-query-tuner service: its compose env and a runtime marker."""
    env = os.environ if environ is None else environ
    return bool(env.get("TUNING_CH_HOST")) and any(os.path.exists(m) for m in markers)


def check_root_env(path: Optional[Path] = None) -> Tuple[bool, str]:
    """(ok, message) for the root .env the container will read through env_file."""
    path = ROOT_ENV if path is None else path
    if not path.is_file():
        return False, f"{path} is missing: run ./setup.sh at the repo root"
    values = v.parse_env(path.read_text())
    missing = [k for k in REQUIRED_KEYS if not values.get(k)]
    if missing:
        return False, f"{path} does not set {', '.join(missing)}: run ./setup.sh at the repo root"
    return True, f"{path} sets {', '.join(REQUIRED_KEYS)}"


def container_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment for the compose call: the shell's, minus LANGFUSE_*.

    The service reads its keys from the root .env (env_file); a shell-exported key for
    another project must not reach compose's ${...} interpolation either.
    """
    env = dict(os.environ if environ is None else environ)
    return {k: val for k, val in env.items() if not k.startswith("LANGFUSE_")}


def run_in_container() -> int:
    ok, message = check_root_env()
    if not ok:
        print(f"ERROR: {message}")
        return 1
    print("Running the seed inside the slow-query-tuner container (it has the deps and the root .env):")
    print("  " + " ".join(CONTAINER_CMD))
    try:
        return subprocess.call(CONTAINER_CMD, cwd=str(REPO_ROOT), env=container_env())
    except OSError as e:
        print(f"ERROR: could not run docker ({e}). Run the command above where Docker is available.")
        return 1


def load_local_env() -> bool:
    """--local: drop shell LANGFUSE_*, load the root .env, derive LANGFUSE_HOST for seed_monitors."""
    ok, message = check_root_env()
    if not ok:
        print(f"ERROR: {message}")
        for k in [k for k in os.environ if k.startswith("LANGFUSE_")]:
            del os.environ[k]
        return False
    v.load_env(ROOT_ENV)
    if not os.environ.get("LANGFUSE_HOST") and os.environ.get("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]
    return True


def run_seeders() -> int:
    import langfuse_config as lf  # noqa: E402 — after the env is final
    import seed_dataset  # noqa: E402
    import seed_monitors  # noqa: E402
    import seed_prompts  # noqa: E402

    if not lf.is_langfuse_enabled():
        print("ERROR: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set, so nothing can be seeded. "
              "Run ./setup.sh at the repo root to write them to .env.")
        return 1
    failed: List[str] = []
    for name, fn in (("prompts", seed_prompts.main),
                     ("dataset", seed_dataset.main),
                     ("monitors", seed_monitors.main)):
        print(f"\n### seed {name} ###")
        try:
            rc = fn()
        except Exception as e:  # noqa: BLE001 — report, keep seeding the rest, fail at the end
            print(f"ERROR: seeding {name} failed: {e}")
            rc = 1
        if rc:
            failed.append(name)
    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    print("\nAll seeders succeeded.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--local" in args:
        return run_seeders() if load_local_env() else 1
    if not in_container():
        return run_in_container()
    return run_seeders()


if __name__ == "__main__":
    sys.exit(main())
