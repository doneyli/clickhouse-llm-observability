"""
The prompt-label moves of the AI engineering loop on bug F1 (SPEC §17).

    .venv/bin/python scripts/loop.py status       # which concierge version holds which label
    .venv/bin/python scripts/loop.py ship-bug     # "release 1.1": the F1 concierge prompt becomes production
    .venv/bin/python scripts/loop.py fix          # step 4: the fix, as a new version labelled candidate
    .venv/bin/python scripts/loop.py promote      # deploy: production moves to the candidate version
    .venv/bin/python scripts/loop.py promote --version 1   # roll back to a known version
    .venv/bin/python scripts/loop.py reset        # production back to the seeded prompt (abort a rehearsal)

Every move is one label change on the `concierge` prompt in Langfuse. The agents fetch their
prompt by label at runtime (60 s cache), so nothing redeploys: production traffic picks up a
move within a minute. A label change is also what fires the CI gate, when the Langfuse prompt
automation is set up (cicd/README.md).

Versions are found by their text, so rerunning the loop relabels the existing F1 and fix
versions instead of piling up copies.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config, faults, prompts  # noqa: E402
from handoff.agents.registry import AGENTS  # noqa: E402
from handoff.lfapi import api, ui  # noqa: E402

PROMPT = "concierge"
KNOWN = {prompts.CONCIERGE: "seeded (good)", faults.F1_CONCIERGE: "F1 bug", faults.F1_FIX_CONCIERGE: "F1 fix"}


def versions(name: str = PROMPT) -> list[dict]:
    meta = api("GET", "/api/public/v2/prompts", params={"name": name}).get("data") or []
    if not meta:
        return []
    return [api("GET", f"/api/public/v2/prompts/{name}", params={"version": v}) for v in meta[0]["versions"]]


def find(text: str, vs: list[dict]) -> Optional[dict]:
    return next((v for v in reversed(vs) if v["prompt"] == text), None)


def holder(label: str, vs: list[dict]) -> Optional[dict]:
    return next((v for v in vs if label in v["labels"]), None)


def ensure(text: str, label: str, commit: str, vs: list[dict]) -> dict:
    """The version with this text, carrying `label` (created if no version has the text)."""
    v = find(text, vs)
    if v is None:
        lf = config.get_langfuse("seed")
        cfg = {"agent": PROMPT, "model": AGENTS[PROMPT].model, "demo": "multi-agent-handoff"}
        created = lf.create_prompt(name=PROMPT, type="text", prompt=text, labels=[label], config=cfg,
                                   tags=["multi-agent-handoff"], commit_message=commit)
        lf.flush()
        print(f"+ {PROMPT} v{created.version} created → {label}  ({commit})")
        return {"version": created.version, "labels": [label], "prompt": text}
    if label in v["labels"]:
        print(f"= {PROMPT} v{v['version']} already holds {label}")
        return v
    move(v["version"], label)
    return v


def move(version: int, label: str) -> None:
    # newLabels is additive on the version; a label is unique per prompt, so this MOVES it here.
    api("PATCH", f"/api/public/v2/prompts/{PROMPT}/versions/{version}", {"newLabels": [label]})
    print(f"→ {label} now on {PROMPT} v{version}")


def status() -> None:
    vs = versions()
    print(f"{PROMPT} versions ({ui('/prompts/' + PROMPT)}):")
    for v in vs:
        print(f"  v{v['version']:<3} {KNOWN.get(v['prompt'], 'other'):<14} {', '.join(v['labels']) or '—'}")
    prod = holder("production", vs)
    state = KNOWN.get(prod["prompt"], "other") if prod else "missing"
    print(f"\nproduction = v{prod['version'] if prod else '?'} ({state})")
    if state == "F1 bug":
        print("The loop is mid-way: production runs the F1 bug. Continue with `fix` and `promote`, or `reset`.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["status", "ship-bug", "fix", "promote", "reset"])
    ap.add_argument("--label", default="candidate", help="promote: the label whose version becomes production")
    ap.add_argument("--version", type=int, help="promote: this version instead (a rollback)")
    a = ap.parse_args()
    vs = versions()
    if a.step == "status":
        status()
        return
    if a.step == "ship-bug":
        v = ensure(faults.F1_CONCIERGE, "fault-f1", "release 1.1: simplified routing, money questions straight "
                   "to billing", vs)
        move(v["version"], "production")
        print("Release 1.1 is live: outage credits now skip tech-support. Agents pick it up within 60 s.")
    elif a.step == "fix":
        ensure(faults.F1_FIX_CONCIERGE, "candidate", "fix F1: outage credits go to tech-support first", vs)
        print("Next: scripts/prompt_gate.py --label candidate, then scripts/loop.py promote")
    elif a.step == "promote":
        v = next((x for x in vs if x["version"] == a.version), None) if a.version else holder(a.label, vs)
        if v is None:
            sys.exit(f"no {PROMPT} version {'v' + str(a.version) if a.version else 'carries ' + repr(a.label)}")
        before = holder("production", vs)
        move(v["version"], "production")
        if before and before["version"] != v["version"]:
            print(f"(was v{before['version']}; roll back with: scripts/loop.py promote --version {before['version']})")
        print("Agents pick it up within 60 s. Run scripts/run_traffic.py again and watch the dashboard.")
    elif a.step == "reset":
        ensure(prompts.CONCIERGE, "production", "seed_prompts.py", vs)
    print()
    status()


if __name__ == "__main__":
    main()
