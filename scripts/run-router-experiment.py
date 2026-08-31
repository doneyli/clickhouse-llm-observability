#!/usr/bin/env python3
"""
Run the router-accuracy experiment — vary ONLY the router (prompt label + model);
taxonomy, threshold, and handlers stay byte-identical.

Default mode is CLASSIFICATION-ONLY: the task never dispatches a handler, which
is the strongest possible variable isolation AND makes a 30-item run cost cents
on Haiku. `--e2e` dispatches to the fixed running handler services for an
end-to-end comparison (handlers must be up).

Configurations compared (each differs from the others in exactly one axis):
    prompt=baseline    model=claude-haiku-4-5    (is the few-shot prompt worth it?)
    prompt=production  model=claude-haiku-4-5
    prompt=production  model=claude-sonnet-4-6   (is a bigger router worth it?)

Per-item score:  route-match (1.0 if chosen route == expected route)
Run-level score: avg_route_accuracy (= 1 - misroute rate)

CI: --ci exits 1 if avg_route_accuracy < 0.90 (the gate the DEMO_SCRIPT ties to
"promote v2 to the production label" = Deploy).

Usage:
    python scripts/run-router-experiment.py                 # all configs, classification-only
    python scripts/run-router-experiment.py --sample 30
    python scripts/run-router-experiment.py --label production --ci
    python scripts/run-router-experiment.py --e2e            # dispatch to live handlers

Env: LANGFUSE_HOST/BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, ANTHROPIC_API_KEY.
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "demos" / "query-router"))

try:
    from langfuse import Langfuse, Evaluation
except ImportError:
    print("Error: langfuse not installed. Run: pip install 'langfuse>=3.0,<4.0'", file=sys.stderr)
    sys.exit(1)

import router  # noqa: E402  (demos/query-router/router.py)

DATASET_NAME = os.getenv("ROUTER_DATASET_NAME", "query-router-accuracy")
CI_THRESHOLD = float(os.getenv("ROUTER_CI_THRESHOLD", "0.90"))

# (prompt_label, model) — each config differs in exactly one axis.
CONFIGS = [
    ("baseline", "claude-haiku-4-5"),
    ("production", "claude-haiku-4-5"),
    ("production", "claude-sonnet-4-6"),
]


def _question(item):
    inp = item.input
    return inp.get("question") if isinstance(inp, dict) else str(inp)


def make_task(prompt_label: str, model: str, e2e: bool):
    def task(*, item, **kwargs):
        # ONLY these two vary; taxonomy, threshold, handlers untouched.
        decision = router.classify(_question(item), prompt_label=prompt_label, model=model)
        out = {"route": decision["route"], "confidence": decision["confidence"]}
        if e2e:
            from handlers import dispatch  # local import: only needed in --e2e mode
            result = dispatch(decision, _question(item), session_id="router-experiment")
            out["handled_by"] = result.get("handled_by")
            out["answer"] = result.get("answer")
        return out
    return task


def route_match(*, output, expected_output, **kwargs):
    expected = expected_output.get("route") if isinstance(expected_output, dict) else expected_output
    return Evaluation(
        name="route-match",
        value=1.0 if output.get("route") == expected else 0.0,
        # BOOLEAN to match evaluators/route-match.ts (the same-named `route-match`
        # code evaluator seed-code-evaluators.sh wires onto this dataset): a score
        # name binds to ONE data type in Langfuse, so the two must agree or one
        # write is rejected / the experiment column becomes unusable.
        data_type="BOOLEAN",
        comment=f"chose '{output.get('route')}', expected '{expected}'",
    )


def avg_route_accuracy(*, item_results, **kwargs):
    vals = [e.value for r in item_results for e in r.evaluations
            if e.name == "route-match" and e.value is not None]
    return Evaluation(name="avg_route_accuracy",
                      value=sum(vals) / len(vals) if vals else None,
                      data_type="NUMERIC")


def main():
    ap = argparse.ArgumentParser(description="Router-accuracy experiment (vary only the router)")
    ap.add_argument("--sample", type=int, default=None, help="run only the first N dataset items")
    ap.add_argument("--label", default=None,
                    help="run only one prompt label (baseline|production); default runs all configs")
    ap.add_argument("--model", default=None, help="override the model for the --label run")
    ap.add_argument("--e2e", action="store_true", help="dispatch to live handlers (end-to-end)")
    ap.add_argument("--ci", action="store_true",
                    help=f"exit 1 if any run's avg_route_accuracy < {CI_THRESHOLD}")
    args = ap.parse_args()

    host = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
    lf = Langfuse(public_key=pk, secret_key=sk, host=host)

    try:
        dataset = lf.get_dataset(DATASET_NAME)
    except Exception as e:
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\nRun scripts/seed-router-dataset.py first.")
        sys.exit(1)

    items = list(dataset.items)
    if args.sample:
        items = items[: args.sample]

    configs = CONFIGS
    if args.label:
        configs = [(args.label, args.model or os.getenv("ROUTER_MODEL", "claude-haiku-4-5"))]

    print(f"Router experiment on '{DATASET_NAME}' ({len(items)} items, "
          f"{'e2e' if args.e2e else 'classification-only'})\n")

    worst = 1.0
    for label, model in configs:
        result = lf.run_experiment(
            name=DATASET_NAME,
            run_name=f"router-prompt={label}-model={model}",
            description=f"Router variant: prompt={label} model={model} "
                        f"({'e2e dispatch' if args.e2e else 'classification-only'}).",
            data=items,
            task=make_task(label, model, args.e2e),
            evaluators=[route_match],
            run_evaluators=[avg_route_accuracy],
            metadata={"varied_component": "router", "prompt_label": label,
                      "router_model": model, "mode": "e2e" if args.e2e else "classification"},
        )
        acc = next((e.value for e in result.run_evaluations if e.name == "avg_route_accuracy"), None)
        worst = min(worst, acc if acc is not None else 1.0)
        print(f"  prompt={label:11s} model={model:20s} avg_route_accuracy="
              f"{acc if acc is not None else 'n/a'}")
        try:
            print(result.format())
        except Exception:
            pass

    lf.flush()
    print(f"\nView: {host} -> Datasets -> {DATASET_NAME} -> Runs")

    if args.ci and worst < CI_THRESHOLD:
        print(f"\nCI GATE FAILED: worst avg_route_accuracy {worst:.3f} < {CI_THRESHOLD}")
        sys.exit(1)
    if args.ci:
        print(f"\nCI GATE PASSED: worst avg_route_accuracy {worst:.3f} >= {CI_THRESHOLD}")


if __name__ == "__main__":
    main()
