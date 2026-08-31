"""
Scripted demo beats. By default this PRINTS the ordered commands for each act
(safe pre-flight); with --run it executes the non-interactive beats (short, long,
runaway) back-to-back so ingestion is warm before you present.

    python scripts/run_live_demo.py           # print the beat plan
    python scripts/run_live_demo.py --run      # run short + long + runaway now

Pause/resume (Act 3) and the HITL approve (Act 5) are inherently interactive and
are only printed — run them by hand from DEMO_SCRIPT.md.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_loop
import budget
import langfuse_config as lf
import queries

BEATS = [
    ("Act 1 — short run (self_completed in a few turns)",
     "python main.py --query q1"),
    ("Act 2 — long run (env error + plateau; same trace name, deeper graph)",
     "python main.py --query q2"),
    ("Act 3 — pause/resume (Ctrl-C ~turn 4, then --resume <run_id>)",
     "python main.py --query q2      # Ctrl-C mid-loop, then: python main.py --resume <run_id>"),
    ("Act 4 — the runaway (trips the cost cap → Monitor)",
     "python main.py --runaway"),
    ("Act 5 — blocked ≠ failed (HITL DDL gate)",
     "python main.py --query q3      # approve the proposed DDL when prompted"),
    ("Act 6 — prove the prompt (pinned caps/tools)",
     "python scripts/run_experiment.py --sample 2"),
]


def _run(goal, **kw):
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    return agent_loop.run(goal, run_id=run_id, session_id=lf.new_session_id(), **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="Execute the non-interactive beats now")
    args = ap.parse_args()

    print("Slow Query Tuner — demo beat plan:\n")
    for title, cmd in BEATS:
        print(f"  {title}\n    $ {cmd}\n")

    if not args.run:
        print("(dry run — pass --run to execute the short/long/runaway beats)")
        return 0

    def goal_of(qid):
        g = queries.get_goal(qid).as_dict()
        g["schema_hint"] = queries.SCHEMA_HINT
        return g

    print("\n>>> Act 1: short run (q1)")
    _run(goal_of("q1"), hitl_mode="auto-deny")
    print("\n>>> Act 2: long run (q2)")
    _run(goal_of("q2"), hitl_mode="auto-deny")
    print("\n>>> Act 4: runaway")
    rg = goal_of("q3")
    rg["target_ms"] = queries.RUNAWAY_TARGET_MS
    _run(rg, caps=budget.Caps(max_turns=25, max_budget_usd=2.50, watchdog_s=600),
         prompt_version="v1-naive", hitl_mode="auto-deny", runaway=True)
    print("\n✓ Warm beats done — open Langfuse (Agent Graph, Sessions, Monitors).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
