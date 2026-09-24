"""
Experiment: wrap the FULL loop invocation as the task, with caps + tool list
PINNED identically across arms so any delta is attributable to the one varied
component (the system-prompt version, or the model).

Grade by OUTCOME (termination_reason, cost, turns) — never step-matching. The
same goal legitimately yields different valid trajectories, so step-matching
would score a better, shorter path as a failure (see seed_dataset.py).

    python scripts/run_experiment.py                       # v1-naive vs v2-disciplined
    python scripts/run_experiment.py --prompt-versions v2-disciplined --model claude-haiku-4-5
    python scripts/run_experiment.py --sample 2 --ci        # gate -> exit 1 on failure

Cost note: each item runs the real agent loop (Anthropic + live ClickHouse).
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

DATASET_NAME = "query-tuner/goals"
# Identical across ALL arms — isolates the varied component.
PINNED = budget.Caps(max_turns=12, max_budget_usd=1.00, watchdog_s=600)


def _new_run_id() -> str:
    return f"exp-{uuid.uuid4().hex[:8]}"


def make_task(prompt_version: str, model: str):
    def task(*, item, **kwargs):
        inp = item.input if isinstance(item.input, dict) else {}
        meta = getattr(item, "metadata", None) or {}
        goal = {"id": meta.get("query_id", "exp"), "sql": inp.get("sql", ""),
                "target_ms": inp.get("target_ms", 800),
                "expected_turn_band": meta.get("expected_turn_band", [2, 12]),
                "schema_hint": queries.SCHEMA_HINT}
        import os
        os.environ["ANTHROPIC_MODEL"] = model   # pinned per arm; agent reads this
        r = agent_loop.run(goal, caps=PINNED, run_id=_new_run_id(),
                           session_id=lf.new_session_id(),
                           prompt_version=prompt_version, hitl_mode="auto-deny")
        return {"termination_reason": r.termination_reason,
                "verified_speedup": r.verified_speedup, "turns_used": r.turns_used,
                "cost_usd": r.cost_usd, "final_sql": r.final_sql}
    return task


def main() -> int:
    ap = argparse.ArgumentParser(description="Prompt/model A-B for the slow-query tuner")
    ap.add_argument("--prompt-versions", nargs="+", default=["v1-naive", "v2-disciplined"],
                    help="System prompt versions to compare (arms)")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="Pinned model for all arms")
    ap.add_argument("--sample", type=int, default=None, help="Run only N dataset items")
    ap.add_argument("--label", default=None, help="Suffix appended to run names")
    ap.add_argument("--max-concurrency", type=int, default=1)
    ap.add_argument("--ci", action="store_true", help="Exit 1 if a gate fails")
    args = ap.parse_args()

    if not lf.is_langfuse_enabled():
        print("Langfuse keys not set — experiments require Langfuse. Aborting.")
        return 1
    client = lf.get_client()
    try:
        from langfuse import Evaluation
    except Exception as e:  # noqa: BLE001
        print(f"Langfuse Evaluation import failed: {e}")
        return 1

    try:
        dataset = client.get_dataset(DATASET_NAME)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\nRun scripts/seed_dataset.py first.")
        return 1

    def task_completed(*, output, expected_output, **kwargs):
        ok = output["termination_reason"] == "self_completed"
        return Evaluation(name="task_completed", value=1.0 if ok else 0.0,
                          comment=f"reason={output['termination_reason']}, "
                                  f"speedup={output['verified_speedup']}x")

    def run_aggregates(*, item_results, **kwargs):
        outs = [r.output for r in item_results if getattr(r, "output", None)]
        n = max(len(outs), 1)
        return [
            Evaluation(name="pass_rate",
                       value=sum(o["termination_reason"] == "self_completed" for o in outs) / n),
            Evaluation(name="avg_cost_usd", value=sum(o["cost_usd"] for o in outs) / n),
            Evaluation(name="avg_turns", value=sum(o["turns_used"] for o in outs) / n),
            Evaluation(name="cap_hit_rate",
                       value=sum(o["termination_reason"].startswith("error_") for o in outs) / n),
        ]

    gate_failed = False
    for v in args.prompt_versions:
        run_name = f"query-tuner prompt {v}" + (f" [{args.label}]" if args.label else "")
        print(f"\n=== arm: prompt={v}  model={args.model}  caps={PINNED.as_dict()} ===")
        result = dataset.run_experiment(
            name=run_name,
            task=make_task(v, args.model),
            evaluators=[task_completed],
            run_evaluators=[run_aggregates],
            max_concurrency=args.max_concurrency,
            metadata={"varied_component": "system_prompt", "prompt_version": v,
                      "model": args.model,
                      "pinned": {"caps": PINNED.as_dict(), "tools": __import__("tools").NAMES}},
        )
        aggs = {e.name: e.value for e in getattr(result, "run_evaluations", []) if e.value is not None}
        print("  " + "  ".join(f"{k}={v2:.3f}" for k, v2 in aggs.items()))
        if args.ci:
            if aggs.get("pass_rate", 0) < 0.8 or aggs.get("cap_hit_rate", 1) != 0:
                print(f"  GATE FAILED for arm '{v}': pass_rate>=0.8 and cap_hit_rate==0 required.")
                gate_failed = True

    client.flush()
    print("\n✓ View: Langfuse UI → Datasets → query-tuner/goals → Runs")
    return 1 if (args.ci and gate_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
