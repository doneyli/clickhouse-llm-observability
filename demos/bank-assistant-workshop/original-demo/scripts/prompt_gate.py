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
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from northwind import config  # noqa: E402
import run_experiment  # noqa: E402


def render(rows, failed, *, label: str, version, dataset: str, run_name: str, run_url) -> str:
    """Markdown verdict — printed to stdout and appended to the GitHub Actions job summary."""
    verdict = "❌ **GATE FAILED**" if failed else "✅ **GATE PASSED**"
    out = [f"## {verdict} — prompt `{label}` (v{version}) · dataset `{dataset}`", "", f"Run `{run_name}`", ""]
    if run_url:
        out += [f"[View the full run in Langfuse]({run_url})", ""]
    out += ["| Metric | Value | Min | Kind | |", "|---|---|---|---|---|"]
    for r in rows:
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "MISSING": "⚠️"}[r["status"]]
        shown = "—" if r["value"] is None else f"{r['value']:.3f}"
        kind = "deterministic (hard)" if r["kind"] == "hard" else "LLM judge (soft)"
        out.append(f"| `{r['name']}` | {shown} | {r['min']:.2f} | {kind} | {icon} |")
    out.append("")
    blocked = [r["name"] for r in rows if r["kind"] == "hard" and r["status"] in ("FAIL", "MISSING")]
    if blocked:
        out += ["**Blocked on:** " + ", ".join(f"`{n}`" for n in blocked), "",
                "This prompt version must not be promoted to `production`."]
    else:
        out.append("All hard thresholds met — safe to promote to `production`.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-label", default="development")
    ap.add_argument("--model", default=config.AGENT_MODEL)
    ap.add_argument("--thresholds", default=str(ROOT / "cicd" / "thresholds.json"))
    ap.add_argument("--dataset", default=None, help="default: thresholds.json 'dataset' (English golden set)")
    ap.add_argument("--run-name", default=None,
                    help="default 'gate · <label> {version} · <model>'; CI passes a unique name per run "
                         "so repeated runs never append to the same Langfuse run")
    ap.add_argument("--warn-only", action="store_true", help="report failures but exit 0 (dry run)")
    a = ap.parse_args()
    t = json.loads(Path(a.thresholds).read_text())
    if a.dataset:  # per-dataset thresholds EXTEND the defaults (stricter wins)
        over = t.get("per_dataset", {}).get(a.dataset, {})
        t = {**t, "dataset": a.dataset,
             "hard": {**t["hard"], **over.get("hard", {})}, "soft": {**t["soft"], **over.get("soft", {})}}
    run_name = a.run_name or f"gate · {a.prompt_label} {{version}} · {a.model}"
    print(f"Quality gate · prompt label '{a.prompt_label}' · {a.model} · dataset {t['dataset']}\n", flush=True)
    result = run_experiment.run(t["dataset"], a.prompt_label, a.model, run_name=run_name)
    got = {e.name: e.value for e in result.run_evaluations}
    version = (getattr(result, "metadata", None) or {}).get("prompt_version") if hasattr(result, "metadata") else None
    rows, failed = [], False
    print(f"\n{'metric':<28}{'value':>8}{'threshold':>11}   result")
    for kind in ("hard", "soft"):
        for name, minimum in t[kind].items():
            v = got.get(name)
            ok = v is not None and v >= minimum
            status = "PASS" if ok else ("MISSING" if v is None else ("FAIL" if kind == "hard" else "WARN"))
            failed |= (not ok and kind == "hard")
            rows.append({"name": name, "value": v, "min": minimum, "kind": kind, "status": status})
            print(f"{name:<28}{('—' if v is None else f'{v:.3f}'):>8}{minimum:>11.2f}   {status} ({kind})")
    url = getattr(result, "dataset_run_url", None)
    if url:
        print(f"\nEvidence (dataset run): {url}")
    from northwind import prompts
    _, resolved = prompts.get_system_prompt(config.get_langfuse(), a.prompt_label)
    report = render(rows, failed, label=a.prompt_label, version=getattr(resolved, "version", "?"),
                    dataset=t["dataset"], run_name=run_name.replace("{version}", f"v{getattr(resolved, 'version', '?')}"),
                    run_url=url)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report + "\n\n")
    if failed:
        print(f"\n✗ GATE FAILED — '{a.prompt_label}' must not be promoted to production.")
        if not a.warn_only:
            sys.exit(1)
        print("(--warn-only: exiting 0 despite failures)")
        return
    print(f"\n✓ GATE PASSED — '{a.prompt_label}' may be promoted (scripts/prompt_label.py --promote {a.prompt_label}).")


if __name__ == "__main__":
    main()
