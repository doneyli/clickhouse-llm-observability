#!/usr/bin/env python3
"""
Seed the 3 Langfuse-managed prompts for the Cluster Health Investigator.

    cluster-health-planner       v1 → label `fault-overplan` (no scaling rules)
                                 v2 → label `production` (scaling rules + dedup)
                                 v3 → label `candidate-scoped-decomposition`
    cluster-health-worker        v1 → label `production` (held fixed)
    cluster-health-synthesizer   v1 → label `production` (held fixed; requires
                                        per-claim [worker:<analysis_type>] citations)

The planner prompt is BOTH the experiment lever (production vs candidate) AND
the fault lever (fault-overplan) — one prompt, the whole story.

Idempotent: if `production` already resolves for a prompt, its versions are left
alone (re-running setup.sh won't bloat the version history). Local fallbacks in
graph.py mean the app still runs if this never ran.

Usage:
    LANGFUSE_HOST=http://localhost:3001 \
    LANGFUSE_PUBLIC_KEY=pk-... LANGFUSE_SECRET_KEY=sk-... \
    python scripts/seed_prompts.py
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph import (  # noqa: E402  (prompt templates — single source of truth)
    PLANNER_FALLBACK,
    PLANNER_FALLBACK_OVERPLAN,
    SYNTH_FALLBACK,
    WORKER_FALLBACK,
)

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
SK = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
_auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()

CONFIG = {"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "temperature": 0.2}

# v3 candidate: tighter scaling + explicit dedup — the "smarter, cheaper" planner.
PLANNER_CANDIDATE = PLANNER_FALLBACK.replace(
    "- A multi-symptom or broad instability report warrants up to 6 analyses.",
    "- A multi-symptom or broad instability report warrants 3-4 analyses; only a\n"
    "  genuinely cluster-wide outage justifies more.",
).replace(
    "Return a plan whose task count is proportionate to the symptom's breadth.",
    "Choose the SMALLEST set of analyses that fully covers the symptom. Each\n"
    "analysis_type must appear at most once. Return a plan proportionate to the\n"
    "symptom's breadth — prefer fewer, well-targeted analyses.",
)


def _post(body: dict) -> dict:
    req = urllib.request.Request(
        f"{HOST}/api/public/v2/prompts",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Basic {_auth}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _exists(name: str, label: str = "production") -> bool:
    import urllib.parse
    url = f"{HOST}/api/public/v2/prompts/{urllib.parse.quote(name)}?label={label}"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {_auth}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def _seed(name: str, versions: list[dict]):
    if _exists(name):
        print(f"  • {name}: already seeded (production label resolves) — skipping")
        return
    for v in versions:
        created = _post({"name": name, "type": "text", "prompt": v["prompt"],
                         "labels": v["labels"], "config": CONFIG,
                         "commitMessage": v["msg"]})
        print(f"  ✓ {name} v{created.get('version')} labels={created.get('labels')}")


def main() -> int:
    print(f"Seeding cluster-health prompts at {HOST} ...")
    try:
        _seed("cluster-health-planner", [
            {"prompt": PLANNER_FALLBACK_OVERPLAN, "labels": ["fault-overplan"],
             "msg": "v1: scaling rules removed (fault lever → max fan-out)"},
            {"prompt": PLANNER_FALLBACK, "labels": ["production"],
             "msg": "v2: scaling rules + dedup instruction; promote to production"},
            {"prompt": PLANNER_CANDIDATE, "labels": ["candidate-scoped-decomposition"],
             "msg": "v3: tightened scoping — the smarter/cheaper planner candidate"},
        ])
        _seed("cluster-health-worker", [
            {"prompt": WORKER_FALLBACK, "labels": ["production"],
             "msg": "v1: worker finding template (held fixed for variable isolation)"},
        ])
        _seed("cluster-health-synthesizer", [
            {"prompt": SYNTH_FALLBACK, "labels": ["production"],
             "msg": "v1: diagnosis synthesis with per-claim [worker:<type>] citations"},
        ])
    except Exception as e:
        print(f"  ! prompt seeding skipped: {e}")
        return 1
    print("Done. Planner fetches label=production at runtime; experiment/fault vary the label.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
