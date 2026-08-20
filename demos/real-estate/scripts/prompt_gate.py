"""
CI quality gate for a prompt version — the **Deploy** node of the loop, automated.

This is what turns "someone clicked Promote in the Langfuse UI" into "the eval
suite ran and agreed". It:

  1. runs the `property-concierge-eval` dataset against ONE prompt label,
     scoring every item with the same code + LLM-as-a-Judge evaluators the demo
     uses everywhere else,
  2. compares the run-level means against the thresholds checked into
     `cicd/thresholds.json`,
  3. exits **non-zero** if any threshold is missed — which is what fails the
     GitHub Actions build and blocks the deploy job,
  4. writes a markdown table to `$GITHUB_STEP_SUMMARY` so the verdict is
     readable in the Actions UI without opening logs.

Run locally exactly as CI runs it:
    ./.venv/bin/python scripts/prompt_gate.py --prompt-label candidate

Try the failing case (this is the demo beat — the gate catching a bad prompt):
    ./.venv/bin/python scripts/prompt_gate.py --prompt-label first-draft

Note on the official action: Langfuse ships `langfuse/experiment-action`, which
wraps this pattern with `RunnerContext` + `RegressionError`. It requires the
**v4** Python SDK, which this demo now uses (`langfuse>=4.10,<5.0`) — so adopting
the official action is unblocked, and both symbols are importable from `langfuse`.
This hand-rolled gate is kept on purpose: the threshold comparison and the
non-zero exit are the part of the Deploy node worth SHOWING in a demo, and a
wrapper hides both. `evaluate_gate()` is the piece you would lift into an
`experiment(context)` function to switch over.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project, AGENT_MODEL
from agent.concierge import run_turn
from data.dataset import DATASET_NAME
from evaluators.experiment_evaluators import ALL_EVALUATORS, RUN_EVALUATORS

THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "cicd" / "thresholds.json"


def load_thresholds(path: Path) -> dict:
    """Flatten the threshold file into {score_name: minimum}.

    Keys starting with '_' are commentary. The code_evaluators / llm_judges
    grouping exists to make the file readable; the gate treats them the same
    way, only the reporting distinguishes them.
    """
    raw = json.loads(path.read_text())
    groups = {k: v for k, v in raw.items() if not k.startswith("_")}
    flat: dict = {}
    for group, entries in groups.items():
        for name, minimum in entries.items():
            flat[name] = {"min": float(minimum), "group": group}
    return flat


def make_task(model: str, prompt_label: str):
    """Bind the agent to one model + one prompt version for this run."""
    def concierge_task(*, item, **kwargs):
        q = item.input.get("question") if isinstance(item.input, dict) else str(item.input)
        return run_turn(q, is_experiment=True, model=model, prompt_label=prompt_label)
    return concierge_task


def evaluate_gate(run_evaluations, thresholds: dict):
    """Compare run-level means to thresholds.

    Returns (rows, failures). A threshold whose score is missing from the run is
    a FAILURE, not a pass — a silently absent evaluator would otherwise let a
    broken eval pipeline wave a bad prompt through, which is the exact failure
    mode a gate exists to prevent.
    """
    actual = {ev.name: ev.value for ev in (run_evaluations or [])}
    rows, failures = [], []
    for name, spec in sorted(thresholds.items()):
        value = actual.get(name)
        if value is None:
            status, ok = "MISSING", False
        else:
            ok = float(value) >= spec["min"]
            status = "PASS" if ok else "FAIL"
        rows.append({"name": name, "value": value, "min": spec["min"],
                     "group": spec["group"], "status": status})
        if not ok:
            failures.append(rows[-1])
    return rows, failures


def render(rows, failures, *, prompt_label: str, run_name: str, run_url) -> str:
    """Markdown report — printed to stdout and to the Actions job summary."""
    verdict = "✅ **GATE PASSED**" if not failures else "❌ **GATE FAILED**"
    out = [f"## {verdict} — prompt `{prompt_label}`", "",
           f"Dataset `{DATASET_NAME}` · run `{run_name}`", ""]
    if run_url:
        out += [f"[View the full run in Langfuse]({run_url})", ""]
    out += ["| Metric | Value | Min | Kind | |", "|---|---|---|---|---|"]
    for r in rows:
        icon = {"PASS": "✅", "FAIL": "❌", "MISSING": "⚠️"}[r["status"]]
        shown = "—" if r["value"] is None else f"{float(r['value']):.3f}"
        kind = "code" if r["group"] == "code_evaluators" else "judge"
        out.append(f"| `{r['name']}` | {shown} | {r['min']:.2f} | {kind} | {icon} |")
    out.append("")
    if failures:
        names = ", ".join(f"`{f['name']}`" for f in failures)
        out += [f"**Blocked on:** {names}", "",
                "This prompt version must not be promoted to `production`."]
    else:
        out.append("All thresholds met — safe to promote to `production`.")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-label", default="production",
                    help="Prompt version to gate: production | candidate | first-draft")
    ap.add_argument("--model", default=AGENT_MODEL)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--max-concurrency", type=int, default=4)
    ap.add_argument("--thresholds", default=str(THRESHOLDS_PATH))
    ap.add_argument("--warn-only", action="store_true",
                    help="Report failures but exit 0 (for a dry run before you "
                         "make the gate blocking).")
    args = ap.parse_args()

    thresholds = load_thresholds(Path(args.thresholds))
    verify_project()
    lf = get_langfuse()

    try:
        dataset = lf.get_dataset(DATASET_NAME)
    except Exception as e:
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\n"
              f"Run scripts/seed_dataset.py first.", file=sys.stderr)
        sys.exit(1)

    run_name = args.run_name or f"gate-{args.prompt_label}"
    print(f"Gating prompt '{args.prompt_label}' on '{DATASET_NAME}' "
          f"({len(dataset.items)} items) as run '{run_name}'…\n")

    result = dataset.run_experiment(
        name=DATASET_NAME,
        run_name=run_name,
        description=f"CI quality gate for prompt label '{args.prompt_label}' "
                    f"(model={args.model}).",
        task=make_task(args.model, args.prompt_label),
        evaluators=ALL_EVALUATORS,
        run_evaluators=RUN_EVALUATORS,
        max_concurrency=args.max_concurrency,
        metadata={"model": args.model, "prompt_label": args.prompt_label, "ci_gate": True},
    )
    lf.flush()

    rows, failures = evaluate_gate(getattr(result, "run_evaluations", None), thresholds)
    report = render(rows, failures, prompt_label=args.prompt_label, run_name=run_name,
                    run_url=getattr(result, "dataset_run_url", None))
    print("\n" + report + "\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report + "\n")

    if failures and not args.warn_only:
        sys.exit(1)
    if failures:
        print("(--warn-only: exiting 0 despite failures)")


if __name__ == "__main__":
    main()
