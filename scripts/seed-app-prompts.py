#!/usr/bin/env python3
"""
Seed Langfuse-managed prompts for the text-to-sql and vector-rag demos.

This is the **Deploy** node of the AI Engineering loop for the main stack: the
apps fetch these prompts by label at runtime (with a local fallback), so editing
a prompt in the Langfuse UI — or promoting a new version to `production` — changes
behaviour with no code change or redeploy, and every generation links the prompt
version that produced it.

(The agentic-rag demo has its own prompt via scripts/seed-langfuse-prompt.py;
this script covers the two LangChain apps that previously hard-coded prompts.)

Idempotent: for each (name, label) a new version is created ONLY if the prompt is
missing or its text differs from what's checked in here — re-running is a no-op.

Usage (from repo root, after sourcing .env):
    LANGFUSE_HOST=http://localhost:3001 \
    LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... \
    python scripts/seed-app-prompts.py
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SK = os.getenv("LANGFUSE_SECRET_KEY", "")
CONFIG = {"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "temperature": 0.7}

_auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()
_HEADERS = {"Authorization": f"Basic {_auth}", "Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# Prompt texts — Langfuse {{var}} syntax. These MIRROR the local fallback
# templates in each app (LangChain {var} syntax): text-to-sql/sql_pipeline.py
# and vector-rag/rag_pipeline.py. Keep them in sync by hand — when managed and
# fallback match, enabling prompt management changes nothing until you edit the
# prompt in Langfuse. get_langchain_prompt() converts {{var}} -> {var} at fetch.
# ---------------------------------------------------------------------------

_CLICKHOUSE_DATABASES = """Available databases include:
- amazon: Amazon product data
- bluesky: Bluesky social network data
- covid: COVID-19 data
- dns: DNS query data
- environmental: Environmental data
- forex: Foreign exchange data
- geo: Geographic data
- git: Git repository data
- github: GitHub events and activities
- hackernews: Hacker News posts and comments
- imdb: Internet Movie Database
- logs: Log data
- mta: Metropolitan Transportation Authority data
- noaa: National Oceanic and Atmospheric Administration data
- nyc_taxi: New York City taxi trip data
- nypd: New York Police Department data
- ontime: Airline on-time performance data
- pypi: Python Package Index data
- stackoverflow: Stack Overflow posts and data
- stock: Stock market data
- twitter: Twitter data
- uk: UK property and related data
- wiki: Wikipedia data
- youtube: YouTube video data"""

TEXT_TO_SQL_ANALYSIS = (
    "You are a data analyst with access to ClickHouse at sql.clickhouse.com.\n\n"
    f"{_CLICKHOUSE_DATABASES}\n\n"
    "Question: {{question}}\n\n"
    "Identify which database(s) and data would help answer this question."
)

TEXT_TO_SQL_RESPONSE = (
    "Based on the analysis and context, answer the question.\n\n"
    "Question: {{question}}\n"
    "Analysis: {{analysis}}\n"
    "Context: {{context}}\n\n"
    "Provide a clear, data-driven response."
)

VECTOR_RAG_GENERATION = (
    "Answer the question based on the provided context.\n\n"
    "Context:\n{{context}}\n\n"
    "Question: {{question}}\n\n"
    "Instructions:\n"
    "- Use only information from the context to answer\n"
    "- If the context doesn't contain relevant information, say so\n"
    "- Be concise and accurate\n\n"
    "Answer:"
)

PROMPTS = [
    ("text-to-sql-analysis", TEXT_TO_SQL_ANALYSIS, "Query-analysis prompt (baseline)"),
    ("text-to-sql-response", TEXT_TO_SQL_RESPONSE, "Response-generation prompt (baseline)"),
    ("vector-rag-generation", VECTOR_RAG_GENERATION, "RAG generation prompt (baseline)"),
]
LABEL = "production"


def _get(name: str, label: str):
    url = f"{HOST}/api/public/v2/prompts/{urllib.parse.quote(name)}?label={label}"
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _create(name: str, text: str, label: str, message: str) -> dict:
    body = {"name": name, "type": "text", "prompt": text, "labels": [label],
            "config": CONFIG, "commitMessage": message}
    req = urllib.request.Request(f"{HOST}/api/public/v2/prompts",
                                 data=json.dumps(body).encode(), headers=_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    if not PK or not SK:
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first).")
    print(f"Seeding app prompts at {HOST} ...")
    for name, text, message in PROMPTS:
        existing = _get(name, LABEL)
        if existing is not None and (existing.get("prompt") or "").strip() == text.strip():
            print(f"  ✓ {name} [{LABEL}] already up to date (v{existing.get('version')})")
            continue
        created = _create(name, text, LABEL, message)
        verb = "updated" if existing is not None else "created"
        print(f"  + {name} [{LABEL}] {verb} (v{created.get('version')})")
    print(f"\nDone. View: {HOST} → Prompts. The apps fetch these by label=production at runtime.")


if __name__ == "__main__":
    main()
