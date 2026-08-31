"""
Slow Query Tuner — CLI.

Examples:
    python main.py --query q1                     # short self-terminating run
    python main.py --query q2                     # long run (env error + plateau)
    python main.py --query q3                     # blocked -> propose_ddl (HITL)
    python main.py --query q3 --auto-approve-ddl   # scripted HITL beat (approve)
    python main.py --runaway                       # deliberate runaway -> cost cap
    python main.py --resume run-abcd1234           # resume a paused/killed run
    python main.py --sql "SELECT ..." --target-ms 500
    python main.py --interactive

The loop length is agent-decided; MAX_TURNS / MAX_BUDGET_USD / watchdog are
backstops only (override with --max-turns / --max-budget-usd / --watchdog-s).
"""

from __future__ import annotations

import argparse
import sys
import uuid

import agent_loop
import budget
import checkpoint
import langfuse_config as lf
import prompts
import queries


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:8]}"


def _build_goal(qid: str) -> dict:
    g = queries.get_goal(qid)
    goal = g.as_dict()
    goal["schema_hint"] = queries.SCHEMA_HINT
    return goal


def _caps_from_args(args, runaway: bool) -> budget.Caps:
    caps = budget.Caps()
    if runaway:
        # --runaway only RAISES caps (never removes them): an impossible target +
        # the naive prompt churn until the spend cap kills it.
        caps.max_turns = 25
        caps.max_budget_usd = 2.50
    if args.max_turns is not None:
        caps.max_turns = args.max_turns
    if args.max_budget_usd is not None:
        caps.max_budget_usd = args.max_budget_usd
    if args.watchdog_s is not None:
        caps.watchdog_s = args.watchdog_s
    return caps


def _hitl_mode(args) -> str:
    if args.auto_approve_ddl:
        return "auto-approve"
    if args.auto_deny_ddl:
        return "auto-deny"
    return "prompt"


def main() -> int:
    ap = argparse.ArgumentParser(description="Autonomous slow-query tuning agent")
    ap.add_argument("--query", choices=sorted(queries.CATALOG), help="Bad-query catalog id")
    ap.add_argument("--sql", help="Ad-hoc SQL to tune (with --target-ms)")
    ap.add_argument("--target-ms", type=int, help="Target latency for --sql")
    ap.add_argument("--runaway", action="store_true",
                    help="Deliberate runaway (naive prompt + impossible target) -> trips the cost cap")
    ap.add_argument("--resume", metavar="RUN_ID", help="Resume a paused/killed run by run_id")
    ap.add_argument("--interactive", action="store_true", help="Prompt for SQL + target")
    ap.add_argument("--prompt-version", default=None,
                    help="System prompt version/label (v1-naive | v2-disciplined | production)")
    ap.add_argument("--auto-approve-ddl", action="store_true", help="Scripted demo: approve DDL")
    ap.add_argument("--auto-deny-ddl", action="store_true", help="Batch/CI: deny DDL")
    ap.add_argument("--max-turns", type=int, default=None, help="Backstop override")
    ap.add_argument("--max-budget-usd", type=float, default=None, help="Backstop override")
    ap.add_argument("--watchdog-s", type=float, default=None, help="Backstop override")
    args = ap.parse_args()

    if not lf.is_langfuse_enabled():
        print("NOTE: Langfuse keys not set — the loop runs, all instrumentation no-ops.")

    # ---- resume path -----------------------------------------------------
    if args.resume:
        if not checkpoint.exists(args.resume):
            print(f"ERROR: no checkpoint for run_id '{args.resume}' in {checkpoint.checkpoint_dir()}",
                  file=sys.stderr)
            return 1
        state = checkpoint.load(args.resume)
        caps = _caps_from_args(args, runaway=False)
        result = agent_loop.run(state.goal, caps=caps, run_id=args.resume,
                                session_id=state.session_id, resume_state=state,
                                prompt_version=args.prompt_version or prompts.PRODUCTION_LABEL,
                                hitl_mode=_hitl_mode(args))
        return _report(result)

    # ---- resolve goal ----------------------------------------------------
    runaway = args.runaway
    if runaway:
        goal = _build_goal("q3")
        goal["target_ms"] = queries.RUNAWAY_TARGET_MS
        prompt_version = "v1-naive"
        hitl_mode = "auto-deny"
    elif args.interactive:
        sql = input("SQL to tune:\n> ").strip()
        target = int(input("Target latency (ms): ").strip() or "800")
        goal = {"id": "adhoc", "sql": sql, "target_ms": target,
                "expected_turn_band": [2, 15], "schema_hint": queries.SCHEMA_HINT}
        prompt_version = args.prompt_version or prompts.PRODUCTION_LABEL
        hitl_mode = _hitl_mode(args)
    elif args.sql:
        goal = {"id": "adhoc", "sql": args.sql,
                "target_ms": args.target_ms or 800,
                "expected_turn_band": [2, 15], "schema_hint": queries.SCHEMA_HINT}
        prompt_version = args.prompt_version or prompts.PRODUCTION_LABEL
        hitl_mode = _hitl_mode(args)
    elif args.query:
        goal = _build_goal(args.query)
        prompt_version = args.prompt_version or prompts.PRODUCTION_LABEL
        hitl_mode = _hitl_mode(args)
    else:
        ap.error("provide --query, --sql, --runaway, --resume, or --interactive")
        return 2

    caps = _caps_from_args(args, runaway=runaway)
    run_id = new_run_id()
    session_id = lf.new_session_id()
    print(f"run_id={run_id}  session_id={session_id}  caps={caps.as_dict()}  "
          f"prompt={prompt_version}{'  [RUNAWAY]' if runaway else ''}")

    result = agent_loop.run(goal, caps=caps, run_id=run_id, session_id=session_id,
                            prompt_version=prompt_version, hitl_mode=hitl_mode,
                            runaway=runaway)
    return _report(result)


def _report(result: "agent_loop.RunResult") -> int:
    print("\n" + "-" * 72)
    print(f"termination_reason : {result.termination_reason}")
    print(f"turns_used         : {result.turns_used}")
    print(f"verified_speedup   : {result.verified_speedup}x")
    print(f"run_cost_usd       : ${result.cost_usd}")
    print(f"summary            : {result.summary}")
    if result.final_sql:
        print(f"final_sql          :\n{result.final_sql}")
    print("-" * 72)
    if result.termination_reason == "killed":
        print(f"Resume with: python main.py --resume {result.run_id}")
        return 130
    return 0


if __name__ == "__main__":
    # Flush on the way out, always — including the --resume/killed paths that exit
    # non-zero. This is a short-lived `docker compose run --rm` process and the SDK
    # exports spans from a background batch processor, so without this the
    # interpreter can exit with spans still queued and silently drop the trace.
    try:
        sys.exit(main())
    finally:
        lf.flush()
