"""
CI quality gate: evaluate a claims-sop-rules (or, with --suite service, annuity-service-rules) version
BEFORE it gets the `production` label.

    .venv/bin/python scripts/prompt_gate.py --prompt-label staging      # the 'concise' candidate → exit 1
    .venv/bin/python scripts/prompt_gate.py --prompt-label production   # → exit 0
    .venv/bin/python scripts/prompt_gate.py --suite service --prompt-label staging   # 'process-first' → exit 1

Runs the rules suite on claims-golden-v1 (perfect intake, the SME-codified oracle as ground truth), reads
the run-level averages and compares them with cicd/thresholds.json. A MISSING metric is a failure, never a
pass. Writes a Markdown verdict to $GITHUB_STEP_SUMMARY when run in GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiment  # noqa: E402

from claims import config  # noqa: E402


def check(name: str, rule: dict, value):
    if value is None:
        return "MISSING"
    if "min" in rule and value < rule["min"]:
        return "FAIL"
    if "max" in rule and value > rule["max"]:
        return "FAIL"
    return "PASS"


def render(rows, failed, *, prompt, dataset, label, version, model, run_name, run_url) -> str:
    out = [f"## {'❌ **GATE FAILED**' if failed else '✅ **GATE PASSED**'} — `{prompt}` `{label}` (v{version}) · {model}",
           "", f"Run `{run_name}` on `{dataset}`", ""]
    if run_url:
        out += [f"[Open the run in Langfuse]({run_url})", ""]
    out += ["| Metric | Value | Bound | Kind | |", "|---|---|---|---|---|"]
    for r in rows:
        icon = {"PASS": "✅", "FAIL": "❌" if r["kind"] == "hard" else "⚠️", "MISSING": "⚠️"}[r["status"]]
        bound = f"≥ {r['rule']['min']}" if "min" in r["rule"] else f"≤ {r['rule']['max']}"
        shown = "—" if r["value"] is None else f"{r['value']:.3f}"
        out.append(f"| `{r['name']}` | {shown} | {bound} | {'hard' if r['kind'] == 'hard' else 'soft (warn)'} | {icon} |")
    blocked = [r["name"] for r in rows if r["kind"] == "hard" and r["status"] != "PASS"]
    out += ["", ("**Blocked on:** " + ", ".join(f"`{n}`" for n in blocked) +
                 "\n\nThis version must not be promoted to `production`.") if blocked else
            "All hard thresholds met — safe to promote to `production`."]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-label", default="staging")
    ap.add_argument("--suite", default="rules", choices=["rules", "service"],
                    help="rules = claims-sop-rules on claims-golden-v1; service = annuity-service-rules")
    ap.add_argument("--model", default=None, help="override the model in the prompt config (pre-upgrade check)")
    ap.add_argument("--thresholds", default=str(ROOT / "cicd" / "thresholds.json"))
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--limit", type=int, default=None, help="first N items only (never in CI)")
    ap.add_argument("--warn-only", action="store_true")
    a = ap.parse_args()
    t = json.loads(Path(a.thresholds).read_text())
    if a.suite != "rules":
        t = {**t, **t["suites"][a.suite]}
    import time
    run_name = a.run_name or f"gate · {a.prompt_label} v{{version}} · {time.strftime('%m-%d %H:%M:%S')}"
    result, version = run_experiment.run(a.suite, a.prompt_label, a.model, run_name=run_name, limit=a.limit,
                                         dataset=t["dataset"])
    got = {e.name: e.value for e in result.run_evaluations}
    # An item whose task raised (rate limit, tool error) is DROPPED from the run and from the averages — a run
    # that did not evaluate every claim must not pass.
    import claims.config as _c
    expected_n = len(_c.get_langfuse().get_dataset(t["dataset"]).items) if not a.limit else a.limit
    got["items-evaluated"] = float(len(getattr(result, "item_results", []) or []))
    t["hard"] = {"items-evaluated": {"min": float(expected_n)}, **t["hard"]}
    rows, failed = [], False
    print(f"\n{'metric':<32}{'value':>8}  bound      result")
    for kind in ("hard", "soft"):
        for name, rule in t[kind].items():
            st = check(name, rule, got.get(name))
            failed |= kind == "hard" and st != "PASS"
            rows.append({"name": name, "value": got.get(name), "rule": rule, "kind": kind, "status": st})
            bound = f">= {rule['min']}" if "min" in rule else f"<= {rule['max']}"
            v = got.get(name)
            print(f"{name:<32}{('—' if v is None else f'{v:.3f}'):>8}  {bound:<10} {st} ({kind})")
    url = getattr(result, "dataset_run_url", None)
    model = a.model or "prompt config model"
    report = render(rows, failed, prompt=run_experiment.PROMPT.get(a.suite, "claims-sop-rules"), dataset=t["dataset"],
                    label=a.prompt_label, version=version, model=model,
                    run_name=run_name.replace("{version}", str(version)), run_url=url)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(report + "\n\n")
    if url:
        print(f"\nEvidence: {url}")
    if failed:
        print(f"\n✗ GATE FAILED — '{a.prompt_label}' (v{version}) must not be promoted to production.")
        return 0 if a.warn_only else 1
    print(f"\n✓ GATE PASSED — promote with: scripts/prompt_label.py --promote {a.prompt_label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
