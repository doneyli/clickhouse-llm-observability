"""
Run a dataset experiment: execute the concierge agent on every dataset item,
score each with the CODE + LLM-as-a-Judge evaluators, and record run-level
aggregates. Shows up in Langfuse under Datasets > property-concierge-eval > Runs.

Run:
    ./.venv/bin/python scripts/run_experiment.py
    ./.venv/bin/python scripts/run_experiment.py --run-name my-run --max-concurrency 3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project, AGENT_MODEL
from agent.concierge import run_turn
from data.dataset import DATASET_NAME
from evaluators.experiment_evaluators import ALL_EVALUATORS, RUN_EVALUATORS


def make_task(model):
    """Build a task bound to a specific agent model (for Claude-vs-GPT compare).
    Returns the structured TurnResult so evaluators can inspect retrieval."""
    def concierge_task(*, item, **kwargs):
        q = item.input.get("question") if isinstance(item.input, dict) else str(item.input)
        return run_turn(q, is_experiment=True, model=model)
    return concierge_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=AGENT_MODEL,
                    help="Agent model to evaluate, e.g. claude-sonnet-4-6 or gpt-4o")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--max-concurrency", type=int, default=4)
    args = ap.parse_args()

    verify_project()
    lf = get_langfuse()

    try:
        dataset = lf.get_dataset(DATASET_NAME)
    except Exception as e:
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\nRun scripts/seed_dataset.py first.")
        sys.exit(1)

    run_name = args.run_name or args.model
    print(f"Experiment on '{DATASET_NAME}' ({len(dataset.items)} items)")
    print(f"  run_name={run_name}  model={args.model}  concurrency={args.max_concurrency}\n")

    result = dataset.run_experiment(
        name=DATASET_NAME,
        run_name=run_name,
        description=f"Property Concierge evaluation with {args.model} "
                    f"(code evaluators + LLM-as-a-Judge).",
        task=make_task(args.model),
        evaluators=ALL_EVALUATORS,
        run_evaluators=RUN_EVALUATORS,
        max_concurrency=args.max_concurrency,
        metadata={"model": args.model},
    )

    lf.flush()
    try:
        print(result.format())
    except Exception:
        print("Experiment complete.")
    print("\n✓ View: Langfuse UI > Datasets > property-concierge-eval > Runs tab")


if __name__ == "__main__":
    main()
