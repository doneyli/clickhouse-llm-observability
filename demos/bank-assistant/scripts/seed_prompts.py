"""Seed the system prompt lifecycle (EXP-04): v1 production, v2 staging, v3 development.

Idempotent: a version is only created when the latest stored text differs.
Run: .venv/bin/python scripts/seed_prompts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, prompts  # noqa: E402

VERSIONS = [
    (prompts.V1_BASELINE, ["production"], "v1 baseline — KB first, banking tools, decline off-topic"),
    (prompts.V2_CANDIDATE, ["staging"], "v2 — inline citations, no guessing, advice + security rules"),
    (prompts.V3_REGRESSION, ["development"], "v3 'growth' rewrite — upsell Premier/investments (expect CI gate to block)"),
]


def main():
    lf = config.get_langfuse()
    try:
        existing = config.api("GET", "/api/public/v2/prompts", params={"name": prompts.PROMPT_NAME})["data"]
        texts = set()
        for v in (existing[0]["versions"] if existing else []):
            texts.add(config.api("GET", f"/api/public/v2/prompts/{prompts.PROMPT_NAME}",
                                 params={"version": v})["prompt"])
    except RuntimeError:
        texts = set()
    for text, labels, msg in VERSIONS:
        if text in texts:
            print(f"= exists: {msg}")
            continue
        p = lf.create_prompt(name=prompts.PROMPT_NAME, prompt=text, labels=labels, type="text",
                             config={"model": config.AGENT_MODEL, "temperature": 0.2, "owner": "retail-digital"},
                             tags=["northwind-assistant", "system"], commit_message=msg)
        print(f"+ v{p.version} {labels} — {msg}")
    print(f"Prompts: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/prompts/{prompts.PROMPT_NAME}")


if __name__ == "__main__":
    main()
