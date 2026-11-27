#!/usr/bin/env python3
"""
Reconcile the 3 Langfuse-managed prompts for the Cluster Health Investigator.

    cluster-health-planner       label `fault-overplan`                  (no scaling rules)
                                 label `production`                      (scaling rules + dedup)
                                 label `candidate-scoped-decomposition`  (tightened scoping)
    cluster-health-worker        label `production` (held fixed)
    cluster-health-synthesizer   label `production` (held fixed; requires
                                        per-claim [worker:<analysis_type>] citations)

The planner prompt is BOTH the experiment lever (production vs candidate) AND
the fault lever (fault-overplan) — one prompt, the whole story.

Each (prompt, label) is reconciled on its own against its complete definition: type
`text`, the prompt text and the config. When the label already serves that definition it
is left alone (unchanged). When another existing version matches, the label moves to it
(updated, no new version). Otherwise a new version is created with the label (created).
An unchanged run writes nothing. Other labels, and versions this script does not own, are
never touched. Exit 1 if any label could not be reconciled.

Local fallbacks in graph.py mean the app still runs if this never ran.

Usage (the env comes from the root .env; see verify_demo.prepare_env):
    python scripts/seed_prompts.py            # host or container
    python scripts/seed_prompts.py --dry-run  # say what would change, write nothing
"""

import argparse
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from graph import (  # noqa: E402  (prompt templates — single source of truth)
    PLANNER_FALLBACK,
    PLANNER_FALLBACK_OVERPLAN,
    SYNTH_FALLBACK,
    WORKER_FALLBACK,
)
from verify_demo import ApiError, Langfuse, client_from_env, prepare_env  # noqa: E402

# v3 candidate: tighter scaling + explicit dedup — the "smarter, cheaper" planner.
PLANNER_CANDIDATE = PLANNER_FALLBACK.replace(
    "- A multi-symptom or broad instability report warrants up to 6 analyses.",
    "- A multi-symptom or broad instability report warrants 3-4 analyses; only a\n"
    "  genuinely cluster-wide outage justifies more.",
).replace(
    "Return a plan whose task count is proportionate to the symptom's breadth.",
    "Choose the SMALLEST set of analyses that fully covers the symptom. Each\n"
    "analysis_type must appear at most once. Return a plan proportionate to the\n"
    "symptom's breadth — prefer fewer, well-targeted analyses.",
)


def prompt_config() -> dict:
    return {"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "temperature": 0.2}


def desired() -> Dict[str, List[dict]]:
    """prompt name -> one entry per owned label, in creation order."""
    return {
        "cluster-health-planner": [
            {"label": "fault-overplan", "prompt": PLANNER_FALLBACK_OVERPLAN,
             "msg": "scaling rules removed (fault lever → max fan-out)"},
            {"label": "production", "prompt": PLANNER_FALLBACK,
             "msg": "scaling rules + dedup instruction"},
            {"label": "candidate-scoped-decomposition", "prompt": PLANNER_CANDIDATE,
             "msg": "tightened scoping — the smarter/cheaper planner candidate"},
        ],
        "cluster-health-worker": [
            {"label": "production", "prompt": WORKER_FALLBACK,
             "msg": "worker finding template (held fixed for variable isolation)"},
        ],
        "cluster-health-synthesizer": [
            {"label": "production", "prompt": SYNTH_FALLBACK,
             "msg": "diagnosis synthesis with per-claim [worker:<type>] citations"},
        ],
    }


def matches(version: Optional[dict], text: str, config: dict) -> bool:
    """A version serves the definition when type, text and config are all equal."""
    return bool(version) and version.get("type") == "text" and version.get("prompt") == text \
        and (version.get("config") or {}) == config


def _q(name: str) -> str:
    return urllib.parse.quote(name, safe="")


def fetch_label(lf: Langfuse, name: str, label: str) -> Optional[dict]:
    try:
        return lf.get(f"/api/public/v2/prompts/{_q(name)}", label=label)
    except ApiError as e:
        if e.status == 404:
            return None
        raise


def versions_of(lf: Langfuse, name: str) -> List[int]:
    rows = lf.pages("/api/public/v2/prompts", {"name": name})
    meta = next((r for r in rows if r.get("name") == name), None)
    return sorted(meta.get("versions") or []) if meta else []


def reconcile_label(lf: Langfuse, name: str, want: dict, config: dict, cache: Dict[int, dict],
                    dry_run: bool = False) -> Tuple[str, str]:
    """Returns (outcome, detail); outcome is unchanged, updated or created."""
    label = want["label"]
    current = fetch_label(lf, name, label)
    if matches(current, want["prompt"], config):
        return "unchanged", f"v{current.get('version')}"
    for v in sorted(versions_of(lf, name), reverse=True):
        if v not in cache:
            cache[v] = lf.get(f"/api/public/v2/prompts/{_q(name)}", version=v)
        if matches(cache[v], want["prompt"], config):
            if not dry_run:
                lf.patch(f"/api/public/v2/prompts/{_q(name)}/versions/{v}", {"newLabels": [label]})
            return "updated", f"label moved to existing v{v}" + (" (dry run)" if dry_run else "")
    if dry_run:
        return "created", "would create a new version (dry run)"
    created = lf.post("/api/public/v2/prompts", {
        "name": name, "type": "text", "prompt": want["prompt"], "labels": [label],
        "config": config, "commitMessage": f"{label}: {want['msg']}"})
    cache[created.get("version")] = created
    return "created", f"v{created.get('version')}"


def reconcile(lf: Langfuse, definitions: Dict[str, List[dict]], config: dict,
              dry_run: bool = False) -> Dict[Tuple[str, str], str]:
    """Reconcile every owned label; a failure on one label does not stop the others."""
    outcomes: Dict[Tuple[str, str], str] = {}
    for name, wants in definitions.items():
        cache: Dict[int, dict] = {}
        for want in wants:
            key = (name, want["label"])
            try:
                outcome, detail = reconcile_label(lf, name, want, config, cache, dry_run)
            except ApiError as e:
                outcome, detail = "failed", str(e)
            outcomes[key] = outcome
            print(f"  {outcome:<9} {name} [{want['label']}] {detail}")
    return outcomes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)
    ok, message = prepare_env()
    if not ok:
        print(f"Prompts NOT seeded: {message}")
        return 1
    lf = client_from_env()
    print(f"Reconciling cluster-health prompts at {lf.base} ({message}) ...")
    outcomes = reconcile(lf, desired(), prompt_config(), args.dry_run)
    counts = {o: sum(1 for v in outcomes.values() if v == o) for o in ("created", "updated", "unchanged", "failed")}
    print("  " + ", ".join(f"{n} {o}" for o, n in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
