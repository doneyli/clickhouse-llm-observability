#!/usr/bin/env python3
"""
Seed Langfuse-managed prompts for the Support Triage Parallel demo (Deploy node).

Seven prompts, fetched by ``label=production`` at runtime with in-code fallbacks
(see triage_pipeline.py / sql_voting.py). Editing one in the Langfuse UI — or
promoting a new version to ``production`` — changes behaviour on the next run
with no redeploy, and every generation links the prompt version that produced it.

The prompt TEXTS mirror the local fallback templates (Langfuse {{var}} syntax).
Keep them in sync by hand — when managed and fallback match, enabling prompt
management changes nothing until you edit the prompt in Langfuse.

``support-triage-sql-voter`` is seeded in TWO versions (v1 baseline, v2 promoted
to ``production``) so the Prompts tab shows version history + linked generations.

Idempotent: a prompt is (re)created only if missing or its production text
differs from what's checked in here — re-running is a no-op.

Usage (from repo root, after sourcing .env):
    LANGFUSE_HOST=http://localhost:3001 \
    LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... \
    python demos/support-triage-parallel/scripts/seed_prompts.py
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

BRANCH_MODEL = os.getenv("BRANCH_MODEL", "claude-haiku-4-5")
SONNET = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-opus-4-7")

_auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()
_HEADERS = {"Authorization": f"Basic {_auth}", "Content-Type": "application/json"}
LABEL = "production"

# --------------------------------------------------------------------------- #
# Prompt texts — MIRROR the in-code fallbacks in triage_pipeline.py / sql_voting.py
# --------------------------------------------------------------------------- #
SUMMARY = (
    "Summarize the following ClickHouse support ticket in exactly two short, "
    "factual sentences. Do not speculate.\n\nTicket:\n{{ticket_body}}\n\nSummary:"
)
SENTIMENT = (
    "Analyze the customer's tone in this support ticket. Reply with ONLY a JSON "
    'object: {"sentiment": "positive|neutral|negative", '
    '"urgency": "low|medium|high"}.\n\nTicket:\n{{ticket_body}}\n\nJSON:'
)
CATEGORY = (
    "Classify this ClickHouse support ticket into exactly ONE category from this "
    "list: query-performance, ingestion, replication, billing, schema-migration, "
    "connectivity, other. Reply with ONLY the category label.\n\n"
    "Ticket:\n{{ticket_body}}\n\nCategory:"
)
POLICY_GUARD = (
    "You are a policy/PII guardrail. Screen the ticket for personal data "
    "(emails, phone numbers), leaked credentials/API keys, or abusive content. "
    'Reply with ONLY JSON: {"flagged": true|false, "reasons": ["..."]}.\n\n'
    "Ticket:\n{{ticket_body}}\n\nJSON:"
)
SYNTHESIS = (
    "You are a support triage lead. Merge the labeled analysis branches below "
    "into a concise triage brief (owner-ready): one-line summary, "
    "sentiment/urgency, category, and any policy flags. If a branch reads "
    "'insufficient data', you MUST say so explicitly for that dimension and do "
    "not invent it.\n\nBranch outputs (JSON):\n{{branch_outputs}}\n\nTriage brief:"
)
SQL_VOTER_V1 = (
    "You are a ClickHouse SQL expert. Write a single read-only ClickHouse SELECT "
    "that answers the question using the public demo datasets (nyc_taxi, github, "
    "hackernews, uk, stackoverflow). Reply with ONLY the SQL.\n\n"
    "Question: {{question}}\n\nSQL:"
)
# v2 (production) mirrors the in-code fallback in sql_voting.py exactly.
SQL_VOTER_V2 = (
    "You are a ClickHouse SQL expert. Write a SINGLE read-only ClickHouse SELECT "
    "that answers the question using the public demo datasets (nyc_taxi, github, "
    "hackernews, uk, stackoverflow). Always qualify database.table, prefer an "
    "explicit GROUP BY, and add a LIMIT. Reply with ONLY the SQL — no prose, no "
    "code fences.\n\nQuestion: {{question}}\n\nSQL:"
)
TIE_BREAK = (
    "The following SQL candidates tied in a majority vote. Pick the ONE that is "
    "most correct and consistent for the question, using the result previews as "
    "evidence. Reply with ONLY the integer index of the best candidate.\n\n"
    "Question: {{question}}\n\nCandidates:\n{{candidates}}\n\n"
    "Result previews:\n{{result_previews}}\n\nBest candidate index:"
)

# name -> (text, config, commit message)
SINGLE_VERSION = [
    ("support-triage-summary", SUMMARY, {"model": BRANCH_MODEL, "temperature": 0.3},
     "2-sentence factual summary of the ticket"),
    ("support-triage-sentiment", SENTIMENT, {"model": BRANCH_MODEL, "temperature": 0.3},
     "JSON {sentiment, urgency}"),
    ("support-triage-category", CATEGORY, {"model": BRANCH_MODEL, "temperature": 0.0},
     "One label from the fixed category taxonomy"),
    ("support-triage-policy-guard", POLICY_GUARD, {"model": BRANCH_MODEL, "temperature": 0.0},
     "PII / credential / abuse screen -> JSON {flagged, reasons}"),
    ("support-triage-synthesis", SYNTHESIS, {"model": SONNET, "temperature": 0.5},
     "Merge labeled branch outputs into a triage brief (states insufficient data)"),
    ("support-triage-tie-break-judge", TIE_BREAK, {"model": JUDGE_MODEL, "temperature": 0.0},
     "USC-style tie-break: pick most consistent candidate"),
]


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


def _create(name: str, text: str, labels, config: dict, message: str) -> dict:
    body = {"name": name, "type": "text", "prompt": text, "labels": labels,
            "config": config, "commitMessage": message}
    req = urllib.request.Request(f"{HOST}/api/public/v2/prompts",
                                 data=json.dumps(body).encode(), headers=_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _seed_single(name, text, config, message):
    existing = _get(name, LABEL)
    if existing is not None and (existing.get("prompt") or "").strip() == text.strip():
        print(f"  ✓ {name} [{LABEL}] already up to date (v{existing.get('version')})")
        return
    created = _create(name, text, [LABEL], config, message)
    verb = "updated" if existing is not None else "created"
    print(f"  + {name} [{LABEL}] {verb} (v{created.get('version')})")


def _seed_voter():
    name = "support-triage-sql-voter"
    config = {"model": SONNET, "temperature": 0.9}
    existing = _get(name, LABEL)
    if existing is not None and (existing.get("prompt") or "").strip() == SQL_VOTER_V2.strip():
        print(f"  ✓ {name} [{LABEL}] already up to date (v{existing.get('version')})")
        return
    if existing is None:
        v1 = _create(name, SQL_VOTER_V1, [], config, "Baseline SQL voter prompt")
        print(f"  + {name} v{v1.get('version')} created (baseline)")
    v2 = _create(name, SQL_VOTER_V2, [LABEL], config,
                 "Qualify database.table + explicit GROUP BY + LIMIT; promote to production")
    print(f"  + {name} v{v2.get('version')} created, labels={v2.get('labels')}")


def main():
    if not PK or not SK:
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first).")
    print(f"Seeding support-triage prompts at {HOST} ...")
    for name, text, config, message in SINGLE_VERSION:
        _seed_single(name, text, config, message)
    _seed_voter()
    print(f"\nDone. View: {HOST} → Prompts. The demo fetches these by label=production at runtime.")


if __name__ == "__main__":
    main()
