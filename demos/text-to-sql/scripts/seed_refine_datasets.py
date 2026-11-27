#!/usr/bin/env python3
"""Seed the two evaluation datasets for the text-to-sql refine loop (Pattern #5).

1) text-to-sql/critic-accuracy — tests the CRITIC IN ISOLATION (candidate SQL +
   evidence -> known-correct verdict). This is the only way to detect
   critic/generator collusion: final-output tests can't, because a colluding
   critic makes the final output *look* accepted. Half the items must-revise
   (unknown column, missing LIMIT, cross-join full scan, non-SELECT, empty result
   for an answerable question), half must-accept.

2) text-to-sql/converged-sql — golden endpoints (task + critique history -> the
   fully-refined SQL). Captured via the Corrections feature on the final
   generate-sql observation, so a new config can be tested for reaching that
   endpoint in fewer iterations.

Idempotent: items have stable ids (`crit-NN`, `conv-NN`). Only a missing, changed or
archived item is written, and it is written ACTIVE (see dataset_sync.py), so an
unchanged run writes nothing.

Run (from the repo root; seed_all.py runs the default mode):
    python demos/text-to-sql/scripts/seed_refine_datasets.py
    python demos/text-to-sql/scripts/seed_refine_datasets.py --dry-run
    # optionally derive one converged-sql item from a REAL Correction (needs the
    # langfuse package, for the CORRECTION score):
    python demos/text-to-sql/scripts/seed_refine_datasets.py \
        --from-trace <trace_id> --from-observation <final generate-sql obs id> \
        --correction "SELECT town, round(avg(price)) ... LIMIT 10"

Environment: the root `.env` (shell-exported LANGFUSE_* are ignored; see
verify_demo.runtime_config). Standard library only, except --from-trace.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dataset_sync  # noqa: E402
import verify_demo as v  # noqa: E402

CRITIC_DATASET = "text-to-sql/critic-accuracy"
CONVERGED_DATASET = "text-to-sql/converged-sql"
DATASET_METADATA = {"source": "demos/text-to-sql", "pattern": "evaluator-optimizer"}
CRITIC_DESCRIPTION = "Tests the SQL critic in isolation (collusion detection): candidate + evidence -> verdict."
CONVERGED_DESCRIPTION = "Golden refine endpoints: task + critique history -> fully-refined SQL (Corrections)."


# --- critic-accuracy items (candidate SQL + evidence -> known verdict) ---------
# must-revise: the evidence contains a failing check the critic MUST flag.
CRITIC_ITEMS = [
    # 1) unknown column -> EXPLAIN error
    {"input": {"question": "Which town had the highest average property price in 2021?",
               "candidate_sql": "SELECT city, avg(price_gbp) FROM uk.uk_price_paid "
                                "WHERE toYear(date)=2021 GROUP BY city ORDER BY 2 DESC LIMIT 10",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=False\n"
                           "ERROR: Code 47. UNKNOWN_IDENTIFIER: Unknown expression identifier 'price_gbp'"},
     "expected_output": {"verdict": "revise", "must_flag": "unknown column price_gbp"}},
    # 2) missing LIMIT
    {"input": {"question": "List every UK property sale.",
               "candidate_sql": "SELECT * FROM uk.uk_price_paid",
               "evidence": "checks: read_only=True, has_limit=False\n"
                           "(no LIMIT clause present — unbounded scan rejected before execution)"},
     "expected_output": {"verdict": "revise", "must_flag": "missing LIMIT"}},
    # 3) cross-join full scan (executes but is a runaway)
    {"input": {"question": "How many taxi trips are there?",
               "candidate_sql": "SELECT count() FROM nyc_taxi.trips a, nyc_taxi.trips b",
               "evidence": "checks: read_only=True, has_limit=False, explain_ok=True, exec_ok=False\n"
                           "ERROR: Code 159. TIMEOUT_EXCEEDED: max_execution_time (cross join full scan)"},
     "expected_output": {"verdict": "revise", "must_flag": "cross join / missing LIMIT"}},
    # 4) non-SELECT (write attempt)
    {"input": {"question": "Remove old taxi trips.",
               "candidate_sql": "DELETE FROM nyc_taxi.trips WHERE pickup_date < '2010-01-01'",
               "evidence": "checks: read_only=False\nERROR: rejected: only SELECT/WITH permitted"},
     "expected_output": {"verdict": "revise", "must_flag": "non-SELECT / write statement"}},
    # 5) empty result for an answerable question (wrong filter)
    {"input": {"question": "How many property sales were recorded in 2021?",
               "candidate_sql": "SELECT count() FROM uk.uk_price_paid WHERE toYear(date)=1800 LIMIT 1",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=True, exec_ok=True, "
                           "nonempty_result=True\nExecution result (1 row(s)):\ncount()\n0"},
     "expected_output": {"verdict": "revise", "must_flag": "zero-count result — wrong year filter"}},
    # must-accept: all checks pass, SQL answers the question.
    # 6) trivial count
    {"input": {"question": "How many property sales are recorded in the UK price paid dataset?",
               "candidate_sql": "SELECT count() FROM uk.uk_price_paid LIMIT 1",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=True, exec_ok=True, "
                           "nonempty_result=True\nExecution result (1 row(s)):\ncount()\n28734000"},
     "expected_output": {"verdict": "accept", "must_flag": ""}},
    # 7) top tags group-by
    {"input": {"question": "What are the 10 most-answered tags on Stack Overflow?",
               "candidate_sql": "SELECT tag, count() AS answers FROM stackoverflow.posts "
                                "ARRAY JOIN arrayFilter(x -> x != '', splitByChar('|', tags)) AS tag "
                                "GROUP BY tag ORDER BY answers DESC LIMIT 10",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=True, exec_ok=True, "
                           "nonempty_result=True\nExecution result (10 row(s)):\ntag | answers\n"
                           "javascript | 2419000\npython | 2100000\n..."},
     "expected_output": {"verdict": "accept", "must_flag": ""}},
    # 8) corrected town query (the multi-iteration endpoint)
    {"input": {"question": "Which town had the highest average property price in 2021?",
               "candidate_sql": "SELECT town, round(avg(price)) AS avg_price FROM uk.uk_price_paid "
                                "WHERE toYear(date)=2021 GROUP BY town ORDER BY avg_price DESC LIMIT 10",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=True, exec_ok=True, "
                           "nonempty_result=True\nExecution result (10 row(s)):\ntown | avg_price\n"
                           "VIRGINIA WATER | 1650000\n..."},
     "expected_output": {"verdict": "accept", "must_flag": ""}},
    # 9) CTE / WITH form is allowed
    {"input": {"question": "What is the average taxi trip distance?",
               "candidate_sql": "WITH t AS (SELECT trip_distance FROM nyc_taxi.trips LIMIT 1000000) "
                                "SELECT round(avg(trip_distance), 2) AS avg_miles FROM t LIMIT 1",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=True, exec_ok=True, "
                           "nonempty_result=True\nExecution result (1 row(s)):\navg_miles\n2.87"},
     "expected_output": {"verdict": "accept", "must_flag": ""}},
    # 10) simple bounded select
    {"input": {"question": "Show 5 recent hacker news story titles.",
               "candidate_sql": "SELECT title FROM hackernews.items WHERE type='story' "
                                "ORDER BY time DESC LIMIT 5",
               "evidence": "checks: read_only=True, has_limit=True, explain_ok=True, exec_ok=True, "
                           "nonempty_result=True\nExecution result (5 row(s)):\ntitle\n..."},
     "expected_output": {"verdict": "accept", "must_flag": ""}},
]

# --- converged-sql golden endpoints (task + critique history -> final SQL) -----
CONVERGED_ITEMS = [
    {"input": {"question": "How many property sales are recorded in the UK price paid dataset?",
               "critique_history": []},
     "expected_output": {"sql": "SELECT count() FROM uk.uk_price_paid LIMIT 1"},
     "metadata": {"iterations_to_converge": 1}},
    {"input": {"question": "What are the 10 most-answered tags on Stack Overflow?",
               "critique_history": []},
     "expected_output": {"sql": "SELECT tag, count() AS answers FROM stackoverflow.posts "
                                "ARRAY JOIN arrayFilter(x -> x != '', splitByChar('|', tags)) AS tag "
                                "GROUP BY tag ORDER BY answers DESC LIMIT 10"},
     "metadata": {"iterations_to_converge": 1}},
    {"input": {"question": "Which town had the highest average property price in 2021?",
               "critique_history": ["iter 1: unknown column price_gbp — use price; table is uk.uk_price_paid"]},
     "expected_output": {"sql": "SELECT town, round(avg(price)) AS avg_price FROM uk.uk_price_paid "
                                "WHERE toYear(date)=2021 GROUP BY town ORDER BY avg_price DESC LIMIT 10"},
     "metadata": {"iterations_to_converge": 2}},
    {"input": {"question": "What is the average taxi trip distance in New York City?",
               "critique_history": ["iter 1: unbounded scan — add LIMIT / sample the table"]},
     "expected_output": {"sql": "SELECT round(avg(trip_distance), 2) AS avg_miles "
                                "FROM nyc_taxi.trips LIMIT 1"},
     "metadata": {"iterations_to_converge": 2}},
    {"input": {"question": "Which US airlines had the highest flight-delay rate?",
               "critique_history": ["iter 1: wrong column name for carrier",
                                    "iter 2: divide-by-zero on empty carrier group — add HAVING count()>0"]},
     "expected_output": {"sql": "SELECT Carrier, round(avg(DepDelay > 15), 3) AS delay_rate "
                                "FROM ontime GROUP BY Carrier HAVING count() > 1000 "
                                "ORDER BY delay_rate DESC LIMIT 10"},
     "metadata": {"iterations_to_converge": 3}},
    {"input": {"question": "What are the most downloaded Python packages?",
               "critique_history": ["iter 1: missing LIMIT"]},
     "expected_output": {"sql": "SELECT project, sum(count) AS downloads FROM pypi.pypi "
                                "GROUP BY project ORDER BY downloads DESC LIMIT 10"},
     "metadata": {"iterations_to_converge": 2}},
]


def critic_specs() -> List[Dict[str, Any]]:
    return [{"label": f"crit-{i:02d}", "id": f"crit-{i:02d}", "input": item["input"],
             "expectedOutput": item["expected_output"],
             "metadata": {"kind": "must-" + item["expected_output"]["verdict"]}}
            for i, item in enumerate(CRITIC_ITEMS, 1)]


def converged_specs() -> List[Dict[str, Any]]:
    return [{"label": f"conv-{i:02d}", "id": f"conv-{i:02d}", "input": item["input"],
             "expectedOutput": item["expected_output"], "metadata": item.get("metadata", {})}
            for i, item in enumerate(CONVERGED_ITEMS, 1)]


def seed(lf: v.Langfuse, dry_run: bool, report: Callable[[str, str], None] = print) -> int:
    """Reconcile both datasets and their items; return the failure count."""
    return (dataset_sync.sync(lf, CRITIC_DATASET, CRITIC_DESCRIPTION, DATASET_METADATA,
                              critic_specs(), dry_run, report)
            + dataset_sync.sync(lf, CONVERGED_DATASET, CONVERGED_DESCRIPTION, DATASET_METADATA,
                                converged_specs(), dry_run, report))


def capture_correction(lf: v.Langfuse, trace_id: str, observation_id: str, correction: str,
                       report: Callable[[str, str], None]) -> int:
    """One converged-sql item from a REAL Correction on a live trace, giving the dataset
    genuine trace lineage (field guide §Build Datasets). The CORRECTION score goes through
    the SDK; the item through the same reconcile rule as the rest."""
    try:
        import langfuse_config as lfc  # needs the langfuse package

        client = lfc.get_langfuse_client()
        if client is None:
            raise RuntimeError("Langfuse not configured")
        client.create_score(
            trace_id=trace_id, observation_id=observation_id,
            name="output", data_type="CORRECTION", value=correction,
            comment="golden refined SQL captured as a Correction",
        )
        client.flush()
    except Exception as e:  # noqa: BLE001
        report("failed", f"Correction score on trace {trace_id[:8]}: {e}")
        return 1
    spec = {"label": "conv-correction", "id": "conv-correction",
            "input": {"question": "(from corrected trace)", "critique_history": ["captured via Correction"]},
            "expectedOutput": {"sql": correction}, "metadata": {"origin": "correction"},
            "sourceTraceId": trace_id, "sourceObservationId": observation_id}
    failures = 0
    for status, label, err in dataset_sync.reconcile_items(lf, CONVERGED_DATASET, [spec], dry_run=False):
        report(status, f"item {label} in {CONVERGED_DATASET}" + (f": {err}" if err else ""))
        failures += status == "failed"
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
    ap.add_argument("--from-trace", default=None,
                    help="Trace id of a real refine run — derive a converged-sql item from a Correction")
    ap.add_argument("--from-observation", default=None,
                    help="The final generate-sql observation id on that trace")
    ap.add_argument("--correction", default=None,
                    help="The corrected/golden SQL text to store as a CORRECTION score")
    args = ap.parse_args(argv)

    cfg = v.runtime_config()
    if cfg.error:
        print(cfg.error, file=sys.stderr)
        return 1
    lf = v.Langfuse(cfg.base, cfg.public_key, cfg.secret_key)
    print(f"Refine-loop datasets at {cfg.base}" + (" (dry run)" if args.dry_run else ""))

    def report(status: str, what: str) -> None:
        print(f"  {status:<10} {what}")

    failures = seed(lf, args.dry_run, report)
    given = [args.from_trace, args.from_observation, args.correction]
    if any(given) and not all(given):
        report("failed", "--from-trace, --from-observation and --correction go together")
        failures += 1
    elif all(given) and args.dry_run:
        report("skipped", "Correction capture: not run in a dry run")
    elif all(given):
        failures += capture_correction(lf, args.from_trace, args.from_observation, args.correction, report)
    else:
        print("  (static golden items only. To add real trace lineage: run a refine trace, add a\n"
              "   Correction on its final generate-sql, then re-run with\n"
              "   --from-trace/--from-observation/--correction.)")
    print(f"\n{'FAILED' if failures else 'Done'}: {failures} failure(s). View: Langfuse UI > Datasets.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
