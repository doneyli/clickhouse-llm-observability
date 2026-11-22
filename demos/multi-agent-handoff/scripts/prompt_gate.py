"""
CI quality gate: evaluate a prompt label BEFORE it gets `production` (SPEC §11, loop step ⟳).

    ./scripts/run_services.sh
    .venv/bin/python scripts/prompt_gate.py --label candidate                  # the F1 fix → passes
    .venv/bin/python scripts/prompt_gate.py --label fault-f1                   # the F1 bug → BLOCKED
    .venv/bin/python scripts/prompt_gate.py --label candidate --compare production
    .venv/bin/python scripts/prompt_gate.py --prompt tech-support --label fault-f3   # → BLOCKED (loops)

Runs `handoff-routing` (seeded items + the ones promoted from production) on the a2a topology,
with every agent on the label under review (an agent with no version under it runs its
production prompt), then compares the run-level scores with cicd/thresholds.json. A missing
metric fails; an item whose task crashed counts as not evaluated, and a run that did not
evaluate every item fails. `--compare production` also runs the production label and fails
if a `compare` metric falls further below it than allowed.

Exit 1 = blocked. Writes a Markdown verdict to $GITHUB_STEP_SUMMARY in GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiment  # noqa: E402

from handoff import prompts  # noqa: E402
from handoff.agents.registry import SPECIALISTS  # noqa: E402
from handoff.lfapi import LangfuseAPIError, api  # noqa: E402


def label_version(prompt: str, label: str) -> int:
    try:
        return api("GET", f"/api/public/v2/prompts/{prompt}", params={"label": label})["version"]
    except LangfuseAPIError as e:
        if e.status == 404:
            # Otherwise the agent would fall back to its production prompt and the gate would grade that.
            sys.exit(f"✗ {prompt!r} has no version labelled {label!r}: nothing to gate")
        raise


def metrics(result) -> dict:
    got = {e.name: e.value for e in result.run_evaluations if isinstance(e.value, (int, float))}
    got["items-evaluated"] = float(sum(1 for r in result.item_results if isinstance(r.output, dict)))
    return got


def status(rule: dict, value) -> str:
    if value is None:
        return "MISSING"
    if "min" in rule and value < rule["min"]:
        return "FAIL"
    if "max" in rule and value > rule["max"]:
        return "FAIL"
    return "PASS"


def bound(rule: dict) -> str:
    return f"≥ {rule['min']}" if "min" in rule else f"≤ {rule['max']}"


def render(rows, failed, *, prompt, label, version, tag, run_name, url, base=None) -> str:
    out = [f"## {'❌ **GATE FAILED**' if failed else '✅ **GATE PASSED**'} — `{prompt}` `{label}` (v{version})", "",
           f"Run `{run_name}` on `handoff-routing`, a2a topology, prompts `{tag}`", ""]
    if url:
        out += [f"[Open the run in Langfuse]({url})", ""]
    head = "| Metric | Value | Bound | Kind | |" + (" production |" if base else "")
    out += [head, "|---|---|---|---|---|" + ("---|" if base else "")]
    for r in rows:
        icon = {"PASS": "✅", "FAIL": "❌" if r["kind"] != "soft" else "⚠️", "MISSING": "⚠️"}[r["status"]]
        shown = "—" if r["value"] is None else f"{r['value']:.3f}"
        line = f"| `{r['name']}` | {shown} | {r['bound']} | {r['kind']} | {icon} |"
        if base:
            b = base.get(r["name"])
            line += f" {'—' if b is None else f'{b:.3f}'} |"
        out.append(line)
    blocked = [r["name"] for r in rows if r["kind"] != "soft" and r["status"] != "PASS"]
    out += ["", ("**Blocked on:** " + ", ".join(f"`{n}`" for n in blocked) +
                 f"\n\nDo not promote `{label}` to `production`.") if blocked else
            f"All hard thresholds met: safe to promote `{label}` to `production`."]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="concierge", help="the prompt under review (its label must exist)")
    ap.add_argument("--label", default="candidate")
    ap.add_argument("--compare", metavar="LABEL", help="also run this label (e.g. production) and bound the drop")
    ap.add_argument("--thresholds", default=str(ROOT / "cicd" / "thresholds.json"))
    ap.add_argument("--run-name", help="default: gate · <prompt> <label> v<version> · time")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int, help="first N items only (never in CI)")
    ap.add_argument("--warn-only", action="store_true")
    a = ap.parse_args()
    t = json.loads(Path(a.thresholds).read_text())
    version = label_version(prompts.prompt_name(a.prompt), a.label)
    tag = prompts.version_tag(["concierge", *SPECIALISTS], a.label)
    run_name = a.run_name or f"gate · {a.prompt} {a.label} v{version} · {time.strftime('%m-%d %H:%M:%S')}"
    run_name = run_name.replace("{version}", str(version))
    print(f"Gating {a.prompt} @{a.label} = v{version}  ({tag})", flush=True)
    result = run_experiment.run(t.get("topology", "a2a"), label=a.label, concurrency=a.concurrency,
                                limit=a.limit, name=run_name)
    got = metrics(result)
    base = None
    if a.compare:
        print(f"\nBaseline: {a.compare}", flush=True)
        base = metrics(run_experiment.run(t.get("topology", "a2a"), label=a.compare, concurrency=a.concurrency,
                                          limit=a.limit, name=f"{run_name} · baseline {a.compare}"))
    rules = [("items-evaluated", {"min": float(result.items_requested)}, "hard")]
    rules += [(n, r, "hard") for n, r in t["hard"].items()] + [(n, r, "soft") for n, r in t["soft"].items()]
    if base:
        for n, drop in t.get("compare", {}).items():
            if base.get(n) is not None:
                rules.append((f"{n} vs {a.compare}", {"min": round(base[n] - drop, 4)}, "compare"))
    rows, failed = [], False
    print(f"\n{'metric':<34}{'value':>8}  {'bound':<12} result" + (f"{'':16}{a.compare}" if base else ""))
    for name, rule, kind in rules:
        value = got.get(name.split(" vs ")[0])
        st = status(rule, value)
        failed |= kind != "soft" and st != "PASS"
        rows.append({"name": name, "value": value, "bound": bound(rule), "kind": kind, "status": st})
        b = base.get(name.split(" vs ")[0]) if base else None
        print(f"{name:<34}{('—' if value is None else f'{value:.3f}'):>8}  {bound(rule):<12} {st + ' (' + kind + ')':<22}"
              + ("" if not base else ("—" if b is None else f"{b:.3f}")))
    report = render(rows, failed, prompt=a.prompt, label=a.label, version=version, tag=tag, run_name=run_name,
                    url=result.experiment_url, base=base)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(report + "\n\n")
    if result.experiment_url:
        print(f"\nEvidence: {result.experiment_url}")
    if failed:
        print(f"\n✗ GATE FAILED — {a.prompt} '{a.label}' (v{version}) must not be promoted to production.")
        return 0 if a.warn_only else 1
    print(f"\n✓ GATE PASSED — promote with: scripts/loop.py promote --label {a.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
