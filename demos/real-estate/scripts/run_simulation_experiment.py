"""
Run a SIMULATED MULTI-TURN experiment: an LLM plays each buyer persona and holds
a real conversation with the concierge, then the whole trajectory is scored.

This is the eval that catches what the single-turn experiment structurally
cannot — a constraint dropped three turns later, "that one" resolved to the
wrong flat, the same question asked twice. One dataset item = one persona = one
conversation = ONE trace, readable top to bottom:

    <experiment item span>
    ├─ simulated-user                       (generation) the buyer's turn 1
    ├─ handle-concierge-chat-message        (span)       the agent's turn 1
    ├─ simulated-user                       (generation) the buyer's turn 2
    ├─ handle-concierge-chat-message        (span)       the agent's turn 2
    └─ …                                                 until [[DONE]] or --max-turns

Every turn calls `run_turn(..., is_experiment=True)`, which skips
`propagate_attributes` and lets the turn nest under whatever span is already
active — here, the experiment item's — instead of opening a trace of its own.

COST WARNING: each item is `--max-turns` agent turns (each of which is a plan
call plus an agentic tool loop) plus a simulator call per turn plus three
trajectory judges. That is roughly an order of magnitude more spend per item
than scripts/run_experiment.py, which is why `--max-concurrency` defaults to 2
and why the run refuses to start without `--yes` (or a yes on a TTY).

Run:
    ./.venv/bin/python scripts/run_simulation_experiment.py --yes
    ./.venv/bin/python scripts/run_simulation_experiment.py --max-turns 4 --yes
    ./.venv/bin/python scripts/run_simulation_experiment.py --model gpt-4o --yes
    ./.venv/bin/python scripts/run_simulation_experiment.py --prompt-label candidate --yes
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import (  # noqa: E402
    get_langfuse, verify_project, record_score, flush_langfuse,
    AGENT_MODEL, langfuse_api,
)
from agent.concierge import run_turn  # noqa: E402
from agent.scoring import run_code_evaluators  # noqa: E402
from agent.simulated_user import (  # noqa: E402
    simulated_user_reply, is_done, strip_done, SIMULATED_USER_MODEL,
)
from data.personas import DATASET_NAME  # noqa: E402
from evaluators.conversation_evaluators import (  # noqa: E402
    CONVERSATION_EVALUATORS, CONVERSATION_RUN_EVALUATORS,
    CONVERSATION_JUDGES, annotation_comment,
)

# Upper bound on LLM calls inside one agent turn: 1 plan call + agent/concierge's
# MAX_ITERS tool-loop iterations. Only used for the cost estimate below.
_LLM_CALLS_PER_AGENT_TURN = 6


def make_task(model, prompt_label, max_turns, show_transcript):
    """Build a task that runs a whole CONVERSATION for one persona item.

    Returns the trajectory, not an answer: the evaluators score the transcript.
    """
    def simulation_task(*, item, **kwargs):
        item_input = item.input if isinstance(item.input, dict) else {}
        persona = str(item_input.get("persona") or "")
        scenario = str(item_input.get("scenario") or "")

        lf = get_langfuse()
        # ONE list, in CONCIERGE point of view: it is both the `history=` handed
        # to run_turn and the transcript handed to the evaluators. The simulated
        # user inverts the roles internally (see agent/simulated_user.py) so
        # there is never a second, divergent copy of the conversation.
        transcript = []
        per_turn = []
        reached_done = False
        closing_remark = ""

        for turn_index in range(max_turns):
            user_message = simulated_user_reply(persona, scenario, transcript)
            if is_done(user_message):
                # The sentinel turn is a control signal, not dialogue: the agent
                # never sees it, so it is not appended to the transcript and not
                # counted by `turns-to-resolution`. Anything the simulator wrote
                # alongside the sentinel is kept for the log only.
                reached_done = True
                closing_remark = strip_done(user_message)
                break

            result = run_turn(
                user_message,
                is_experiment=True, model=model, prompt_label=prompt_label,
                # A copy: run_turn builds its own message list from this, and a
                # shared list would be an easy way to leak tool-call blocks back
                # into the transcript the judges read.
                history=list(transcript), turn_index=turn_index,
            )
            transcript.append({"role": "user", "content": user_message})
            transcript.append({"role": "assistant", "content": result["answer"]})

            # --- per-turn CODE scores, attached HERE and not in run_turn -------
            # run_turn skips its own code scores when is_experiment=True, and for
            # the single-turn experiment that is right: the item-level evaluators
            # re-check the same properties against the dataset's ground-truth
            # constraints, so scoring inside the turn would duplicate score names
            # on one trace with weaker inputs.
            #
            # A simulated CONVERSATION is the opposite case. It has no per-turn
            # ground truth (nobody wrote an expected answer for turn 4 of an
            # improvised dialogue) and its item-level evaluators score the
            # TRAJECTORY — so without this, the individual turns of a
            # conversation trace would carry no scores at all, and "which turn
            # regressed?" would be unanswerable. Attaching them per turn is safe
            # because each lands on a DIFFERENT observation (that turn's
            # synthesis generation), so the same five names coexist on one trace
            # without clashing. Done in the runner rather than by changing
            # run_turn so the single-turn experiment's behaviour is untouched.
            turn_scores = {}
            for s in run_code_evaluators(result):
                turn_scores[s.name] = s.value
                if result.get("final_generation_id"):
                    record_score(lf, trace_id=result["trace_id"],
                                 observation_id=result["final_generation_id"],
                                 name=s.name, value=s.value, data_type=s.data_type,
                                 comment=s.comment)

            per_turn.append({
                "turn": turn_index + 1,
                "user": user_message,
                "listings_shown": result["listings_shown"],
                "tools_called": result["tools_called"],
                "response_language": result["response_language"],
                "final_generation_id": result.get("final_generation_id"),
                "scores": turn_scores,
            })

        conversation = {
            "transcript": transcript,
            "turns": len(per_turn),
            "reached_done": reached_done,
            # Passed through so `reached-done` / `turns-to-resolution` can say
            # "truncated at the cap" instead of just "did not finish".
            "max_turns": max_turns,
            "per_turn": per_turn,
        }

        if show_transcript:
            # One atomic multi-line block per conversation, so concurrent items
            # interleave as whole transcripts rather than shuffled lines.
            outcome = "finished" if reached_done else "hit turn cap"
            parting = f" — buyer's parting words: {closing_remark}" if closing_remark else ""
            block = annotation_comment({**conversation, "persona": persona,
                                        "scenario": scenario})
            print(f"\n{'─' * 72}\n[{item.id}] {outcome} after {len(per_turn)} "
                  f"turn(s){parting}\n{block}\n", flush=True)
        return conversation
    return simulation_task


def confirm(args, n_items) -> None:
    """Print the run's shape and refuse to spend money by accident.

    A 7-persona x 6-turn run is ~250 LLM calls; a mistyped flag on a bigger
    dataset is real money on a shared Langfuse Cloud project. So: always show
    the estimate, then require an explicit yes — from `--yes`, or from a human
    when stdin is a terminal. Non-interactive and no `--yes` means abort, never
    "assume yes".
    """
    agent_turns = n_items * args.max_turns
    # max_turns + 1: after the last agent turn the simulator is called once more,
    # and that call is what emits [[DONE]].
    simulator_calls = n_items * (args.max_turns + 1)
    judge_calls = n_items * len(CONVERSATION_JUDGES)
    print("  cost shape (upper bound):")
    print(f"    {n_items} conversation(s) x up to {args.max_turns} turns")
    print(f"    {agent_turns} agent turns  -> up to "
          f"{agent_turns * _LLM_CALLS_PER_AGENT_TURN} LLM calls on {args.model}")
    print(f"    {simulator_calls} simulated-user calls on {SIMULATED_USER_MODEL}")
    print(f"    {judge_calls} trajectory judge calls")
    print(f"    ~{agent_turns * _LLM_CALLS_PER_AGENT_TURN + simulator_calls + judge_calls} "
          f"LLM calls total, all billed to live API keys\n")

    if args.yes:
        return
    if not sys.stdin.isatty():
        print("Refusing to start: this run costs real LLM spend and writes to a shared "
              "Langfuse project.\nRe-run with --yes to confirm.", file=sys.stderr)
        sys.exit(1)
    if input("  Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-turns", type=int, default=9,
                    help="Hard cap on agent turns per conversation (default 9). The "
                         "simulated buyer usually finishes sooner via [[DONE]].")
    ap.add_argument("--model", default=AGENT_MODEL,
                    help="Agent model under test, e.g. claude-sonnet-4-6 or gpt-4o")
    ap.add_argument("--prompt-label", default="production",
                    help="Which prompt version the agent runs: production, candidate, first-draft")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--max-concurrency", type=int, default=2,
                    help="Concurrent conversations (default 2 — each is many LLM calls, "
                         "unlike the single-turn experiment's default of 4)")
    ap.add_argument("--dataset", default=DATASET_NAME,
                    help=f"Persona dataset to run (default {DATASET_NAME})")
    ap.add_argument("--no-transcripts", action="store_true",
                    help="Do not print each conversation as it completes")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the cost confirmation (required when not on a TTY)")
    args = ap.parse_args()

    verify_project()
    lf = get_langfuse()

    try:
        dataset = lf.get_dataset(args.dataset)
    except Exception as e:
        print(f"ERROR loading dataset '{args.dataset}': {e}\n"
              f"Run scripts/seed_persona_dataset.py first.")
        sys.exit(1)

    # Run name encodes both axes, same convention as scripts/run_experiment.py,
    # with a `sim-` prefix so trajectory runs never look like single-turn runs in
    # a Runs tab (they are not comparable — different dataset, different scores).
    default_run_name = (f"sim-{args.model}" if args.prompt_label == "production"
                        else f"sim-{args.model}-{args.prompt_label}")
    run_name = args.run_name or default_run_name

    print(f"Simulated multi-turn experiment on '{args.dataset}' "
          f"({len(dataset.items)} personas)")
    print(f"  run_name={run_name}  model={args.model}  prompt_label={args.prompt_label}  "
          f"max_turns={args.max_turns}  concurrency={args.max_concurrency}\n")
    confirm(args, len(dataset.items))

    # Same idempotency step as run_experiment.py: re-running with an existing
    # run_name APPENDS to that run, silently mixing old and new conversations and
    # skewing the aggregates. Drop the prior run so each invocation is a clean
    # snapshot; pass a distinct --run-name to keep several.
    st, _ = langfuse_api("DELETE", f"/api/public/datasets/{args.dataset}/runs/{run_name}")
    if st == 200:
        print(f"  (replaced existing run '{run_name}')\n")

    result = dataset.run_experiment(
        name=args.dataset,
        run_name=run_name,
        description=f"Simulated multi-turn conversations with {args.model} "
                    f"(prompt={args.prompt_label}, max_turns={args.max_turns}); "
                    f"buyer played by {SIMULATED_USER_MODEL}; trajectory-level scores.",
        task=make_task(args.model, args.prompt_label, args.max_turns,
                       not args.no_transcripts),
        evaluators=CONVERSATION_EVALUATORS,
        run_evaluators=CONVERSATION_RUN_EVALUATORS,
        max_concurrency=args.max_concurrency,
        metadata={"model": args.model, "prompt_label": args.prompt_label,
                  "max_turns": args.max_turns,
                  "simulated_user_model": SIMULATED_USER_MODEL,
                  "eval_kind": "simulated-multi-turn"},
    )

    # flush_langfuse (not lf.flush): run_turn does NOT flush in experiment mode,
    # so every span of every conversation is still buffered here — and this also
    # flushes the optional mirror exporter, which lf.flush() does not.
    flush_langfuse(lf)
    try:
        print(result.format())
    except Exception:
        print("Experiment complete.")
    print(f"\n✓ View: Langfuse UI > Datasets > {args.dataset} > Runs tab > {run_name}")


if __name__ == "__main__":
    main()
