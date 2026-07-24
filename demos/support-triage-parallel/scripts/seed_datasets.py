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

Idempotent: datasets are created if missing; items are upserted by a stable id.

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


def _client():
    from langfuse import get_client
    return get_client()


def _ensure_dataset(lf, name, description):
    try:
        lf.create_dataset(name=name, description=description)
        print(f"  + dataset '{name}' created")
    except Exception:
        print(f"  ✓ dataset '{name}' exists")


def _upsert_item(lf, dataset_name, item_id, input, expected_output):
    lf.create_dataset_item(dataset_name=dataset_name, id=item_id,
                           input=input, expected_output=expected_output)


def seed_category(lf):
    _ensure_dataset(lf, CATEGORY_DATASET, "Per-branch: ticket body -> expected category label")
    for i, (body, category) in enumerate(CATEGORY_ITEMS):
        _upsert_item(lf, CATEGORY_DATASET, f"cat-{i:02d}",
                     {"ticket_body": body}, {"category": category})
    print(f"  ✓ {len(CATEGORY_ITEMS)} category-branch items seeded")


def seed_sql_voting(lf):
    from sql_voting import _result_signature
    _ensure_dataset(lf, SQL_VOTING_DATASET,
                    "E2E voter: NL question -> pinned result signature + canonical SQL")
    pinned = 0
    for i, (question, ref_sql) in enumerate(SQL_VOTING_ITEMS):
        sig = None
        try:
            sig = _result_signature(ref_sql)   # execute once against the playground + hash
        except Exception as e:
            print(f"    ! could not pin signature for item {i}: {e}")
        if sig:
            pinned += 1
        _upsert_item(lf, SQL_VOTING_DATASET, f"vote-{i:02d}",
                     {"question": question},
                     {"result_signature": sig, "canonical_sql": ref_sql})
    print(f"  ✓ {len(SQL_VOTING_ITEMS)} sql-voting items seeded ({pinned} with pinned signatures)")


def main():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first).")
    lf = _client()
    print("Seeding support-triage datasets ...")
    seed_category(lf)
    seed_sql_voting(lf)
    lf.flush()
    print(f"\nLocal aggregator cases (run via scripts/run_experiment.py --local-aggregator): "
          f"{len(LOCAL_AGGREGATOR_CASES)} cases")
    for c in LOCAL_AGGREGATOR_CASES:
        print(f"  - expects {c['expected_output']}")
    print("\nDone. View: Langfuse → Datasets.")


if __name__ == "__main__":
    main()
