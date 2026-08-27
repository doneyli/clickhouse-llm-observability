"""
Simulate ONE long, realistic multi-turn conversation to demonstrate the
Langfuse Sessions view with a session deep enough to be visually convincing
(run_live_traffic.py's seeded session is only 3 turns).

Same pattern as run_live_traffic.py's SESSION block: every turn is its own
trace, threaded together by a shared session_id, with prior turns passed as
`history` so follow-ups ("that one", "the Chamberí apartment") resolve
correctly. No fault injection — this is meant to look like a clean, coherent
conversation end-to-end.

Run:
    ./.venv/bin/python scripts/simulate_long_session.py
    ./.venv/bin/python scripts/simulate_long_session.py --session-id sess-my-demo-001
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, record_score, flush_langfuse, verify_project
from agent.concierge import run_turn, CONVERSATION_END_TAG, SNAPSHOT_NAME
from agent.scoring import judge_groundedness, judge_tone, code_grounded_listings

COMPLEMENTARY_JUDGES = [judge_groundedness, judge_tone]

USER_ID = "elena.v"

# A single buyer working through the full arc: initial ask -> refine -> compare
# neighborhoods -> commit -> mortgage math -> nearby alternatives -> listing
# detail follow-up -> amenities -> resale/investment question -> closing summary.
CONVERSATION = [
    "Hi, I'm looking to buy a 2-bedroom apartment in Madrid. My budget is around €480,000.",
    "I'd like it to be close to a metro station if possible.",
    "Between Malasaña and Chamberí, which is quieter and safer for a family?",
    "Let's go with the Chamberí one. What would the estimated monthly mortgage be?",
    "What if I put down 30% instead of 20% — how much lower would the monthly payment be?",
    "How does the average price per square meter in Chamberí compare to Malasaña?",
    "Are there any cheaper 2-bedroom options nearby, just so I can compare?",
    "Tell me more about that Lavapiés one — is it big enough for two people working from home?",
    "Does the Chamberí apartment have air conditioning, and what's its energy rating?",
    "Is parking included with that one?",
    "If we bought it and needed to sell in five years, does Chamberí tend to hold its value?",
    "This has been really helpful. Can you summarize the best option for us, with the "
    "all-in estimated monthly cost including the mortgage?",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", default="sess-madrid-buyer-longconvo-001")
    ap.add_argument("--no-judge", action="store_true", help="Skip LLM-as-a-Judge trace scores (faster)")
    args = ap.parse_args()

    verify_project()
    lf = get_langfuse()

    print(f"\nSimulating a {len(CONVERSATION)}-turn conversation in session "
          f"'{args.session_id}' ({'no ' if args.no_judge else ''}LLM-judge scores)...\n")

    history = []
    grounded_turns = 0
    for turn_index, query in enumerate(CONVERSATION):
        is_final_turn = turn_index == len(CONVERSATION) - 1
        print(f"[{turn_index + 1:2}/{len(CONVERSATION)}] {query[:76]}")
        # The LAST turn is flagged so the agent tags it `conversation_end` and
        # emits the `conversation-snapshot` observation a conversation-level
        # judge runs on. Only this loop knows the conversation is over — Langfuse
        # never does, which is exactly why no managed judge can target a session.
        result = run_turn(query, session_id=args.session_id, user_id=USER_ID,
                          extra_tags=["live", "session-demo", "long-session-demo"],
                          history=history, turn_index=turn_index,
                          is_final_turn=is_final_turn)
        history = history + [{"role": "user", "content": query},
                             {"role": "assistant", "content": result["answer"]}]

        # Per-turn code scores are attached inside run_turn; re-derive just the
        # grounding verdict here to aggregate it across the conversation below.
        if code_grounded_listings(result).value:
            grounded_turns += 1

        if not args.no_judge:
            for judge in COMPLEMENTARY_JUDGES:
                s = judge(result)
                record_score(lf, trace_id=result["trace_id"],
                             observation_id=result.get("final_generation_id"),
                             name=s.name, value=s.value, data_type=s.data_type, comment=s.comment)

        shown = ", ".join(result["listings_shown"]) or "(none)"
        print(f"        -> tools={result['tools_called']} shown={shown} trace={result['trace_id'][:12]}")

    # --- SESSION-level score: the one thing no managed evaluator can produce ---
    # Turn-level scores cannot answer "was this CONVERSATION any good". A score
    # whose subject is the session can, and `session_id=` is the only route to
    # one (the alternative being a human annotating the session in the UI or an
    # annotation queue). Deliberately a deterministic aggregate rather than a
    # judge: it is free, it cannot drift, and it is honest about what it measures.
    grounded_fraction = grounded_turns / len(CONVERSATION)
    record_score(lf, session_id=args.session_id,
                 name="session-grounded-turns", value=round(grounded_fraction, 3),
                 data_type="NUMERIC",
                 comment=f"{grounded_turns}/{len(CONVERSATION)} turns recommended only "
                         f"listings that exist and were retrieved (this turn or earlier).")

    flush_langfuse(lf)
    print(f"\n✓ Done. {len(CONVERSATION)} turns in one session.")
    print(f"  session-grounded-turns = {grounded_fraction:.0%} "
          f"({grounded_turns}/{len(CONVERSATION)} turns)")
    print(f"  Final turn tagged '{CONVERSATION_END_TAG}' + '{SNAPSHOT_NAME}' observation emitted.")
    print(f"  View: Langfuse UI > Sessions > {args.session_id}")


if __name__ == "__main__":
    main()
