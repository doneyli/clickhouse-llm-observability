"""
Run a dataset experiment: execute the concierge agent on every dataset item,
score each with the CODE + LLM-as-a-Judge evaluators, and record run-level
aggregates. Shows up in Langfuse under Datasets > property-concierge-eval > Runs.

Run:
    ./.venv/bin/python scripts/run_experiment.py
    ./.venv/bin/python scripts/run_experiment.py --run-name my-run --max-concurrency 3

Two axes of comparison, same dataset + evaluators:
    --model claude-sonnet-4-6 | gpt-4o    # compare model providers
    --prompt-label production | candidate  # compare prompt versions (closes the loop)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project, AGENT_MODEL, langfuse_api
from agent.concierge import run_turn
from data.dataset import DATASET_NAME
from evaluators.experiment_evaluators import ALL_EVALUATORS, RUN_EVALUATORS


def make_task(model, prompt_label):
    """Build a task bound to a specific agent model + prompt version.
    Model varies for the Claude-vs-GPT compare; prompt_label varies for the
    production-vs-candidate prompt compare. Returns the structured TurnResult so
    evaluators can inspect retrieval."""
    def concierge_task(*, item, **kwargs):
        q = item.input.get("question") if isinstance(item.input, dict) else str(item.input)
        return run_turn(q, is_experiment=True, model=model, prompt_label=prompt_label)
    return concierge_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=AGENT_MODEL,
                    help="Agent model to evaluate, e.g. claude-sonnet-4-6 or gpt-4o")
    ap.add_argument("--prompt-label", default="production",
                    help="Which prompt version to run: production (baseline) or candidate")
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

    # Default run name encodes both axes so runs never collide in the Runs tab:
    # the baseline is just the model; a non-production prompt appends its label.
    default_run_name = args.model if args.prompt_label == "production" else f"{args.model}-{args.prompt_label}"
    run_name = args.run_name or default_run_name
    print(f"Experiment on '{DATASET_NAME}' ({len(dataset.items)} items)")
    print(f"  run_name={run_name}  model={args.model}  prompt_label={args.prompt_label}  "
          f"concurrency={args.max_concurrency}\n")

    # Re-running with an existing run_name APPENDS items to that run, silently
    # mixing old + new results (and skewing the aggregates) — especially after the
    # dataset composition changes. Make re-runs idempotent: drop a prior run of the
    # same name so each invocation is a clean snapshot. (To keep multiple runs of
    # one config, pass a distinct --run-name.)
    st, _ = langfuse_api("DELETE", f"/api/public/datasets/{DATASET_NAME}/runs/{run_name}")
    if st == 200:
        print(f"  (replaced existing run '{run_name}')\n")

    result = dataset.run_experiment(
        name=DATASET_NAME,
        run_name=run_name,
        description=f"Property Concierge evaluation with {args.model} "
                    f"(prompt={args.prompt_label}; code evaluators + LLM-as-a-Judge).",
        task=make_task(args.model, args.prompt_label),
        evaluators=ALL_EVALUATORS,
        run_evaluators=RUN_EVALUATORS,
        max_concurrency=args.max_concurrency,
        metadata={"model": args.model, "prompt_label": args.prompt_label},
    )

    lf.flush()
    try:
        print(result.format())
    except Exception:
        print("Experiment complete.")
    print("\n✓ View: Langfuse UI > Datasets > property-concierge-eval > Runs tab")


if __name__ == "__main__":
    main()
