#!/usr/bin/env python3
"""
Aggregation-strategy experiment for the Support Triage Parallel demo.

Variable isolation (parallelization field guide): all branch/voter prompts are
pinned at ``production``; ONLY the aggregation strategy varies. Each strategy runs
the full best-of-N fan-out on the ``support-triage/sql-voting`` dataset, then a
run-level ``voting_accuracy_rate`` decides which aggregator wins.

Expected result: ``result-signature`` >= ``judge-consensus`` > ``majority-exact``
(string voting loses semantically-equal SQL).

Usage (from repo root, after sourcing .env):
    python demos/support-triage-parallel/scripts/run_experiment.py --strategy all
    python demos/support-triage-parallel/scripts/run_experiment.py --strategy result-signature --sample 4
    python demos/support-triage-parallel/scripts/run_experiment.py --strategy result-signature --ci
    python demos/support-triage-parallel/scripts/run_experiment.py --vary-n-samples 1,3,5
    python demos/support-triage-parallel/scripts/run_experiment.py --local-aggregator   # pure code, no LLM/network (CI)
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.seed_datasets import SQL_VOTING_DATASET, LOCAL_AGGREGATOR_CASES  # noqa: E402

STRATEGIES = ["majority-exact", "result-signature", "judge-consensus"]
CI_THRESHOLD = 0.8


# --------------------------------------------------------------------------- #
# Item + run-level evaluators (field-guide shapes).
# --------------------------------------------------------------------------- #
def item_accuracy(*, output, expected_output, **kw):
    from langfuse import Evaluation
    expected = (expected_output or {}).get("result_signature")
    got = (output or {}).get("result_signature")
    if not expected:
        # No pinned signature (unreachable playground at seed time) -> skip.
        return Evaluation(name="item_accuracy", value=None,
                          comment="no pinned expected signature")
    return Evaluation(name="item_accuracy", value=1.0 if got == expected else 0.0,
                      comment=f"got {got} vs expected {expected}")


def voting_agreement_rate(*, item_results, **kw):
    from langfuse import Evaluation
    vals = [e.value for r in item_results for e in r.evaluations
            if e.name == "item_accuracy" and e.value is not None]
    rate = sum(vals) / len(vals) if vals else None
    comment = (f"majority answer matched pinned signature in {rate:.0%} of {len(vals)} scored items"
               if rate is not None else "no scored items")
    return Evaluation(name="voting_accuracy_rate", value=rate, comment=comment)


def mean_consensus_confidence(*, item_results, **kw):
    from langfuse import Evaluation
    confs = [(r.output or {}).get("consensus_confidence") for r in item_results]
    confs = [c for c in confs if c is not None]
    val = sum(confs) / len(confs) if confs else None
    return Evaluation(name="mean_consensus_confidence", value=val)


def make_task(strategy: str, n_samples: int):
    from sql_voting import vote_sql

    def task(*, item, **kwargs):
        inp = item.input if hasattr(item, "input") else item.get("input", {})
        question = inp.get("question") if isinstance(inp, dict) else str(inp)
        return asyncio.run(vote_sql(question, strategy=strategy, n_samples=n_samples))

    return task


def _run_one(lf, dataset, strategy, n_samples, label, sample):
    items = dataset.items
    if sample and sample < len(items):
        items = items[:sample]
    run_name = f"aggregator-{strategy}" + (f"-n{n_samples}" if n_samples != 5 else "")
    if label:
        run_name += f"-{label}"
    print(f"\n=== strategy={strategy}  n_samples={n_samples}  items={len(items)}  run={run_name} ===")
    result = dataset.run_experiment(
        name="sql-voting aggregator comparison",
        run_name=run_name,
        description=f"Aggregator strategy '{strategy}' at n_samples={n_samples} "
                    f"(branches/voter pinned at production).",
        task=make_task(strategy, n_samples),
        evaluators=[item_accuracy],
        run_evaluators=[voting_agreement_rate, mean_consensus_confidence],
        metadata={"varied_component": "aggregator", "strategy": strategy,
                  "n_samples": n_samples},
    )
    try:
        print(result.format())
    except Exception:
        pass
    return result


def _accuracy_of(result):
    for ev in getattr(result, "run_evaluations", []) or []:
        if ev.name == "voting_accuracy_rate" and ev.value is not None:
            return ev.value
    return None


def run_local_aggregator():
    """Pure-code CI target: exercise compute_tally + build_synthesis_input against
    the local aggregator cases. No LLM, no network, no Langfuse required."""
    from sql_voting import compute_tally
    from triage_pipeline import build_synthesis_input
    passed = failed = 0
    for i, case in enumerate(LOCAL_AGGREGATOR_CASES):
        inp, exp = case["input"], case["expected_output"]
        if "candidates" in inp:
            t = compute_tally(inp["candidates"])
            checks = {k: t.get(k) == v for k, v in exp.items()}
        else:
            outputs, failed_n, degraded = build_synthesis_input(inp["branch_results"])
            actual = {"degraded": degraded, "failed_branches": failed_n,
                      "sentiment": outputs.get("sentiment")}
            checks = {k: actual.get(k) == v for k, v in exp.items()}
        ok = all(checks.values())
        passed += ok
        failed += (not ok)
        print(f"  case {i}: {'PASS' if ok else 'FAIL'}  {checks}")
    print(f"\nLocal aggregator: {passed} passed, {failed} failed")
    return failed == 0


def main():
    ap = argparse.ArgumentParser(description="Support Triage aggregation-strategy experiment")
    ap.add_argument("--strategy", default="result-signature",
                    help="all | result-signature | majority-exact | judge-consensus")
    ap.add_argument("--sample", type=int, default=None, help="Run only the first N items")
    ap.add_argument("--label", default=None, help="Suffix for the run name")
    ap.add_argument("--vary-n-samples", default=None,
                    help="Comma list of N to chart diminishing returns, e.g. 1,3,5")
    ap.add_argument("--ci", action="store_true",
                    help=f"Exit 1 if voting_accuracy_rate < {CI_THRESHOLD}")
    ap.add_argument("--local-aggregator", action="store_true",
                    help="Run the pure-code aggregator cases only (no LLM/network)")
    args = ap.parse_args()

    if args.local_aggregator:
        ok = run_local_aggregator()
        sys.exit(0 if ok or not args.ci else 1)

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first).")

    from langfuse import get_client
    lf = get_client()
    try:
        dataset = lf.get_dataset(SQL_VOTING_DATASET)
    except Exception as e:
        raise SystemExit(f"Could not load dataset '{SQL_VOTING_DATASET}': {e}\n"
                         f"Run scripts/seed_datasets.py first.")

    results = []
    if args.vary_n_samples:
        ns = [int(x) for x in args.vary_n_samples.split(",") if x.strip()]
        for n in ns:
            results.append(_run_one(lf, dataset, args.strategy, n, args.label, args.sample))
    else:
        strategies = STRATEGIES if args.strategy == "all" else [args.strategy]
        for strat in strategies:
            results.append(_run_one(lf, dataset, strat, 5, args.label, args.sample))

    lf.flush()

    if args.ci:
        worst = min((a for a in (_accuracy_of(r) for r in results) if a is not None), default=None)
        if worst is None:
            print("\nCI: no accuracy scored (no pinned signatures?) — not gating.", file=sys.stderr)
        elif worst < CI_THRESHOLD:
            print(f"\nCI GATE FAILED: voting_accuracy_rate {worst:.2f} < {CI_THRESHOLD}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"\nCI GATE PASSED: min voting_accuracy_rate {worst:.2f} >= {CI_THRESHOLD}")


if __name__ == "__main__":
    main()
