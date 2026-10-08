"""CI quality gate (EXP-06): evaluate a prompt version BEFORE it gets the production label.

  .venv/bin/python scripts/prompt_gate.py --prompt-label development   # exits 1 → blocked
  .venv/bin/python scripts/prompt_gate.py --prompt-label staging       # exits 0 → promotable

Runs the golden dataset against the labelled prompt, reads the run-level
averages and compares them with cicd/thresholds.json. Wire it to a Langfuse
prompt webhook (on new version / label change) → GitHub repository_dispatch →
this script; see cicd/README.md and .github/workflows/northwind-prompt-gate.yml.
A MISSING metric counts as a failure, never as a pass.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from northwind import config  # noqa: E402
import run_experiment  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-label", default="development")
    ap.add_argument("--model", default=config.AGENT_MODEL)
    ap.add_argument("--thresholds", default=str(ROOT / "cicd" / "thresholds.json"))
    ap.add_argument("--dataset", default=None, help="default: thresholds.json 'dataset' (English golden set)")
    a = ap.parse_args()
    t = json.loads(Path(a.thresholds).read_text())
    if a.dataset:  # per-dataset thresholds EXTEND the defaults (stricter wins)
        over = t.get("per_dataset", {}).get(a.dataset, {})
        t = {**t, "dataset": a.dataset,
             "hard": {**t["hard"], **over.get("hard", {})}, "soft": {**t["soft"], **over.get("soft", {})}}
    print(f"Quality gate · prompt label '{a.prompt_label}' · {a.model} · dataset {t['dataset']}\n", flush=True)
    result = run_experiment.run(t["dataset"], a.prompt_label, a.model, run_name=f"gate · {a.prompt_label} {{version}} · {a.model}")
    got = {e.name: e.value for e in result.run_evaluations}
    failed = False
    print(f"\n{'metric':<28}{'value':>8}{'threshold':>11}   result")
    for kind in ("hard", "soft"):
        for name, minimum in t[kind].items():
            v = got.get(name)
            ok = v is not None and v >= minimum
            verdict = "PASS" if ok else ("FAIL" if kind == "hard" else "WARN")
            failed |= (not ok and kind == "hard")
            print(f"{name:<28}{('—' if v is None else f'{v:.3f}'):>8}{minimum:>11.2f}   {verdict} ({kind})")
    url = getattr(result, "dataset_run_url", None)
    if url:
        print(f"\nEvidence (dataset run): {url}")
    if failed:
        print(f"\n✗ GATE FAILED — '{a.prompt_label}' must not be promoted to production.")
        sys.exit(1)
    print(f"\n✓ GATE PASSED — '{a.prompt_label}' may be promoted (scripts/prompt_label.py --promote {a.prompt_label}).")


if __name__ == "__main__":
    main()
