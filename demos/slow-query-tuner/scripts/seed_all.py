"""
Seed prompts + dataset + monitors in one shot (idempotent, non-fatal). Called by
setup.sh's per-demo seeding block:

    docker compose --profile demo run --rm slow-query-tuner python scripts/seed_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # demo root (langfuse_config, queries, prompts)
sys.path.insert(0, str(_HERE))          # this scripts/ dir (sibling seeders)

import seed_dataset  # noqa: E402
import seed_monitors  # noqa: E402
import seed_prompts  # noqa: E402


def main() -> int:
    rc = 0
    for name, fn in (("prompts", seed_prompts.main),
                     ("dataset", seed_dataset.main),
                     ("monitors", seed_monitors.main)):
        print(f"\n### seed {name} ###")
        try:
            rc |= fn() or 0
        except Exception as e:  # noqa: BLE001 — seeding is best-effort
            print(f"WARN: seeding {name} failed: {e}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
