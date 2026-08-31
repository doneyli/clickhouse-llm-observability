"""
Run the N+1 multi-turn experiment: replay each frozen conversation's turns 1..N as
history, run the agent on turn N+1 only, and score that one turn with the
cross-turn evaluators plus the single-turn code evaluators. Shows up in Langfuse
under Datasets > property-concierge-conversations > Runs.

Why this is a normal single-item experiment: the conversation is INPUT, not
something the runner has to simulate. Each item produces exactly one trace of one
`handle-concierge-chat-message` turn — the same trace shape as live traffic — so
the item-level scores attach cleanly and the Runs tab comparison works the same
way it does for the single-turn dataset.

Run:
    ./.venv/bin/python scripts/run_n_plus_1_experiment.py
    ./.venv/bin/python scripts/run_n_plus_1_experiment.py --run-name my-run --max-concurrency 3

Two axes of comparison, same dataset + evaluators:
    --model claude-sonnet-4-6 | gpt-4o     # compare model providers
    --prompt-label production | candidate  # compare prompt versions (closes the loop)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project, AGENT_MODEL, langfuse_api
from agent.concierge import run_turn
from data.conversations import DATASET_NAME
from evaluators.conversation_experiment_evaluators import (
    N_PLUS_1_EVALUATORS, N_PLUS_1_RUN_EVALUATORS,
)


def make_task(model, prompt_label):
    """Build an N+1 task bound to a specific agent model + prompt version.

    The item's `history` is passed straight to `run_turn`, which threads it into
    both the planner and the agent loop — so "keep it under €400,000" or "the
    second option" resolve against the replayed conversation exactly as they would
    in production. `turn_index` is derived from the history length (one
    user+assistant exchange per turn) so the trace's `turn` metadata says which
    turn of the conversation this really is, not "turn 1".
    """
    def n_plus_1_task(*, item, **kwargs):
        history = item.input.get("history", [])
        return run_turn(item.input["question"], history=history,
                        turn_index=len(history) // 2, is_experiment=True,
                        model=model, prompt_label=prompt_label)
    return n_plus_1_task


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
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\n"
              f"Run scripts/seed_conversation_dataset.py first.")
        sys.exit(1)

    # Default run name encodes both axes so runs never collide in the Runs tab:
    # the baseline is just the model; a non-production prompt appends its label.
    default_run_name = args.model if args.prompt_label == "production" else f"{args.model}-{args.prompt_label}"
    run_name = args.run_name or default_run_name
    print(f"N+1 experiment on '{DATASET_NAME}' ({len(dataset.items)} conversations)")
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
        description=f"Property Concierge N+1 multi-turn evaluation with {args.model} "
                    f"(prompt={args.prompt_label}; cross-turn + single-turn code evaluators).",
        task=make_task(args.model, args.prompt_label),
        evaluators=N_PLUS_1_EVALUATORS,
        run_evaluators=N_PLUS_1_RUN_EVALUATORS,
        max_concurrency=args.max_concurrency,
        metadata={"model": args.model, "prompt_label": args.prompt_label,
                  "method": "n-plus-1"},
    )

    lf.flush()
    try:
        print(result.format())
    except Exception:
        print("Experiment complete.")
    print("\n✓ View: Langfuse UI > Datasets > property-concierge-conversations > Runs tab")


if __name__ == "__main__":
    main()
