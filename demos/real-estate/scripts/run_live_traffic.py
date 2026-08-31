"""
Generate realistic 'production' traffic for the Traces view.

Produces a mix of:
  - a multi-turn SESSION (one buyer refining their search) -> Sessions view
  - single-turn queries from different users (buy/rent, EN/ES)
  - a few intentionally-degraded answers (fault injection) so the code + LLM
    scores VISIBLY vary — an all-green board doesn't prove evals catch problems.

Every trace ends up with three layers of scores:
  - observation-level CODE scores  (pushed by the agent onto the synthesis obs)
  - MANAGED LLM-as-a-Judge scores  (Helpfulness/Relevance) — run automatically
    by the Langfuse worker on traces tagged 'real-estate'
    (see scripts/seed_managed_evaluators.sh)
  - custom SDK judge scores        (groundedness, tone) pushed here to COMPLEMENT
    the managed set without duplicating names; skip with --no-judge

Run:
    ./.venv/bin/python scripts/run_live_traffic.py
    ./.venv/bin/python scripts/run_live_traffic.py --no-judge   # faster
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, record_score, flush_langfuse, verify_project
from agent.concierge import run_turn
from agent.scoring import judge_groundedness, judge_tone

# Custom judges that COMPLEMENT the managed evaluators (which cover
# Helpfulness/Relevance). Kept distinct to avoid score-name clashes.
COMPLEMENTARY_JUDGES = [judge_groundedness, judge_tone]

# (query, session_id, user_id, extra_tags, fault)
SESSION = "sess-madrid-buyer-001"
TRAFFIC = [
    # --- a genuine multi-turn conversation (history is threaded between turns
    #     that share a session_id, so follow-ups resolve against prior turns) ---
    ("I'm looking to buy a 2-bedroom flat in Madrid, my budget is around €450,000.",
     SESSION, "maria.g", ["live", "session-demo"], None),
    ("Actually, let's keep it under €400,000 and make sure it's close to a metro.",
     SESSION, "maria.g", ["live", "session-demo"], None),
    ("Great — what would the estimated monthly mortgage be on that one?",
     SESSION, "maria.g", ["live", "session-demo"], None),

    # --- single-turn, clean ---
    ("We want to buy a house near the beach in Málaga with a pool and sea views, budget about €500,000.",
     None, "james.p", ["live"], None),
    ("What furnished two-bedroom apartments can I rent in the Ruzafa area of Valencia?",
     None, "sofia.r", ["live"], None),
    ("I'd like to buy a 3-bed apartment in central Bilbao near the Guggenheim; what's the mortgage on a €410,000 place?",
     None, "koldo.e", ["live"], None),
    ("Busca un piso de 2 habitaciones en Gràcia, Barcelona, por menos de 450.000 euros. ¿Cómo es el barrio?",
     None, "nuria.m", ["live"], None),

    # --- single-turn, clean, across Europe (same agent, wider catalog) ---
    ("I want to buy a 2-bedroom apartment in central Berlin's Mitte for under €600,000; what's the mortgage?",
     None, "jonas.b", ["live"], None),
    ("Show me a renovated 2-bed to buy in Lisbon's Alfama for around €450,000. How's the neighborhood?",
     None, "ana.f", ["live"], None),
    ("Find a furnished 2-bedroom apartment to rent near Canal Saint-Martin in Paris, budget €2,500 a month.",
     None, "claire.d", ["live"], None),

    # --- fault-injected so scores VARY (demo only) ---
    ("Find me a 2-bedroom flat to buy in Valencia under €250,000.",
     None, "tom.h", ["live", "fault-demo"], "over_budget"),
    ("Show me furnished 3-bedroom apartments to rent in Barcelona under €2,000 a month.",
     None, "lena.k", ["live", "fault-demo"], "hallucinate"),
    ("Quiero alquilar un piso de 2 habitaciones en Sevilla, cerca del centro.",
     None, "pablo.d", ["live", "fault-demo"], "wrong_language"),
    # Tool-use failures: the trace itself lacks a tool:search_listings span,
    # so used-search-tool = 0 and the trace tree tell the same story.
    ("What 2-bedroom apartments are available to buy in Madrid right now, under €400,000?",
     None, "nadia.s", ["live", "fault-demo"], "no_search"),
    ("Find me a family apartment to buy in Valencia near good schools, around €300,000.",
     None, "victor.m", ["live", "fault-demo"], "wrong_tool"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-judge", action="store_true", help="Skip LLM-as-a-Judge trace scores (faster)")
    args = ap.parse_args()

    verify_project()
    lf = get_langfuse()

    print(f"\nGenerating {len(TRAFFIC)} live traces "
          f"({'no ' if args.no_judge else ''}LLM-judge scores)...\n")

    histories: dict = {}   # session_id -> accumulated conversational turns
    turns: dict = {}       # session_id -> 0-based turn index
    # A conversation-level judge can only run if the APP declares the end of the
    # conversation — Langfuse never knows when a session has concluded. Find the
    # last row of each session up front so that turn can be flagged, which tags it
    # `conversation_end` and emits the `conversation-snapshot` observation the
    # managed `stated-constraint-respected` rule fires on (see
    # scripts/seed_managed_evaluators.sh).
    last_row_of_session: dict = {}
    for row_index, row in enumerate(TRAFFIC):
        if row[1]:
            last_row_of_session[row[1]] = row_index
    for i, (query, sess, user, tags, fault) in enumerate(TRAFFIC, 1):
        tag = f" [fault:{fault}]" if fault else ""
        sess_tag = " [session]" if sess else ""
        print(f"[{i:2}/{len(TRAFFIC)}]{sess_tag}{tag} {query[:64]}")
        history = histories.get(sess, []) if sess else []
        # Each turn is its own trace; a session's turns are grouped by session_id
        # (visible together in the Langfuse Sessions view).
        turn_index = turns.get(sess, 0)
        # `enumerate(..., 1)` makes i 1-based; last_row_of_session is 0-based.
        is_final_turn = bool(sess) and last_row_of_session.get(sess) == i - 1
        result = run_turn(query, session_id=sess, user_id=user, extra_tags=tags,
                          fault=fault, history=history, turn_index=turn_index,
                          is_final_turn=is_final_turn)
        if sess:
            histories[sess] = history + [{"role": "user", "content": query},
                                         {"role": "assistant", "content": result["answer"]}]
            turns[sess] = turn_index + 1

        if not args.no_judge:
            # Attach custom judges to this turn's synthesis observation, so each
            # turn of a conversation gets its own groundedness/tone (not the trace).
            for judge in COMPLEMENTARY_JUDGES:
                s = judge(result)
                record_score(lf, trace_id=result["trace_id"],
                             observation_id=result.get("final_generation_id"),
                             name=s.name, value=s.value, data_type=s.data_type, comment=s.comment)
        shown = ", ".join(result["listings_shown"]) or "(none)"
        print(f"        -> tools={result['tools_called']} shown={shown} trace={result['trace_id'][:12]}")

    flush_langfuse(lf)
    print("\n✓ Live traffic complete. Managed judges (Helpfulness/Relevance) "
          "score these automatically within ~1 min.")
    print("  Conversation-level: the final turn of each session carries a "
          "'conversation-snapshot' observation, which the")
    print("  'stated-constraint-respected' judge scores once per conversation "
          "(one score for the whole thread, not per turn).")
    print("  View: Langfuse UI > Tracing (filter tag 'real-estate'); Sessions > sess-madrid-buyer-001")


if __name__ == "__main__":
    main()
