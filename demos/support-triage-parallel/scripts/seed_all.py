#!/usr/bin/env python3
"""
Seed everything the Support Triage Parallel demo needs in Langfuse in one shot:
the 7 managed prompts and the 2 hosted datasets. The local aggregator cases are
printed, not hosted.

The independent managed judge is a separate root-level shell script
(``scripts/seed-support-triage-evaluators.sh``) because it upserts Postgres
job_configurations like the other evaluator seeds.

Idempotent: a prompt gets a new version only when its production text differs
from ``seed_prompts.py``; datasets are created if missing and items are upserted
by a stable id.

Usage, from the repo root (reads the root .env; shell-exported LANGFUSE_* are ignored):
    python demos/support-triage-parallel/scripts/seed_all.py

Inside the container (what ./setup.sh runs), the environment comes from compose:
    docker compose --profile langfuse --profile demo run --rm support-triage-parallel python scripts/seed_all.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # demo root (for sql_voting etc.)
sys.path.insert(0, _HERE)                    # scripts dir (for sibling imports)

import verify_demo  # noqa: E402  (stdlib only: the .env rule)


def load_root_env(environ=None) -> bool:
    """On the host, make the root .env the only source of LANGFUSE_*.

    Inside the container the demo folder is mounted at /app, so there is no root .env
    and the compose environment (env_file + LANGFUSE_HOST) is left as it is. Returns
    whether the file was loaded. seed_prompts.py reads LANGFUSE_HOST, so it is filled
    from LANGFUSE_BASE_URL when the file sets only that.
    """
    env = os.environ if environ is None else environ
    path = verify_demo.REPO_ROOT / ".env"
    if not path.is_file():
        return False
    verify_demo.load_env(path, env)
    if env.get("LANGFUSE_BASE_URL") and not env.get("LANGFUSE_HOST"):
        env["LANGFUSE_HOST"] = env["LANGFUSE_BASE_URL"]
    return True


def main():
    if load_root_env():
        print(f"Using {verify_demo.REPO_ROOT / '.env'} (shell-exported LANGFUSE_* ignored)")
    # Imported after the env is settled: both read LANGFUSE_* when they load.
    import seed_prompts   # noqa: E402
    import seed_datasets  # noqa: E402

    print("== Seeding managed prompts ==")
    seed_prompts.main()
    print("\n== Seeding datasets ==")
    seed_datasets.main()
    print("\nAll done. Next: ./scripts/seed-support-triage-evaluators.sh (managed judge).")


if __name__ == "__main__":
    main()
