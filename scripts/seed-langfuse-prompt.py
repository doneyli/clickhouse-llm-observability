#!/usr/bin/env python3
"""
Seed the Langfuse-managed generation prompt for the Agentic RAG demo.

Creates `agentic-rag-generation` with two versions to showcase prompt
management + versioning:
  v1  baseline
  v2  grounding rules + source citation  (labeled `production`)

The agentic-rag generate node pulls this prompt at runtime via
langfuse.get_prompt(..., label="production"), so editing it in the Langfuse UI
changes the agent's behavior without a redeploy, and every trace links the
prompt version that produced the answer.

Idempotent-ish: re-running appends new versions. Usage:
    LANGFUSE_HOST=http://localhost:3001 \
    LANGFUSE_PUBLIC_KEY=pk-lf-1234567890 LANGFUSE_SECRET_KEY=sk-lf-1234567890 \
    python scripts/seed-langfuse-prompt.py
"""

import base64
import json
import os
import urllib.request

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
SK = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
NAME = os.getenv("GEN_PROMPT_NAME", "agentic-rag-generation")

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


V1 = (
    "Answer the question using ONLY the provided context. "
    "If the context is insufficient, say what is missing. Be concise and accurate.\n\n"
    "Context:\n{{context}}\n\nQuestion: {{question}}\n\nAnswer:"
)

V2 = (
    "You are a retrieval-augmented assistant for ClickHouse, RAG and LLM-observability questions.\n"
    "Answer the QUESTION using ONLY the CONTEXT below.\n"
    "Rules:\n"
    "- Use only facts present in the context; do not rely on prior knowledge.\n"
    "- If the context is insufficient, state exactly what is missing.\n"
    "- Cite the document titles you relied on.\n"
    "- Be concise, accurate and grounded.\n\n"
    "CONTEXT:\n{{context}}\n\nQUESTION: {{question}}\n\nANSWER:"
)

CONFIG = {"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "temperature": 0.3}


def main():
    print(f"Seeding prompt '{NAME}' at {HOST} ...")
    v1 = _create({"name": NAME, "type": "text", "prompt": V1, "labels": [],
                  "config": CONFIG, "commitMessage": "Baseline RAG generation prompt"})
    print(f"  v{v1.get('version')} created (baseline)")
    v2 = _create({"name": NAME, "type": "text", "prompt": V2, "labels": ["production"],
                  "config": CONFIG,
                  "commitMessage": "Add grounding rules + source citation; promote to production"})
    print(f"  v{v2.get('version')} created, labels={v2.get('labels')}")
    print("Done. The agentic-rag generate node will use label=production.")


if __name__ == "__main__":
    main()
