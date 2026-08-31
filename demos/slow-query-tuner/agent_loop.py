"""
THE PATTERN — an open-ended plan-act-observe controller.

There is no predefined path. The agent (Anthropic tool-use API) emits ONE tool
call per turn; no code decides the sequence. It gains ground truth from the live
ClickHouse tuning lab on every action, and it decides FOR ITSELF when the target
is met by calling `finish` — a claim the controller INDEPENDENTLY RE-EXECUTES
before accepting. Turn/budget/watchdog caps and the kill switch are backstops
only; a run ending on one of them is recorded as a failure mode.

Termination reasons (trace metadata):
  self_completed | self_gave_up | self_completed_implicit
  blocked_hitl_denied
  killed
  error_max_turns | error_max_budget_usd | error_watchdog

Instrumentation (per turn, low-cardinality names; turn # in metadata):
  GENERATION plan-next-action -> TOOL <action> -> EVALUATOR assess-progress
Five trace scores at the end + span scores (semantics_preserved, improvement_delta).
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional

import budget
import ch_env
import checkpoint
import langfuse_config as lf
import prompts
import tools
from checkpoint import LoopState

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
COMPACT_AFTER = int(os.getenv("TUNER_COMPACT_AFTER", "6"))
TRACE_NAME = "tune-clickhouse-query"

_anthropic = None


def _client():
    global _anthropic
    if _anthropic is None:
        import anthropic
        _anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


@dataclass
class Verdict:
    ok: bool
    measured_speedup: float = 1.0
    measured_ms: Optional[float] = None
    reason: str = ""


@dataclass
class RunResult:
    termination_reason: str
    verified_speedup: float
    turns_used: int
    cost_usd: float
    final_sql: Optional[str]
    summary: str
    run_id: str
    session_id: str


# ------------------------------------------------------------------- HITL gate
def hitl_approve(action: "tools.Action", mode: str) -> bool:
    """Human-approval gate for propose_ddl. mode: prompt | auto-approve | auto-deny."""
    if mode == "auto-approve":
        return True
    if mode == "auto-deny":
        return False
    ddl = action.args.get("ddl", "")
    rationale = action.args.get("rationale", "")
    print("\n" + "=" * 72)
    print("HUMAN APPROVAL REQUIRED — the agent proposes a schema change:")
    print(f"  Rationale: {rationale}")
    print(f"  DDL:\n    {ddl}")
    print("=" * 72)
    try:
        answer = input("Approve and apply this DDL? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


# --------------------------------------------------- finish-claim verification
def verify_finish_claim(action: "tools.Action", env: "ch_env.TuningLabEnv",
                        state: LoopState, target_ms: int) -> Verdict:
    """Independently re-execute the agent's final SQL + equivalence probe. A
    success claim is accepted only if the result set matches the baseline AND the
    median measured latency meets the target. gave_up is always an acceptable
    (honest) termination."""
    status = action.args.get("status", "success")
    final_sql = action.args.get("final_sql", "")

    ok, reason = ch_env.sql_allowed(final_sql)
    if not ok and status == "success":
        return Verdict(ok=False, reason=f"final_sql {reason}")

    # Measure the final SQL for real (median of 3) — the agent can't fake this.
    samples: List[float] = []
    last: Optional[ch_env.Obs] = None
    for _ in range(3):
        last = env.run_query(final_sql)
        if not last.ok:
            if status == "gave_up":
                break
            return Verdict(ok=False, reason=f"final_sql failed: {last.error}")
        samples.append(last.elapsed_ms or 0.0)

    if status == "gave_up":
        # Conceding is a valid, honest termination. Record best speedup so far.
        return Verdict(ok=True, measured_speedup=state.best_speedup,
                       measured_ms=(median(samples) if samples else None),
                       reason="agent conceded (structural blocker)")

    med = median(samples) if samples else float("inf")
    equivalent = (last is not None and last.signature == state.baseline_signature)
    if not equivalent:
        return Verdict(ok=False, reason="result set differs from the original query")
    if state.baseline_ms:
        speedup = state.baseline_ms / max(med, 0.001)
    else:
        speedup = 1.0
    if med > target_ms:
        return Verdict(ok=False, measured_speedup=speedup, measured_ms=med,
                       reason=f"measured {med:.0f} ms > target {target_ms} ms (not met)")
    return Verdict(ok=True, measured_speedup=speedup, measured_ms=med,
                   reason=f"verified {med:.0f} ms <= target {target_ms} ms")


# ------------------------------------------------------------------ summaries
def _summary(turn: int, action: "tools.Action", obs: "ch_env.Obs") -> str:
    if obs.is_error:
        return f"turn {turn}: {action.span_name} -> ERROR {obs.error[:80]}"
    if action.name == "run_query":
        eq = "equiv ok" if obs.equivalent else "NOT equivalent"
        return f"turn {turn}: run_query -> {obs.elapsed_ms:.0f} ms, {eq}"
    if action.name == "explain_query":
        return f"turn {turn}: explain_query -> plan inspected"
    if action.name == "get_schema":
        return f"turn {turn}: get_schema -> DDL inspected"
    if action.name == "check_equivalence":
        return f"turn {turn}: check_equivalence -> {'equivalent' if obs.equivalent else 'DIFFERENT'}"
    if action.name == "propose_ddl":
        return f"turn {turn}: propose_ddl approved -> {obs.text}"
    return f"turn {turn}: {action.span_name}"


def _root_update(root, **kwargs):
    if root is not None:
        try:
            root.update(**kwargs)
        except Exception:
            pass


# ----------------------------------------------------------------- finalize
def finalize(root, state: LoopState, caps: budget.Caps, reason: str,
             goal: Dict[str, Any], prompt_version: str,
             verdict: Optional[Verdict] = None,
             finish_summary: Optional[str] = None) -> RunResult:
    if verdict is not None and verdict.ok:
        state.best_speedup = max(state.best_speedup, verdict.measured_speedup)

    is_error = reason.startswith("error_") or reason in ("killed", "blocked_hitl_denied")
    turns_used = max(0, state.turn - 1) if is_error else state.turn
    final_sql = state.best_sql

    band = goal.get("expected_turn_band", [2, 12])
    expected_hi = band[1] if band else 12
    trajectory_efficiency = round(min(1.0, expected_hi / max(turns_used, 1)), 3)

    # original_sql + summary live on the trace OUTPUT (flat) so the managed
    # goal-drift judge (seed-query-tuner-evaluators.sh) can map to them directly.
    summary_text = finish_summary or (verdict.reason if verdict else reason)
    output = {"original_sql": goal.get("sql"), "final_sql": final_sql,
              "verified_speedup": round(state.best_speedup, 2),
              "termination_reason": reason, "summary": summary_text}
    metadata = {"turns_used": turns_used, "cost_usd": round(state.cost_usd, 4),
                "caps": caps.as_dict(), "prompt_version": prompt_version,
                "run_id": state.run_id, "termination_reason": reason,
                "plateau_turns": state.plateau}
    _root_update(root, output=output, metadata=metadata)
    lf.update_current_trace(output=output, metadata=metadata)

    # Five first-class trace scores (Scores API).
    lf.score_current_trace("turns_used", turns_used, data_type="NUMERIC",
                           comment=f"reason={reason}")
    lf.score_current_trace("run_cost_usd", round(state.cost_usd, 4), data_type="NUMERIC")
    lf.score_current_trace("task_completed", int(reason == "self_completed"),
                           data_type="BOOLEAN", comment=f"reason={reason}")
    lf.score_current_trace("verified_speedup", round(state.best_speedup, 2), data_type="NUMERIC")
    lf.score_current_trace("trajectory_efficiency", trajectory_efficiency, data_type="NUMERIC",
                           comment=f"{turns_used} turns vs expected {band}; reason={reason}; "
                                   f"plateau_turns={state.plateau}")

    checkpoint.save(state.run_id, state)
    lf.flush()

    return RunResult(termination_reason=reason, verified_speedup=round(state.best_speedup, 2),
                     turns_used=turns_used, cost_usd=round(state.cost_usd, 4),
                     final_sql=final_sql, summary=(verdict.reason if verdict else reason),
                     run_id=state.run_id, session_id=state.session_id)


# --------------------------------------------------------------------- run
def run(goal: Dict[str, Any], *, caps: Optional[budget.Caps] = None,
        run_id: str, session_id: str, resume_state: Optional[LoopState] = None,
        prompt_version: str = prompts.PRODUCTION_LABEL,
        hitl_mode: str = "prompt", runaway: bool = False,
        env: Optional["ch_env.TuningLabEnv"] = None) -> RunResult:
    """Execute (or resume) one tuning run as a single Langfuse trace."""
    caps = caps or budget.Caps()
    env = env or ch_env.TuningLabEnv()
    # Re-read at call time (not just the import-time MODEL) so a per-run/per-arm
    # ANTHROPIC_MODEL override (e.g. run_experiment.py's model pin) actually
    # reaches the API call and the cost table.
    model = os.getenv("ANTHROPIC_MODEL", MODEL)
    target_ms = int(goal["target_ms"])
    tool_schemas = tools.RUNAWAY_SCHEMAS if runaway else tools.SCHEMAS
    compact_after = 0 if runaway else COMPACT_AFTER   # runaway lets context grow (realistic)
    tags = lf.DEFAULT_TAGS + (["fault:runaway"] if runaway else [])

    system_text, system_prompt_obj = prompts.get_system_prompt(prompt_version)

    # --- state: fresh or resumed ------------------------------------------
    if resume_state is not None:
        state = resume_state
        state.t0 = __import__("time").monotonic()
        state.sigint = False
        print(f"Resumed run {run_id} at turn {state.turn} "
              f"(cost so far ${state.cost_usd:.4f}, session {session_id})")
    else:
        # Measure the baseline for real — ground truth for speedup + equivalence.
        base = env.run_query(goal["sql"])
        baseline_ms = base.elapsed_ms if base.ok else None
        baseline_sig = base.signature if base.ok else None
        goal_prompt, _ = prompts.get_goal_prompt(
            goal["sql"], target_ms, goal.get("schema_hint", ""), baseline_ms)
        state = LoopState.fresh(run_id=run_id, session_id=session_id, goal=goal,
                                goal_prompt=goal_prompt)
        state.baseline_ms = baseline_ms
        state.baseline_signature = baseline_sig

    # --- SIGINT -> finish current turn, checkpoint, exit as `killed` -------
    _install_sigint(state)

    with lf.trace_context(TRACE_NAME, session_id=session_id, tags=tags):
        with lf.observe(TRACE_NAME, as_type="agent", input={"goal": goal, "mode": prompt_version}) as root:
            _root_update(root, input={"goal": goal, "mode": prompt_version})
            lf.update_current_trace(input={"goal": goal, "mode": prompt_version})

            while True:
                stop = budget.check(state, caps)
                if stop:
                    return finalize(root, state, caps, stop, goal, prompt_version)

                # ---- plan (generation) ----
                with lf.observe("plan-next-action", as_type="generation",
                                input=state.compacted_messages(compact_after)) as gen:
                    try:
                        resp = _client().messages.create(
                            model=model, system=system_text, tools=tool_schemas,
                            messages=state.compacted_messages(compact_after),
                            max_tokens=2000, temperature=TEMPERATURE,
                            # The loop's contract is ONE tool call per turn
                            # (extract_tool_call only dispatches the first
                            # tool_use block and appends exactly one
                            # tool_result). Without disabling parallel tool
                            # use, the model can emit 2+ tool_use blocks in a
                            # single turn; the extra block(s) get silently
                            # dropped (never dispatched, never scored) and the
                            # dangling tool_use id corrupts the message
                            # history, crashing the NEXT call with a 400
                            # ("tool_use ids were found without tool_result").
                            tool_choice={"type": "auto", "disable_parallel_tool_use": True})
                    except Exception:
                        # Checkpoint already holds the last completed turn; surface
                        # the failure so the operator can --resume.
                        checkpoint.save(state.run_id, state)
                        raise
                    state.cost_usd += budget.cost_of(resp.usage, model)
                    if gen is not None:
                        _root_update(gen,
                                     output=_text_of(resp) or "[tool_use]",
                                     model=model,
                                     usage_details={"input_tokens": resp.usage.input_tokens,
                                                    "output_tokens": resp.usage.output_tokens},
                                     metadata={"turn": state.turn, "cost_so_far": round(state.cost_usd, 4),
                                               "best_speedup": round(state.best_speedup, 2),
                                               "plateau_turns": state.plateau})
                        if system_prompt_obj is not None:
                            _root_update(gen, prompt=system_prompt_obj)

                action = tools.extract_tool_call(resp)
                assistant_msg = tools.to_plain_assistant(resp)

                # ---- end_turn with no tool call = implicit (lower-quality) finish ----
                if action is None:
                    return finalize(root, state, caps, "self_completed_implicit",
                                    goal, prompt_version)

                # ---- finish: verify the claim against the environment ----
                if action.name == "finish":
                    verdict = verify_finish_claim(action, env, state, target_ms)
                    if verdict.ok:
                        if action.args.get("status") == "success":
                            state.best_sql = action.args.get("final_sql")
                            reason = "self_completed"
                        else:
                            reason = "self_gave_up"
                            if state.best_sql is None:
                                state.best_sql = action.args.get("final_sql")
                        return finalize(root, state, caps, reason, goal, prompt_version,
                                        verdict, finish_summary=action.args.get("summary"))
                    # False claim -> bounce back as the next observation.
                    state.append_pair(assistant_msg, action.tool_use_id,
                                      {"rejected": True, "reason": verdict.reason,
                                       "hint": "your claim did not verify — keep going or give up honestly"},
                                      f"turn {state.turn}: finish REJECTED — {verdict.reason}")
                    state.plateau += 1
                    state.turn += 1
                    checkpoint.save(state.run_id, state)
                    continue

                # ---- propose_ddl: human-approval gate ----
                if action.name == "propose_ddl":
                    if not hitl_approve(action, hitl_mode):
                        state.append_pair(assistant_msg, action.tool_use_id,
                                          {"denied": True,
                                           "hint": "human denied the DDL; try more rewrites or "
                                                   "finish with status=gave_up naming the blocker"},
                                          f"turn {state.turn}: propose_ddl DENIED by human")
                        state.turn += 1
                        checkpoint.save(state.run_id, state)
                        continue
                    # Approved -> apply via the ADMIN connection inside a tool span.
                    with lf.observe(action.span_name, as_type="tool", input=action.args) as tspan:
                        obs = env.apply_ddl(action.args.get("ddl", ""))
                        _root_update(tspan, output=obs.public, metadata={"turn": state.turn},
                                     level=("WARNING" if obs.is_error else None))
                    state.append_pair(assistant_msg, action.tool_use_id, obs.public,
                                      _summary(state.turn, action, obs))
                    state.turn += 1
                    checkpoint.save(state.run_id, state)
                    continue

                # ---- read-only environment tool ----
                with lf.observe(action.span_name, as_type="tool", input=action.args) as tspan:
                    obs = tools.dispatch(action, env, goal["sql"], state.baseline_signature)
                    _root_update(tspan, output=obs.public, metadata={"turn": state.turn},
                                 level=("WARNING" if obs.is_error else None))
                    if action.name == "run_query" and obs.candidate_checked:
                        lf.score_current_span("semantics_preserved", int(bool(obs.equivalent)),
                                              data_type="BOOLEAN",
                                              comment=("candidate result set matches baseline"
                                                       if obs.equivalent else "candidate CHANGED the result set"))

                # ---- assess progress (code evaluator, no LLM) ----
                with lf.observe("assess-progress", as_type="evaluator") as ev:
                    delta = state.record_progress(obs, candidate_sql=action.args.get("sql"))
                    _root_update(ev, output={"improvement_delta": delta,
                                             "best_speedup": round(state.best_speedup, 2),
                                             "plateau_turns": state.plateau},
                                 metadata={"turn": state.turn})
                    lf.score_current_span("improvement_delta", round(delta, 3), data_type="NUMERIC",
                                          comment=f"best_speedup={state.best_speedup:.2f}x, "
                                                  f"plateau={state.plateau}")

                state.append_pair(assistant_msg, action.tool_use_id, obs.public,
                                  _summary(state.turn, action, obs))
                state.turn += 1
                checkpoint.save(state.run_id, state)


def _text_of(resp) -> str:
    return "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or []
                   if getattr(b, "type", None) == "text").strip()


def _install_sigint(state: LoopState) -> None:
    def _handler(signum, frame):
        state.sigint = True
        print("\n[SIGINT] finishing current turn, then checkpointing…")
    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, RuntimeError):
        # Not in the main thread (e.g. under an experiment runner) — skip.
        pass
