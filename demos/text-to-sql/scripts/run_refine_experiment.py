#!/usr/bin/env python3
"""Run the two single-variable refine-loop experiments (Pattern #5).

Both runs pin MAX_ITERATIONS identically across every arm — it is never a hidden
variable — and report `avg_iterations_to_converge` alongside an INDEPENDENT
`execution_success_rate` run-evaluator that deterministically re-executes each
run's final SQL (ground truth, NOT the critic's opinion).

  Run A — fix critic, vary generator:  generator `production` vs `candidate`
          (schema-hinted). Hypothesis: candidate converges in fewer iterations at
          equal correctness.
  Run B — fix generator, vary critic rubric:  critic `production` (evidence-
          grounded) vs `opinion-only`. Hypothesis — THE COLLUSION DEMO:
          opinion-only shows LOWER avg iterations + HIGHER acceptance, but
          `execution_success_rate` is WORSE. The critic got happier; the SQL
          didn't get better (Pan et al., arXiv:2407.04549).

Run (from demos/text-to-sql/, after sourcing repo .env; needs live ClickHouse + Langfuse):
    python scripts/run_refine_experiment.py --run all
    python scripts/run_refine_experiment.py --run B --sample 4
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import langfuse_config as lf  # noqa: E402
from sql_evidence import gather_evidence  # noqa: E402
from sql_refine_loop import run_refine_loop  # noqa: E402

MAX_ITERATIONS = 3  # pinned identically across every arm — never a hidden variable
EXPERIMENT_DATASET = os.getenv("REFINE_EXPERIMENT_DATASET", "text-to-sql/converged-sql")


def _question(item) -> str:
    inp = item.input
    if isinstance(inp, dict):
        return inp.get("question") or str(inp)
    return str(inp)


def refine_task(*, item, generator_label="production", critic_label="production", **kwargs):
    """Run the refine loop for one dataset item; return the fields the
    run-evaluators need. MAX_ITERATIONS is pinned so the only variable is the arm's
    generator/critic label."""
    q = _question(item)
    # analysis is a lightweight hint; keep it constant so the varied component is
    # the only difference between arms.
    analysis = "Use the ClickHouse public demo datasets (uk, nyc_taxi, stackoverflow, ontime, pypi, hackernews)."
    res = run_refine_loop(
        q, analysis, generator_label=generator_label, critic_label=critic_label,
        max_iterations=MAX_ITERATIONS,
    )
    return {"sql": res.sql, "iterations": res.iterations,
            "converged": res.converged, "stop_reason": res.stop_reason}


def avg_iterations(*, item_results, **kwargs):
    from langfuse import Evaluation
    vals = [r.output["iterations"] for r in item_results if isinstance(r.output, dict)]
    return Evaluation(name="avg_iterations_to_converge",
                      value=(sum(vals) / len(vals)) if vals else None)


def execution_success_rate(*, item_results, **kwargs):
    """Independent ground truth: re-execute each run's final SQL and measure the
    real success rate — the signal a colluding critic cannot inflate."""
    from langfuse import Evaluation
    oks = []
    for r in item_results:
        if not isinstance(r.output, dict) or not r.output.get("sql"):
            oks.append(False)
            continue
        oks.append(bool(gather_evidence(r.output["sql"]).checks.get("exec_ok", False)))
    return Evaluation(name="execution_success_rate",
                      value=(sum(oks) / len(oks)) if oks else None)


RUN_EVALUATORS = [avg_iterations, execution_success_rate]


def _run(dataset, name, *, generator_label, critic_label, varied, sample):
    items = list(dataset.items)
    if sample:
        items = items[:sample]
    print(f"\n=== {name} ===  (varied={varied}, generator={generator_label}, "
          f"critic={critic_label}, items={len(items)}, MAX_ITERATIONS={MAX_ITERATIONS})")

    def task(*, item, **kw):
        return refine_task(item=item, generator_label=generator_label, critic_label=critic_label)

    result = dataset.run_experiment(
        name=name,
        description=f"Refine-loop experiment; varied={varied}; "
                    f"generator={generator_label}; critic={critic_label}; MAX_ITERATIONS={MAX_ITERATIONS}.",
        task=task,
        run_evaluators=RUN_EVALUATORS,
        metadata={"varied_component": varied, "generator_label": generator_label,
                  "critic_label": critic_label, "max_iterations": MAX_ITERATIONS},
    )
    try:
        print(result.format())
    except Exception:
        print("(experiment complete)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=["A", "B", "all"], default="all")
    ap.add_argument("--sample", type=int, default=None, help="Only run the first N dataset items")
    args = ap.parse_args()

    client = lf.get_langfuse_client()
    if client is None:
        raise SystemExit("Langfuse not configured. Source .env first.")
    try:
        dataset = client.get_dataset(EXPERIMENT_DATASET)
    except Exception as e:
        raise SystemExit(f"Dataset '{EXPERIMENT_DATASET}' not found ({e}). "
                         "Run scripts/seed_refine_datasets.py first.")

    if args.run in ("A", "all"):
        # Run A — fix critic (production), vary generator.
        _run(dataset, "generator: production", generator_label="production",
             critic_label="production", varied="generator", sample=args.sample)
        _run(dataset, "generator: candidate", generator_label="candidate",
             critic_label="production", varied="generator", sample=args.sample)
    if args.run in ("B", "all"):
        # Run B — fix generator (production), vary critic rubric. THE COLLUSION DEMO.
        _run(dataset, "critic rubric: production", generator_label="production",
             critic_label="production", varied="critic_rubric", sample=args.sample)
        _run(dataset, "critic rubric: opinion-only", generator_label="production",
             critic_label="opinion-only", varied="critic_rubric", sample=args.sample)

    client.flush()
    print("\n✓ View: Langfuse UI > Datasets > " + EXPERIMENT_DATASET + " > Runs")


if __name__ == "__main__":
    main()
