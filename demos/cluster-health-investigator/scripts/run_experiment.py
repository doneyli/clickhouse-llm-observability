#!/usr/bin/env python3
"""
Planner-prompt A/B experiment — vary ONLY the orchestrator's planning prompt.

The worker prompt, synthesizer prompt, models, and analysis catalog are held
FIXED; only `planner_label` varies (`production` vs `candidate-scoped-
decomposition`). Run-level evaluators track FAN-OUT alongside QUALITY, so a
"smarter" planner that triples cost is visible in one comparison table:

    candidate → coverage 4.6 vs 4.1, avg_worker_count 3.2 vs 4.8
    (better AND cheaper — or the tradeoff exposed)

Usage:
    python scripts/run_experiment.py                          # both labels
    python scripts/run_experiment.py --label production
    python scripts/run_experiment.py --sample 3
    python scripts/run_experiment.py --ci                     # exit 1 on gate failure
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATASET_NAME = "cluster-health/plan-quality"
LABELS = ["production", "candidate-scoped-decomposition"]
FANOUT_GATE = float(os.getenv("FANOUT_THRESHOLD", "6"))


def _criteria_pass_rate(plan: list, criteria: list) -> float:
    """Heuristic: fraction of plan-quality criteria the plan satisfies."""
    if not criteria:
        return 1.0
    types = [t.get("analysis_type", "") for t in plan]
    n_ok = 0
    for c in criteria:
        cl = c.lower()
        ok = True
        if "must not exceed" in cl:
            m = re.search(r"(\d+)\s*task", cl)
            if m:
                ok = len(types) <= int(m.group(1))
        elif "share an analysis_type" in cl or "distinct" in cl:
            ok = len(types) == len(set(types))
        elif "at least" in cl and "distinct" in cl:
            m = re.search(r"at least (\d+)", cl)
            ok = len(set(types)) >= (int(m.group(1)) if m else 1)
        elif "at least one of" in cl:
            opts = re.findall(r"[a-z_]+", cl.split("at least one of", 1)[1])
            ok = any(o in types for o in opts)
        elif "must include" in cl:
            wanted = [w for w in re.findall(r"[a-z_]+", cl) if w in set(_CATALOG_KEYS)]
            ok = all(w in types for w in wanted) if wanted else True
        n_ok += 1 if ok else 0
    return n_ok / len(criteria)


try:
    from analysis_catalog import CATALOG_KEYS as _CATALOG_KEYS
except Exception:
    _CATALOG_KEYS = frozenset()


def _build_evaluators():
    from langfuse import Evaluation

    def per_item_evals(*, input, output, expected_output=None, metadata=None, **kw):
        out = output if isinstance(output, dict) else {}
        plan = out.get("plan", [])
        crit = (expected_output or {}).get("criteria", []) if isinstance(expected_output, dict) else []
        return [
            Evaluation(name="worker_count", value=float(out.get("workers_spawned", 0))),
            Evaluation(name="plan_matches_expected", value=_criteria_pass_rate(plan, crit)),
        ]

    def synthesis_quality(*, input, output, **kw):
        """LLM judge on the diagnosis (0..1). Falls back to None if judge fails."""
        out = output if isinstance(output, dict) else {}
        diagnosis = out.get("diagnosis", "")
        if not diagnosis:
            return Evaluation(name="synthesis_quality", value=0.0, comment="empty diagnosis")
        try:
            from langchain_anthropic import ChatAnthropic
            judge = ChatAnthropic(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                                  temperature=0, max_tokens=200)
            symptom = input.get("symptom", "") if isinstance(input, dict) else str(input)
            resp = judge.invoke(
                "Score this ClickHouse diagnosis 0.0-1.0 for coverage + evidence citations "
                "([worker:<type>]) + actionable next steps.\n"
                f"SYMPTOM: {symptom}\nDIAGNOSIS: {diagnosis[:1500]}\n"
                'Return ONLY JSON: {"score": <0..1>, "reason": "<one sentence>"}')
            m = re.search(r"\{.*\}", getattr(resp, "content", str(resp)), re.DOTALL)
            data = json.loads(m.group()) if m else {"score": 0.5, "reason": "no json"}
            return Evaluation(name="synthesis_quality", value=float(data["score"]),
                              comment=str(data.get("reason", ""))[:200])
        except Exception as e:
            return Evaluation(name="synthesis_quality", value=None, comment=f"judge error: {e}")

    def avg_worker_count(*, item_results, **kw):
        vals = [ev.value for r in item_results for ev in r.evaluations
                if ev.name == "worker_count" and ev.value is not None]
        return Evaluation(name="avg_worker_count",
                          value=(sum(vals) / len(vals)) if vals else None,
                          comment="cost / fan-out proxy across the dataset")

    def fanout_gate(*, item_results, **kw):
        vals = [ev.value for r in item_results for ev in r.evaluations
                if ev.name == "worker_count" and ev.value is not None]
        avg = (sum(vals) / len(vals)) if vals else 0.0
        passed = avg <= FANOUT_GATE
        return Evaluation(name="fanout_gate", value=1.0 if passed else 0.0,
                          comment=f"avg_worker_count {avg:.2f} {'<=' if passed else '>'} {FANOUT_GATE}")

    return [per_item_evals, synthesis_quality], [avg_worker_count, fanout_gate]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None, help="Run a single planner label (default: both)")
    ap.add_argument("--sample", type=int, default=None, help="Run on N random items")
    ap.add_argument("--ci", action="store_true", help="Exit 1 if the fan-out gate fails")
    args = ap.parse_args()

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY required.")
        return 1

    from langfuse import get_client
    from graph import Investigator

    lf = get_client()
    try:
        dataset = lf.get_dataset(DATASET_NAME)
    except Exception as e:
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\nRun scripts/seed_datasets.py first.")
        return 1

    item_evals, run_evals = _build_evaluators()
    labels = [args.label] if args.label else LABELS
    gate_failed = False

    for label in labels:
        inv = Investigator(planner_label=label)

        def task(*, item, **kw):
            symptom = item.input.get("symptom") if isinstance(item.input, dict) else str(item.input)
            return inv.run(symptom)

        print(f"\n=== planner: {label} ===")
        result = dataset.run_experiment(
            name=DATASET_NAME,
            run_name=f"planner-{label}",
            description=f"Cluster-health planner A/B (label={label}); only the planner prompt varies.",
            task=task,
            evaluators=item_evals,
            run_evaluators=run_evals,
            max_concurrency=int(os.getenv("EXPERIMENT_CONCURRENCY", "2")),
            metadata={"varied_component": "orchestrator_planner", "planner_label": label},
        )
        try:
            print(result.format())
        except Exception:
            for ev in getattr(result, "run_evaluations", []):
                print(f"  {ev.name}: {ev.value}  {ev.comment or ''}")
        for ev in getattr(result, "run_evaluations", []):
            if ev.name == "fanout_gate" and ev.value == 0.0:
                gate_failed = True

    lf.flush()
    print("\n✓ View: Langfuse UI > Datasets > cluster-health/plan-quality > Runs")
    if args.ci and gate_failed:
        print("Fan-out gate FAILED (avg worker count over threshold).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
