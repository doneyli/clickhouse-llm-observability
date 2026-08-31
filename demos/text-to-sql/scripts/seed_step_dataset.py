#!/usr/bin/env python3
"""
Per-step dataset for the text-to-sql chain (prompt-chaining pattern).

Creates `text-to-sql-analysis-step` — a dataset scoped to a SINGLE chain stage
(the analysis step), so you can test "did the analysis pick the right database"
in isolation from "did the response step ruin a good analysis". The existing
end-to-end evaluation (test scenarios) remains the E2E complement.

Two modes:
  * default            — seed ~8 curated items (question -> expected database(s)).
  * --link-latest N     — mine the last N `text-to-sql` traces and add items whose
                          source_trace_id + source_observation_id point at the
                          ANALYSIS generation (not the trace root), so click-through
                          from a dataset item lands on the exact step it was minted
                          from (pattern guide §6-Datasets).

Usage (from repo root, after sourcing .env):
    python demos/text-to-sql/scripts/seed_step_dataset.py
    python demos/text-to-sql/scripts/seed_step_dataset.py --dry-run
    python demos/text-to-sql/scripts/seed_step_dataset.py --link-latest 10

Environment:
    LANGFUSE_HOST / LANGFUSE_BASE_URL  (default: http://localhost:3001)
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

# Make the demo package importable (parent of this scripts/ dir) for CATALOG_DATABASES.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gates import CATALOG_DATABASES  # noqa: E402

DATASET_NAME = "text-to-sql-analysis-step"

HOST = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001")).rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
SK = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")

_auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()
_HEADERS = {"Authorization": f"Basic {_auth}", "Content-Type": "application/json"}


@dataclass
class DatasetItem:
    input: Dict[str, Any]
    expected_output: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


def _step_item(question: str, databases: List[str]) -> DatasetItem:
    return DatasetItem(
        input={"question": question},
        expected_output={
            "databases": databases,
            "criteria": "analysis names at least one of these catalog databases",
        },
        metadata={"chain_step": "analysis", "gate": "gate-database-selection"},
    )


# Curated items: question -> the catalog database(s) a good analysis should name.
CURATED_ITEMS: List[DatasetItem] = [
    _step_item("What are the most expensive areas for property in London?", ["uk"]),
    _step_item("What is the average taxi trip distance in New York City?", ["nyc_taxi"]),
    _step_item("Which programming languages have the most Stack Overflow questions?", ["stackoverflow"]),
    _step_item("How has GitHub activity changed over the past year?", ["github"]),
    _step_item("What are the highest-scored stories on Hacker News this year?", ["hackernews"]),
    _step_item("Which US airlines have the highest rate of flight delays?", ["ontime"]),
    _step_item("What are the most downloaded Python packages?", ["pypi"]),
    _step_item("Which YouTube videos have the most views?", ["youtube"]),
]


# --------------- REST helpers (mining traces for --link-latest) ---------------

def _get(path: str):
    url = f"{HOST}{path}"
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _latest_analysis_observations(limit: int):
    """Yield (trace_id, observation_id, question) for the analysis generation of
    the most recent `text-to-sql` traces."""
    data = _get(f"/api/public/traces?name=text-to-sql&limit={int(limit)}")
    for trace in data.get("data", []):
        tid = trace.get("id")
        if not tid:
            continue
        full = _get(f"/api/public/traces/{urllib.parse.quote(tid)}")
        obs = full.get("observations", [])
        analysis = next(
            (o for o in obs
             if (o.get("metadata") or {}).get("purpose") == "query_analysis"),
            None,
        )
        if analysis is None:
            continue
        question = ""
        tin = full.get("input")
        if isinstance(tin, dict):
            question = tin.get("question", "") or ""
        yield tid, analysis.get("id"), question


def _infer_expected(question: str) -> List[str]:
    """Best-effort: which catalog databases the question plausibly maps to
    (substring match on the question) — a starting point for the expected set."""
    q = (question or "").lower()
    return sorted(db for db in CATALOG_DATABASES if db in q)


# --------------- Main ---------------

def parse_args():
    ap = argparse.ArgumentParser(description="Seed the per-step analysis dataset for text-to-sql")
    ap.add_argument("--dry-run", action="store_true", help="Preview without creating")
    ap.add_argument("--link-latest", type=int, default=0, metavar="N",
                    help="Also mine the last N text-to-sql traces and link items to the analysis step")
    return ap.parse_args()


def main():
    args = parse_args()

    print(f"Per-step dataset '{DATASET_NAME}' at {HOST}", file=sys.stderr)
    print(f"  curated items: {len(CURATED_ITEMS)}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN — no data will be created **", file=sys.stderr)
        for it in CURATED_ITEMS:
            print(f"    - {it.input['question'][:70]}  -> {it.expected_output['databases']}", file=sys.stderr)
        if args.link_latest:
            print(f"  would also link the analysis observation of the last "
                  f"{args.link_latest} text-to-sql traces", file=sys.stderr)
        return

    try:
        from langfuse import Langfuse
    except ImportError:
        print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'", file=sys.stderr)
        sys.exit(1)

    client = Langfuse(public_key=PK, secret_key=SK, host=HOST)

    try:
        client.create_dataset(
            name=DATASET_NAME,
            description="Per-step regression set for the text-to-sql ANALYSIS stage "
                        "(gate: gate-database-selection). Isolates 'did analysis pick "
                        "the right database' from the response step.",
            metadata={"source": "seed_step_dataset.py", "chain_step": "analysis"},
        )
        print(f"  created dataset: {DATASET_NAME}", file=sys.stderr)
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"  dataset exists: {DATASET_NAME} (adding items)", file=sys.stderr)
        else:
            print(f"  warning creating dataset: {e}", file=sys.stderr)

    created = 0
    for it in CURATED_ITEMS:
        try:
            client.create_dataset_item(
                dataset_name=DATASET_NAME,
                input=it.input,
                expected_output=it.expected_output,
                metadata=it.metadata,
            )
            created += 1
        except Exception as e:
            print(f"    error adding item: {e}", file=sys.stderr)
    print(f"  added {created} curated item(s)", file=sys.stderr)

    if args.link_latest:
        linked = 0
        try:
            for tid, oid, question in _latest_analysis_observations(args.link_latest):
                client.create_dataset_item(
                    dataset_name=DATASET_NAME,
                    input={"question": question},
                    expected_output={
                        "databases": _infer_expected(question),
                        "criteria": "analysis names at least one relevant catalog database",
                    },
                    metadata={"chain_step": "analysis", "gate": "gate-database-selection",
                              "linked_from": "trace"},
                    source_trace_id=tid,
                    source_observation_id=oid,  # the analysis generation, not the trace root
                )
                linked += 1
        except Exception as e:
            print(f"  warning mining traces: {e}", file=sys.stderr)
        print(f"  linked {linked} item(s) to the analysis observation of recent traces", file=sys.stderr)

    client.flush()
    print(f"\nDone. View: {HOST} → Datasets → {DATASET_NAME}", file=sys.stderr)


if __name__ == "__main__":
    main()
