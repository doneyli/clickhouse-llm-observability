#!/usr/bin/env python3
"""
Per-step dataset for the text-to-sql chain (prompt-chaining pattern).

Creates `text-to-sql-analysis-step` — a dataset scoped to a SINGLE chain stage
(the analysis step), so you can test "did the analysis pick the right database"
in isolation from "did the response step ruin a good analysis". The existing
end-to-end evaluation (test scenarios) remains the E2E complement.

Two modes:
  * default            — reconcile the 8 curated items (question -> expected database(s)).
  * --link-latest N     — also add items for the last N `text-to-sql` runs whose
                          sourceTraceId + sourceObservationId point at the
                          ANALYSIS generation (not the trace root), so click-through
                          from a dataset item lands on the exact step it was minted
                          from (pattern guide §6-Datasets).

Idempotent: a curated item is matched on its question, a linked item on a stable id
(`step-link-<observation id>`). Only a missing, changed or archived item is written, and
it is written ACTIVE (see dataset_sync.py). Runs are found through Observations API v2,
or the v1 observations list on the pinned self-hosted server, which predates v2; never
the trace endpoints Langfuse Cloud removed.

Usage (from the repo root; seed_all.py runs the default mode):
    python demos/text-to-sql/scripts/seed_step_dataset.py
    python demos/text-to-sql/scripts/seed_step_dataset.py --dry-run
    python demos/text-to-sql/scripts/seed_step_dataset.py --link-latest 10

Environment: the root `.env` (shell-exported LANGFUSE_* are ignored; see
verify_demo.runtime_config). Standard library only.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make the demo package importable (parent of this scripts/ dir) for CATALOG_DATABASES,
# and this folder for the shared client and dataset rule.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gates import CATALOG_DATABASES  # noqa: E402
import dataset_sync  # noqa: E402
import verify_demo as v  # noqa: E402

DATASET_NAME = "text-to-sql-analysis-step"


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


# --------------- Definitions as dataset_sync specs ---------------

DESCRIPTION = ("Per-step regression set for the text-to-sql ANALYSIS stage "
               "(gate: gate-database-selection). Isolates 'did analysis pick "
               "the right database' from the response step.")
DATASET_METADATA = {"source": "seed_step_dataset.py", "chain_step": "analysis"}


def curated_specs() -> List[Dict[str, Any]]:
    """The curated items, identified by question (they predate stable ids)."""
    return [{"label": it.input["question"][:60], "input": it.input,
             "expectedOutput": it.expected_output, "metadata": it.metadata}
            for it in CURATED_ITEMS]


# --------------- --link-latest: the analysis step of recent runs ---------------

TRACE_NAME = "text-to-sql"
LINK_LOOKBACK_DAYS = 30


def _purpose(obs: Dict[str, Any]) -> Any:
    """`purpose` from observation metadata, nested or flattened (`langchain.purpose`)."""
    meta = obs.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return None
    if not isinstance(meta, dict):
        return None
    if "purpose" in meta:
        return meta["purpose"]
    return next((v for k, v in meta.items() if k.endswith(".purpose")), None)


def _question_from(obs: Dict[str, Any]) -> str:
    inp = obs.get("input")
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except ValueError:
            return ""
    q = inp.get("question") if isinstance(inp, dict) else None
    # A retry or a demo fault appends "\n\n[...]" to the question: keep the user's words.
    return q.split("\n\n[")[0].strip() if isinstance(q, str) else ""


def analysis_link(observations: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(observation id, question) for one trace: the first analysis GENERATION, and the
    question from the analysis step's input. None when the trace has no analysis step."""
    steps = sorted((o for o in observations if _purpose(o) == "query_analysis"),
                   key=lambda o: o.get("startTime") or "")
    gen = next((o for o in steps if o.get("type") == "GENERATION" and o.get("id")), None)
    question = next((q for q in map(_question_from, steps) if q), "")
    return (gen["id"], question) if gen and question else None


def _v1_pages(lf: v.Langfuse, **params: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in range(1, 21):
        d = lf.get("/api/public/observations", limit=100, page=page, **params) or {}
        rows += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return rows
    raise v.ApiError(None, "observations v1: more than 20 pages")


def latest_analysis_observations(lf: v.Langfuse, limit: int,
                                 now: Optional[datetime] = None) -> List[Tuple[str, str, str]]:
    """(trace id, analysis observation id, question) for the newest `limit` runs.

    Observations API v2; on a 404 (self-hosted server 3.221.1 predates it) the v1
    observations list. No trace endpoints: Langfuse Cloud removed them."""
    now = now or datetime.now(timezone.utc)
    window = {"fromStartTime": v.iso(now - timedelta(days=LINK_LOOKBACK_DAYS)),
              "toStartTime": v.iso(now + timedelta(minutes=5))}
    try:
        roots = lf.cursor("/api/public/v2/observations", dict(name=TRACE_NAME, isRootObservation=True,
                                                              fields="core,basic", **window), cap=limit)
        api = "v2"
    except v.ApiError as e:
        if e.status != 404:
            raise
        roots = [r for r in _v1_pages(lf, name=TRACE_NAME, **window) if not r.get("parentObservationId")]
        roots.sort(key=lambda r: r.get("startTime") or "", reverse=True)
        roots, api = roots[:limit], "v1"
    out = []
    for root in roots:
        tid = root["traceId"]
        if api == "v2":
            started = v.parse_time(root["startTime"])
            obs = lf.cursor("/api/public/v2/observations", {
                "traceId": tid, "fields": "core,basic,io,metadata",
                "fromStartTime": v.iso(started - timedelta(hours=1)), "toStartTime": window["toStartTime"]})
        else:
            obs = _v1_pages(lf, traceId=tid)
        link = analysis_link(obs)
        if link:
            out.append((tid, link[0], link[1]))
    return out


def linked_specs(links: List[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    """One item per linked run, with a stable id so re-linking the same run upserts."""
    return [{"label": f"{q[:40]} (trace {tid[:8]})", "id": f"step-link-{oid}",
             "input": {"question": q},
             "expectedOutput": {"databases": _infer_expected(q),
                                "criteria": "analysis names at least one relevant catalog database"},
             "metadata": {"chain_step": "analysis", "gate": "gate-database-selection",
                          "linked_from": "trace"},
             "sourceTraceId": tid, "sourceObservationId": oid}
            for tid, oid, q in links]


def _infer_expected(question: str) -> List[str]:
    """Best-effort: which catalog databases the question plausibly maps to
    (substring match on the question) — a starting point for the expected set."""
    q = (question or "").lower()
    return sorted(db for db in CATALOG_DATABASES if db in q)


def seed(lf: v.Langfuse, dry_run: bool, link_latest: int = 0,
         report: Callable[[str, str], None] = print) -> int:
    """Reconcile the dataset and its curated items (plus linked ones); return the failure count."""
    failures = dataset_sync.sync(lf, DATASET_NAME, DESCRIPTION, DATASET_METADATA, curated_specs(),
                                 dry_run, report)
    if link_latest:
        try:
            links = latest_analysis_observations(lf, link_latest)
        except (v.ApiError, KeyError, ValueError) as e:
            report("failed", f"--link-latest: reading recent runs: {e}")
            return failures + 1
        if not links:
            report("unchanged", f"--link-latest: no '{TRACE_NAME}' run with an analysis step found")
        for status, label, err in dataset_sync.reconcile_items(lf, DATASET_NAME, linked_specs(links), dry_run):
            report(status, f"linked item {label}" + (f": {err}" if err else ""))
            failures += status == "failed"
    return failures


# --------------- Main ---------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Seed the per-step analysis dataset for text-to-sql")
    ap.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
    ap.add_argument("--link-latest", type=int, default=0, metavar="N",
                    help="Also link items to the analysis step of the last N text-to-sql runs")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = v.runtime_config()
    if cfg.error:
        print(cfg.error, file=sys.stderr)
        return 1
    lf = v.Langfuse(cfg.base, cfg.public_key, cfg.secret_key)
    print(f"Per-step dataset '{DATASET_NAME}' at {cfg.base}" + (" (dry run)" if args.dry_run else ""))

    def report(status: str, what: str) -> None:
        print(f"  {status:<10} {what}")

    failures = seed(lf, args.dry_run, args.link_latest, report)
    print(f"\n{'FAILED' if failures else 'Done'}: {failures} failure(s). View: {cfg.base} → Datasets → {DATASET_NAME}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
