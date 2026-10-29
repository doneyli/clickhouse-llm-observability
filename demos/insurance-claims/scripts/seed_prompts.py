"""
Seed every agent prompt in Langfuse (idempotent):
  * v1 of each prompt with label `production` (previous-production is set by the first promote), model and limits in `config`
  * the candidate versions of claims-sop-rules (SPEC.md §7): v2 'concise' → `staging` (F1, the CI gate
    must block it), v3 'conservative' → `conservative` (F4)

Per-agent model choice lives in each prompt's config, so changing a model is a prompt version that goes
through the same CI gate as a wording change.

    .venv/bin/python scripts/seed_prompts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import config, prompts  # noqa: E402

CONFIGS = {
    "doc-intake": {"model": config.INTAKE_MODEL, "max_tokens": 2048},
    "claims-policy-coverage": {"model": "claude-haiku-5-5", "max_tokens": 800},
    "claims-sop-rules": {"model": config.AGENT_MODEL, "max_tokens": 4000},
    "claims-adjudication": {"model": config.AGENT_MODEL, "max_tokens": 1500},
    "claims-correspondence": {"model": config.AGENT_MODEL, "max_tokens": 1200},
    "claims-examiner-assistant": {"model": config.AGENT_MODEL, "max_tokens": 1000},
    "annuity-service-rules": {"model": config.AGENT_MODEL, "max_tokens": 3000},
    "annuity-service-recommendation": {"model": config.AGENT_MODEL, "max_tokens": 1200},
}


def _get(lf, name, label):
    try:
        return lf.get_prompt(name, label=label, cache_ttl_seconds=0, type="text")
    except Exception:  # noqa: BLE001 — not found
        return None


def main() -> int:
    lf = config.get_langfuse()
    for name, spec in prompts.PROMPTS.items():
        existing = _get(lf, name, "production")
        if existing is not None:
            print(f"= {name} v{existing.version} has production")
        else:
            p = lf.create_prompt(name=name, prompt=spec["text"], type="text",
                                 labels=["production"], config=CONFIGS[name],
                                 commit_message="v1 baseline (seed_prompts.py)")
            print(f"+ {name} v{p.version} → production")
        for cand in prompts.CANDIDATES.get(name, []):
            label = cand["labels"][0]
            have = _get(lf, name, label)
            if have is not None and have.prompt == cand["text"]:
                print(f"= {name} v{have.version} has {label}")
                continue
            p = lf.create_prompt(name=name, prompt=cand["text"], type="text", labels=cand["labels"],
                                 config=CONFIGS[name], commit_message=cand["commit"])
            print(f"+ {name} v{p.version} → {', '.join(cand['labels'])}")
    config.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
