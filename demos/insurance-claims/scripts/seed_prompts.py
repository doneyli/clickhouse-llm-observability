"""
Seed every agent prompt in Langfuse (idempotent): v1 of each name with labels
`production` + `previous-production`, and the model/parameters in `config`.

    .venv/bin/python scripts/seed_prompts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import config, prompts  # noqa: E402

CONFIGS = {
    "doc-intake": {"model": config.INTAKE_MODEL, "max_tokens": 2048, "temperature": 0},
}


def main() -> int:
    lf = config.get_langfuse()
    for name, spec in prompts.PROMPTS.items():
        try:
            existing = lf.get_prompt(name, label="production", cache_ttl_seconds=0, type="text")
            print(f"= {name} v{existing.version} already has production")
            continue
        except Exception:  # noqa: BLE001 — not found → create
            pass
        p = lf.create_prompt(name=name, prompt=spec["text"], type="text",
                             labels=["production", "previous-production"],
                             config=CONFIGS.get(name, {"model": config.AGENT_MODEL}),
                             commit_message="v1 baseline (seed_prompts.py)")
        print(f"+ {name} v{p.version} → production")
    config.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
