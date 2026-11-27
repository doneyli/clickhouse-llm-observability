#!/usr/bin/env python
"""Compute each question's ground truth from its reference SQL and upsert the Langfuse dataset.

    .venv/bin/python scripts/seed_dataset.py --dry-run     # ground truth + margins, no Langfuse
    .venv/bin/python scripts/seed_dataset.py               # also upsert + read back the dataset
    EXPLORER_TARGET=playground .venv/bin/python scripts/seed_dataset.py --dry-run

The expected output is exactly what the reference query returns (one row, one column per
answer key), so the experiment needs no LLM judge. The script also runs each reference
query's ranking (the same SQL with the final ``LIMIT 1`` widened to the top 3) and reports
how clearly the winner beats the runner-up. A near-tie fails the run: the question would
grade noise, not search breadth.

Idempotent: items are upserted by id (the question id); dataset items whose id is no
longer in ``data/questions.yaml`` are archived. Success is decided by reading the dataset
back, never by exit codes alone.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explorer.ch_client import ClickHouseClient  # noqa: E402
from explorer.config import ClickHouseSettings, load_env  # noqa: E402
from explorer.evaluators import (  # noqa: E402
    QUESTIONS_PATH,
    load_question_specs,
    normalize,
    ranking_sql,
    relative_margin,
    split_reference_sql,
)

DATASET_NAME = "scenario-explorer-questions"
DATASET_DESCRIPTION = (
    "What-if questions over US domestic flights (`ontime`). Each expected output is computed "
    "exactly by the question's reference SQL (see item metadata), so scoring is deterministic."
)
MIN_MARGIN = 0.02
# Reference queries scan up to ~40M rows with a 61-121 step ARRAY JOIN; give them room.
REFERENCE_QUERY_SETTINGS = {"max_execution_time": "120", "max_result_rows": "100",
                            "result_overflow_mode": "throw", "use_query_cache": "0"}


def _round(v: Any) -> Any:
    return round(v, 6) if isinstance(v, float) else v


async def compute_ground_truth(specs: list[dict], settings: ClickHouseSettings, *,
                               margins: bool) -> list[dict[str, Any]]:
    out = []
    async with ClickHouseClient(settings, timeout_s=180, query_settings=REFERENCE_QUERY_SETTINGS) as ch:
        for s in specs:
            sql = s["reference_sql"].format(table=settings.qualified_table)
            res = await ch.query(sql)  # check=True: the reference SQL passes the agent's allow-list too
            if len(res.rows) != 1:
                raise SystemExit(f"{s['id']}: reference_sql returned {len(res.rows)} rows, expected exactly 1")
            if res.columns != list(s["answer_keys"]):
                raise SystemExit(f"{s['id']}: reference_sql columns {res.columns} != answer_keys {s['answer_keys']}")
            row = {k: _round(v) for k, v in res.first().items()}
            entry: dict[str, Any] = {"id": s["id"], "expected": row, "server_ms": res.stats.server_ms,
                                     "read_rows": res.stats.read_rows}
            if margins:
                top = (await ch.query(ranking_sql(sql, top=3))).records()
                winner = {k: top[0][k] for k in s["answer_keys"]}
                if {k: normalize(v) for k, v in winner.items()} != {k: normalize(v) for k, v in row.items()}:
                    raise SystemExit(f"{s['id']}: ranking winner {winner} != reference answer {row}")
                entry["support"] = top[0].get("support")
                entry["top"] = [{k: _round(v) for k, v in r.items()} for r in top]
                entry["margin"] = (relative_margin(float(top[0]["metric"]), float(top[1]["metric"]))
                                   if len(top) > 1 else None)
            out.append(entry)
    return out


def print_table(specs: list[dict], truth: list[dict]) -> None:
    by_id = {s["id"]: s for s in specs}
    print(f"\n{'id':<16} {'diff':<7} {'margin':>7} {'support':>8} {'ms':>6}  expected (runner-up)")
    for t in truth:
        s = by_id[t["id"]]
        margin = f"{t['margin']:.1%}" if t.get("margin") is not None else "-"
        runner = ""
        if len(t.get("top", [])) > 1:
            r = t["top"][1]
            runner = "  (" + ", ".join(f"{k}={r[k]}" for k in s["answer_keys"]) + f"; metric={r['metric']})"
        print(f"{t['id']:<16} {s.get('difficulty', ''):<7} {margin:>7} {str(t.get('support', '-')):>8} "
              f"{t['server_ms']:>6.0f}  {t['expected']}{runner}")


def upsert_dataset(lf: Any, specs: list[dict], truth: list[dict], settings: ClickHouseSettings) -> None:
    from langfuse.api import NotFoundError
    from langfuse.api.commons.types.dataset_status import DatasetStatus

    try:
        lf.api.datasets.get(dataset_name=DATASET_NAME)
    except NotFoundError:
        lf.create_dataset(name=DATASET_NAME, description=DATASET_DESCRIPTION,
                          metadata={"demo": "analytics-agentic-speed", "questions_file": "data/questions.yaml"})
        print(f"created dataset {DATASET_NAME}")

    by_id = {t["id"]: t for t in truth}
    for s in specs:
        t = by_id[s["id"]]
        lf.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=s["id"],
            input={"question_id": s["id"], "question": " ".join(str(s["text"]).split()),
                   "answer_keys": list(s["answer_keys"])},
            expected_output=t["expected"],
            metadata={
                "difficulty": s.get("difficulty"),
                "notes": " ".join(str(s.get("notes", "")).split()),
                "margin": round(t["margin"], 4) if t.get("margin") is not None else None,
                "runner_up": t["top"][1] if len(t.get("top", [])) > 1 else None,
                "reference_sql": s["reference_sql"].strip(),
                "computed_on": f"{settings.host}/{settings.qualified_table}",
            },
            status=DatasetStatus.ACTIVE,
        )
        print(f"upserted {s['id']}")

    wanted = {s["id"] for s in specs}
    for item in lf.get_dataset(DATASET_NAME).items:
        if item.id not in wanted and str(item.status) != "ARCHIVED":
            lf.create_dataset_item(dataset_name=DATASET_NAME, id=item.id, input=item.input,
                                   expected_output=item.expected_output, metadata=item.metadata,
                                   status=DatasetStatus.ARCHIVED)
            print(f"archived stale item {item.id}")
    lf.flush()


def read_back(lf: Any, specs: list[dict], truth: list[dict]) -> bool:
    """Assert on what Langfuse holds: every question present, active, with our expected output."""
    ds = lf.get_dataset(DATASET_NAME)
    active = {i.id: i for i in ds.items if str(i.status) != "ARCHIVED"}
    wanted = {s["id"] for s in specs}
    ok = True
    if set(active) != wanted:
        print(f"READ-BACK FAIL: active items {sorted(active)} != questions {sorted(wanted)}")
        ok = False
    for t in truth:
        item = active.get(t["id"])
        if item is not None and item.expected_output != t["expected"]:
            print(f"READ-BACK FAIL: {t['id']} expected_output {item.expected_output} != {t['expected']}")
            ok = False
    print(f"read back dataset {DATASET_NAME}: {len(active)} active items "
          f"({len(ds.items)} total), expected {len(wanted)} -> {'OK' if ok else 'MISMATCH'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print ground truth only; do not touch Langfuse")
    ap.add_argument("--only", nargs="*", help="question ids to compute (dry runs only)")
    ap.add_argument("--no-margin", action="store_true", help="skip the runner-up/margin check")
    ap.add_argument("--min-margin", type=float, default=MIN_MARGIN,
                    help=f"fail if the winner beats the runner-up by less than this fraction (default {MIN_MARGIN})")
    ap.add_argument("--questions", default=str(QUESTIONS_PATH))
    args = ap.parse_args()

    load_env()
    settings = ClickHouseSettings.from_env()
    specs = load_question_specs(args.questions)
    for s in specs:
        split_reference_sql(s["reference_sql"])  # fail fast on the convention
    if args.only:
        if not args.dry_run:
            raise SystemExit("--only is for dry runs: a real seed must write every question")
        specs = [s for s in specs if s["id"] in set(args.only)]
    print(f"computing ground truth for {len(specs)} questions on {settings.host} / {settings.qualified_table}")

    truth = asyncio.run(compute_ground_truth(specs, settings, margins=not args.no_margin))
    print_table(specs, truth)

    near = [t["id"] for t in truth if t.get("margin") is not None and t["margin"] < args.min_margin]
    if near:
        print(f"\nNEAR-TIE (< {args.min_margin:.0%} margin): {near}")
        return 1
    if args.dry_run:
        print("\n--dry-run: Langfuse not touched")
        return 0

    from explorer.tracing import get_langfuse

    lf = get_langfuse()
    if not lf.auth_check():
        print("Langfuse auth_check failed (check LANGFUSE_* in .env)")
        return 1
    upsert_dataset(lf, specs, truth, settings)
    return 0 if read_back(lf, specs, truth) else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
