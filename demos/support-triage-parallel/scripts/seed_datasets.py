#!/usr/bin/env python3
"""
Seed Langfuse datasets for the Support Triage Parallel demo.

Per the parallelization field-guide's two dataset types:

1. ``support-triage/category-branch`` (Langfuse-hosted, per-branch): 12 items,
   ``input={ticket_body}``, ``expected_output={category}``. A sectioning branch is
   a natural single-observation item; after a seeded run, link each item back with
   ``source_observation_id`` of its ``branch-category`` span (from the UI).

2. ``support-triage/sql-voting`` (Langfuse-hosted, E2E for the voter): 10 items,
   ``input={question}``, ``expected_output={result_signature, canonical_sql}``.
   Signatures are precomputed ONCE against the live playground and pinned, so the
   experiment can score a majority answer against a stable expected signature.
   (Requires network + clickhouse-connect at seed time; items with an
   unreachable/failed reference query are seeded with ``result_signature=None``.)

3. ``LOCAL_AGGREGATOR_CASES`` (NOT Langfuse-hosted — pure merge/vote code): run via
   ``langfuse.run_experiment(...)`` so only traces are created. Doubles as the CI
   target for the *aggregation-logic-bug* failure mode. Printed at the end.

Idempotent and drift-reconciling: a dataset is created if missing (its description
updated if it drifted), and an item is written only when it is missing, ARCHIVED, or
its input / expected output differ from what is checked in here, always as ACTIVE
under a stable id. An unchanged project gets no writes, so no new dataset versions.
A playground outage never erases a signature pinned earlier. Each dataset prints
created / updated / unchanged counts; failed writes and unpinned signatures are
reported, the rest still run, and ``main()`` exits non-zero if any occurred.

Usage (from repo root, after sourcing .env):
    python demos/support-triage-parallel/scripts/seed_datasets.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CATEGORY_DATASET = "support-triage/category-branch"
SQL_VOTING_DATASET = "support-triage/sql-voting"

# 12 (ticket_body, category) items for the category branch.
CATEGORY_ITEMS = [
    ("Our nyc_taxi dashboard's borough panel takes 20+ seconds to load since we added fare columns.", "query-performance"),
    ("Ingestion job for the events table is backing up and rows are arriving hours late.", "ingestion"),
    ("Our analytics replica is falling behind the primary and dashboards are stale.", "replication"),
    ("We got a bill that's roughly double last month with no change in volume.", "billing"),
    ("We need to add three columns to a 2B-row table without downtime — how?", "schema-migration"),
    ("clickhouse-client times out connecting from our VPC, but works locally.", "connectivity"),
    ("A GROUP BY query that used to take 1s now takes 40s after a version bump.", "query-performance"),
    ("Kafka engine table stopped consuming after a broker restart.", "ingestion"),
    ("ReplicatedMergeTree parts are diverging between two replicas.", "replication"),
    ("Can you explain the line items on our ClickHouse Cloud invoice?", "billing"),
    ("We want to change a column type from String to LowCardinality(String) safely.", "schema-migration"),
    ("Just wanted to say the docs are great — no issue, thanks!", "other"),
]

# 10 (question, reference_sql) items for the SQL voter. The reference SQL is
# executed once against the playground to pin the expected result signature.
SQL_VOTING_ITEMS = [
    ("Which pickup borough had the highest average fare_amount in nyc_taxi for 2015?",
     "SELECT pickup_ntaname AS borough, avg(fare_amount) AS avg_fare FROM nyc_taxi.trips "
     "WHERE toYear(pickup_datetime) = 2015 GROUP BY borough ORDER BY avg_fare DESC LIMIT 5"),
    ("How many nyc_taxi trips were recorded in 2015 and 2016?",
     "SELECT toYear(pickup_datetime) AS year, count() AS trips FROM nyc_taxi.trips "
     "WHERE year IN (2015, 2016) GROUP BY year ORDER BY year LIMIT 10"),
    ("How many Hacker News stories were posted in 2015?",
     "SELECT count() AS stories FROM hackernews.hits WHERE type = 'story' AND toYear(time) = 2015 LIMIT 1"),
    ("Top 10 Hacker News stories by score in 2015?",
     "SELECT title, score FROM hackernews.hits WHERE type = 'story' AND toYear(time) = 2015 "
     "ORDER BY score DESC LIMIT 10"),
    ("What was the average uk_price_paid price in Greater London in 2022?",
     "SELECT avg(price) AS avg_price FROM uk.uk_price_paid WHERE toYear(date) = 2022 "
     "AND county = 'GREATER LONDON' LIMIT 1"),
    ("Top 10 stackoverflow tags by number of questions?",
     "SELECT arrayJoin(splitByChar('|', tags)) AS tag, count() AS questions FROM stackoverflow.posts "
     "WHERE post_type_id = 1 GROUP BY tag ORDER BY questions DESC LIMIT 10"),
    ("How many distinct github repos had at least one event in the last full year?",
     "SELECT uniqExact(repo_name) AS repos FROM github.github_events "
     "WHERE toYear(created_at) = toYear(now()) - 1 LIMIT 1"),
    ("Top 10 github repos by number of stars (watch events) in the last full year?",
     "SELECT repo_name, count() AS stars FROM github.github_events "
     "WHERE event_type = 'WatchEvent' AND toYear(created_at) = toYear(now()) - 1 "
     "GROUP BY repo_name ORDER BY stars DESC LIMIT 10"),
    ("Average nyc_taxi trip_distance for airport pickups in 2015?",
     "SELECT avg(trip_distance) AS avg_distance FROM nyc_taxi.trips "
     "WHERE toYear(pickup_datetime) = 2015 AND pickup_ntaname LIKE '%Airport%' LIMIT 1"),
    ("How many stackoverflow questions were asked per year for the last five years?",
     "SELECT toYear(creation_date) AS year, count() AS questions FROM stackoverflow.posts "
     "WHERE post_type_id = 1 AND year >= toYear(now()) - 5 GROUP BY year ORDER BY year LIMIT 10"),
]

# Pure merge/vote logic cases — run locally (traces only, no hosted dataset).
LOCAL_AGGREGATOR_CASES = [
    {"input": {"candidates": [{"valid": True, "signature": "sig-a"}] * 3
               + [{"valid": True, "signature": "sig-b"}, {"valid": False, "signature": None}]},
     "expected_output": {"winner": "sig-a", "tie": False, "margin": 2}},
    {"input": {"candidates": [{"valid": True, "signature": "sig-a"}] * 2
               + [{"valid": True, "signature": "sig-b"}] * 2
               + [{"valid": True, "signature": "sig-c"}]},
     "expected_output": {"winner": None, "tie": True, "margin": 0}},
    {"input": {"candidates": [{"valid": False, "signature": None}] * 5},
     "expected_output": {"winner": None, "tie": False, "empty": True}},
    {"input": {"branch_results": [
        {"branch": "branch-summary", "key": "summary", "ok": True, "output": "s"},
        {"branch": "branch-sentiment-urgency", "key": "sentiment", "ok": False, "output": None},
        {"branch": "branch-category", "key": "category", "ok": True, "output": "billing"},
        {"branch": "branch-policy-guard", "key": "policy", "ok": True, "output": "{}"}]},
     "expected_output": {"degraded": True, "failed_branches": 1,
                         "sentiment": "insufficient data"}},
]


DESCRIPTIONS = {
    CATEGORY_DATASET: "Per-branch: ticket body -> expected category label",
    SQL_VOTING_DATASET: "E2E voter: NL question -> pinned result signature + canonical SQL",
}


def category_items():
    """The desired (id, input, expected_output) rows of the category-branch dataset."""
    return [(f"cat-{i:02d}", {"ticket_body": body}, {"category": category})
            for i, (body, category) in enumerate(CATEGORY_ITEMS)]


def sql_voting_items():
    """The desired (id, input, canonical_sql) rows of the sql-voting dataset; the pinned
    signature is computed at seed time."""
    return [(f"vote-{i:02d}", {"question": q}, ref_sql) for i, (q, ref_sql) in enumerate(SQL_VOTING_ITEMS)]


def _client():
    from langfuse import get_client
    return get_client()


def _status(item) -> str:
    status = getattr(item, "status", None)
    return str(getattr(status, "value", status) or "ACTIVE").upper()


def _is_not_found(exc) -> bool:
    return getattr(exc, "status_code", None) == 404 or type(exc).__name__ == "NotFoundError"


def reconcile_dataset(lf, name) -> str:
    """Return "created", "updated" or "unchanged" for the dataset itself."""
    try:
        current = lf.api.datasets.get(name)
    except Exception as e:  # noqa: BLE001 - only a 404 means "missing"
        if not _is_not_found(e):
            raise
        current = None
    if current is not None and (current.description or "") == DESCRIPTIONS[name]:
        return "unchanged"
    lf.create_dataset(name=name, description=DESCRIPTIONS[name])  # upserts by name
    return "created" if current is None else "updated"


def existing_items(lf, name) -> dict:
    """Every item of the dataset (ACTIVE and ARCHIVED), by id. Follows the page count."""
    out, page = {}, 1
    while True:
        res = lf.api.dataset_items.list(dataset_name=name, page=page, limit=100)
        for item in res.data or []:
            out[item.id] = item
        if page >= (getattr(res.meta, "total_pages", 1) or 1) or page >= 50:
            return out
        page += 1


def item_matches(current, input, expected_output) -> bool:
    return (current is not None and _status(current) == "ACTIVE"
            and current.input == input and current.expected_output == expected_output)


def reconcile_items(lf, name, desired, counts) -> None:
    """Write each (id, input, expected_output) that is missing, archived or changed."""
    from langfuse.api import DatasetStatus
    current = existing_items(lf, name)
    for item_id, input, expected_output in desired:
        have = current.get(item_id)
        if item_matches(have, input, expected_output):
            counts["unchanged"] += 1
            continue
        try:
            lf.create_dataset_item(dataset_name=name, id=item_id, input=input,
                                   expected_output=expected_output, status=DatasetStatus.ACTIVE)
        except Exception as e:  # noqa: BLE001 - report, keep going, fail at the end
            counts["failed"] += 1
            print(f"    failed  {name} {item_id}: {type(e).__name__}: {str(e)[:160]}")
        else:
            counts["created" if have is None else "updated"] += 1


def pinned_signature(ref_sql, previous):
    """Execute the reference query once and hash its rows. On failure keep the signature
    pinned earlier for the same query, so an outage never erases it."""
    from sql_voting import _result_signature
    try:
        sig = _result_signature(ref_sql)
    except Exception as e:  # noqa: BLE001 - network / driver errors
        print(f"    ! could not execute reference SQL: {type(e).__name__}: {e}")
        sig = None
    if sig is None and previous and previous.get("canonical_sql") == ref_sql:
        return previous.get("result_signature")
    return sig


def seed_category(lf) -> dict:
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0, "unpinned": 0}
    print(f"  dataset {CATEGORY_DATASET}: {reconcile_dataset(lf, CATEGORY_DATASET)}")
    reconcile_items(lf, CATEGORY_DATASET, category_items(), counts)
    return counts


def seed_sql_voting(lf) -> dict:
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0, "unpinned": 0}
    print(f"  dataset {SQL_VOTING_DATASET}: {reconcile_dataset(lf, SQL_VOTING_DATASET)}")
    current = existing_items(lf, SQL_VOTING_DATASET)
    desired = []
    for item_id, input, ref_sql in sql_voting_items():
        have = current.get(item_id)
        sig = pinned_signature(ref_sql, have.expected_output if have is not None else None)
        if not sig:
            counts["unpinned"] += 1
            print(f"    ! {item_id}: no pinned result signature (is the playground reachable?)")
        desired.append((item_id, input, {"result_signature": sig, "canonical_sql": ref_sql}))
    reconcile_items(lf, SQL_VOTING_DATASET, desired, counts)
    return counts


def main():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first).")
    lf = _client()
    print("Seeding support-triage datasets ...")
    problems = 0
    for name, seed in ((CATEGORY_DATASET, seed_category), (SQL_VOTING_DATASET, seed_sql_voting)):
        try:
            counts = seed(lf)
        except Exception as e:  # noqa: BLE001 - a failed read or dataset write
            problems += 1
            print(f"  failed  dataset {name}: {type(e).__name__}: {str(e)[:200]}")
            continue
        print(f"  items {name}: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
        problems += counts["failed"] + counts["unpinned"]
    lf.flush()
    print(f"\nLocal aggregator cases (run via scripts/run_experiment.py --local-aggregator): "
          f"{len(LOCAL_AGGREGATOR_CASES)} cases")
    for c in LOCAL_AGGREGATOR_CASES:
        print(f"  - expects {c['expected_output']}")
    if problems:
        raise SystemExit(f"{problems} dataset problem(s): see the lines above")
    print("\nDone. View: Langfuse → Datasets.")


if __name__ == "__main__":
    main()
