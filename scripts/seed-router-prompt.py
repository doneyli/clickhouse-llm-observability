#!/usr/bin/env python3
"""
Seed the Langfuse-managed router prompt for the Query Router demo.

Creates `query-router-classifier` with two versions to showcase prompt
management + versioning (same v1/v2 showcase shape as seed-langfuse-prompt.py):
  v1  baseline               (taxonomy + JSON schema only, no label)
  v2  few-shot + calibration (labeled `production`)

The router pulls this prompt at runtime via
langfuse.get_prompt("query-router-classifier", label="production"), links the
version to the `route-query` generation, and falls back to a local template if
Langfuse is unavailable. config.temperature=0.0 (routing wants determinism — a
deliberate contrast with the demos' default TEMPERATURE=0.7).

Idempotent-ish: re-running appends new versions. Usage:
    LANGFUSE_HOST=http://localhost:3001 \
    LANGFUSE_PUBLIC_KEY=pk-lf-1234567890 LANGFUSE_SECRET_KEY=sk-lf-1234567890 \
    python scripts/seed-router-prompt.py
"""

import base64
import json
import os
import urllib.request

HOST = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001")).rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
SK = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
NAME = os.getenv("ROUTER_PROMPT_NAME", "query-router-classifier")
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "claude-haiku-4-5")

_auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()


def _create(body: dict) -> dict:
    req = urllib.request.Request(
        f"{HOST}/api/public/v2/prompts",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Basic {_auth}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# v1 — baseline: taxonomy + JSON schema only.
V1 = (
    "You are the front-door router for a data-and-docs assistant. Classify the "
    "question into exactly ONE route:\n"
    "- analytics_sql : needs LIVE numbers from ClickHouse public datasets "
    "(taxi rides, github stars, stackoverflow, prices, ...)\n"
    "- docs_simple   : a single factual/definitional question answerable from docs\n"
    "- docs_complex  : multi-part, comparative, or accuracy-critical doc questions "
    "that merit retrieval verification\n"
    "- out_of_scope  : none of the above (small talk, unrelated domains, unsafe asks)\n\n"
    'Respond ONLY with JSON: {"route": "...", "confidence": 0.0-1.0, "rationale": "..."}\n\n'
    "Question: {{question}}"
)

# v2 — few-shot + confidence calibration + explicit out_of_scope guidance.
V2 = (
    "You are the front-door router for a data-and-docs assistant. Classify the "
    "question into exactly ONE route:\n"
    "- analytics_sql : needs LIVE numbers from ClickHouse public datasets "
    "(taxi rides, github stars, stackoverflow, prices, ...)\n"
    "- docs_simple   : a single factual/definitional question answerable from docs\n"
    "- docs_complex  : multi-part, comparative, or accuracy-critical doc questions "
    "that merit retrieval verification\n"
    "- out_of_scope  : none of the above (small talk, unrelated domains, unsafe asks)\n\n"
    'Respond ONLY with JSON: {"route": "...", "confidence": 0.0-1.0, "rationale": "..."}\n\n'
    "Calibration: confidence reflects P(correct route). If the question BOTH asks "
    "for live numbers AND a conceptual explanation, pick the dominant intent and "
    "LOWER the confidence. Mixed-intent or vague questions must score below 0.7. "
    "Small talk, unrelated domains, or unsafe requests are out_of_scope.\n\n"
    "Examples:\n"
    'Q: "How many taxi rides in NYC in July 2015?"  -> {"route": "analytics_sql", "confidence": 0.96, "rationale": "asks for a live count from a dataset"}\n'
    'Q: "What is a vector index?"  -> {"route": "docs_simple", "confidence": 0.93, "rationale": "single definitional doc question"}\n'
    'Q: "Compare ClickHouse-native vectors with Chroma and when each wins."  -> {"route": "docs_complex", "confidence": 0.9, "rationale": "comparative, verification-worthy"}\n'
    'Q: "Write me a poem about databases."  -> {"route": "out_of_scope", "confidence": 0.95, "rationale": "creative, unrelated to data/docs"}\n'
    'Q: "Is ClickHouse fast?"  -> {"route": "docs_simple", "confidence": 0.5, "rationale": "vague — could mean docs or a benchmark number"}\n\n'
    "Question: {{question}}"
)

CONFIG = {"model": ROUTER_MODEL, "temperature": 0.0}


def main():
    print(f"Seeding prompt '{NAME}' at {HOST} ...")
    # v1 is labeled `baseline` (NOT production) so the experiment can fetch it by
    # label to compare against production — the router only ever runs `production`.
    v1 = _create({"name": NAME, "type": "text", "prompt": V1, "labels": ["baseline"],
                  "config": CONFIG, "commitMessage": "Baseline router: taxonomy + JSON schema"})
    print(f"  v{v1.get('version')} created (baseline)")
    v2 = _create({"name": NAME, "type": "text", "prompt": V2, "labels": ["production"],
                  "config": CONFIG,
                  "commitMessage": "Few-shot + confidence calibration; promote to production"})
    print(f"  v{v2.get('version')} created, labels={v2.get('labels')}")
    print("Done. The router will use label=production; vary it in the experiment (v1 vs production).")


if __name__ == "__main__":
    main()
