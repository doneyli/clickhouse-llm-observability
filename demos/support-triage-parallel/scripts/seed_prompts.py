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

Idempotent and drift-reconciling: a prompt gets a new version, carrying the
``production`` label, only when the version that label serves today differs from
what's checked in here in type, text or config (or no version carries the label).
Re-running an unchanged project appends nothing. Each prompt prints ``created``,
``updated`` or ``unchanged``; a failed write is reported, the rest still run, and
``main()`` exits non-zero if any failed.

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

# The target and keys are read when a request is made, not at import, so a caller that
# settles the environment first (seed_all.py) is always the one that decides.
def _host() -> str:
    return os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")

BRANCH_MODEL = os.getenv("BRANCH_MODEL", "claude-haiku-4-5")
SONNET = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-opus-4-7")

LABEL = "production"
VOTER = "support-triage-sql-voter"
VOTER_CONFIG = {"model": SONNET, "temperature": 0.9}


def _headers() -> dict:
    pk, sk = os.getenv("LANGFUSE_PUBLIC_KEY", ""), os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

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


def _get(name: str, label=None, version=None):
    """The version a label (or a version number) serves, or None on 404. Other errors raise."""
    query = urllib.parse.urlencode({k: v for k, v in (("label", label), ("version", version)) if v is not None})
    url = f"{_host()}/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}?{query}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
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
    req = urllib.request.Request(f"{_host()}/api/public/v2/prompts",
                                 data=json.dumps(body).encode(), headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def matches(served, text: str, config: dict) -> bool:
    """True when the served version already is the checked-in definition: a text prompt
    with the same text and the same config, carrying the label."""
    return (served is not None
            and served.get("type", "text") == "text"
            and (served.get("prompt") or "").strip() == text.strip()
            and (served.get("config") or {}) == config
            and LABEL in (served.get("labels") or [LABEL]))


def reconcile_single(name, text, config, message) -> str:
    """Return "created", "updated" or "unchanged" for one single-version prompt."""
    existing = _get(name, LABEL)
    if matches(existing, text, config):
        return "unchanged"
    _create(name, text, [LABEL], config, message)
    return "updated" if existing is not None else "created"


def reconcile_voter() -> str:
    """The voter ships a v1 baseline plus a production version. The baseline is written
    only when the prompt does not exist at all, so a rerun never appends history."""
    existing = _get(VOTER, LABEL)
    if matches(existing, SQL_VOTER_V2, VOTER_CONFIG):
        return "unchanged"
    if existing is None and _get(VOTER, version=1) is None:
        _create(VOTER, SQL_VOTER_V1, [], VOTER_CONFIG, "Baseline SQL voter prompt")
    _create(VOTER, SQL_VOTER_V2, [LABEL], VOTER_CONFIG,
            "Qualify database.table + explicit GROUP BY + LIMIT; promote to production")
    return "updated" if existing is not None else "created"


def reconcile_all() -> dict:
    """Reconcile every prompt; one failure does not stop the others. Returns the counts."""
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    jobs = [(name, (lambda n=name, t=text, c=config, m=message: reconcile_single(n, t, c, m)))
            for name, text, config, message in SINGLE_VERSION]
    jobs.append((VOTER, reconcile_voter))
    for name, job in jobs:
        try:
            result = job()
        except (urllib.error.URLError, OSError, ValueError) as e:
            result = "failed"
            detail = e.read().decode("utf-8", "replace")[:200] if isinstance(e, urllib.error.HTTPError) and e.fp else ""
            print(f"  failed     {name}: {e} {detail}".rstrip())
        else:
            print(f"  {result:<10} {name} [{LABEL}]")
        counts[result] += 1
    return counts


def main():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first).")
    print(f"Seeding support-triage prompts at {_host()} ...")
    counts = reconcile_all()
    print("  " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    if counts["failed"]:
        raise SystemExit(f"{counts['failed']} prompt(s) failed to seed")
    print(f"\nDone. View: {_host()} → Prompts. The demo fetches these by label=production at runtime.")
    return counts


if __name__ == "__main__":
    main()
