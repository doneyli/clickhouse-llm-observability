#!/usr/bin/env python3
"""Seed everything {{NAME}} needs before it runs. Idempotent, and it reconciles drift.

    {{CMD_SEED}}              # prints created / updated / unchanged per item; exit 1 on failure
    {{CMD_SEED}} --dry-run    # report what would change, write nothing

A definition that changed is updated, not skipped because something with that name
exists. The worked example is one Langfuse prompt: it gets a new version, carrying the
label, only when its text, type or config differ from what the label serves today.

Add a reconciler per artifact type (datasets, score configs, evaluators, dashboards) in the
same shape, or call the root `scripts/` seeders with subprocess. Standard library only,
like verify_demo.py, whose .env loading and API client this reuses.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

# --- DEMO-SPECIFIC: the definitions this demo needs. verify_demo.EXPECTED_PROMPTS must match.
PROMPTS: List[Dict[str, Any]] = [
    {
        "name": "{{NAME}}-answer",
        "label": "production",
        "type": "text",
        "prompt": "You answer questions from customers of a fictional company. "
                  "Reply in two sentences at most.\n\nQuestion: {{question}}",
        "config": {"model": "claude-sonnet-4-6", "temperature": 0},
    },
]


def reconcile_prompt(lf: v.Langfuse, spec: Dict[str, Any], dry_run: bool = False) -> str:
    """Return "created", "updated" or "unchanged" for one prompt definition."""
    path = f"/api/public/v2/prompts/{urllib.parse.quote(spec['name'], safe='')}"
    try:
        current: Optional[Dict[str, Any]] = lf.get(path, label=spec["label"])
    except v.ApiError as e:
        if e.status != 404:
            raise
        current = None  # no version carries the label yet
    if current and (current.get("type"), current.get("prompt"), current.get("config") or {}) == (
            spec["type"], spec["prompt"], spec.get("config") or {}):
        return "unchanged"
    if not dry_run:
        lf.post("/api/public/v2/prompts", {
            "name": spec["name"], "type": spec["type"], "prompt": spec["prompt"],
            "config": spec.get("config") or {}, "labels": [spec["label"]],
        })
    return "created" if current is None else "updated"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    manifest_path = v.DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    env_path = v.env_file(v.manifest_value(manifest, "", "profile"))
    if not v.load_env(env_path):
        print(f"no env file at {env_path}", file=sys.stderr)
        return 1
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (base and pk and sk):
        print(f"LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL must be set in {env_path}",
              file=sys.stderr)
        return 1
    lf = v.Langfuse(base, pk, sk)

    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    print(f"Seeding {base}" + (" (dry run)" if args.dry_run else ""))
    for spec in PROMPTS:
        try:
            result = reconcile_prompt(lf, spec, dry_run=args.dry_run)
        except v.ApiError as e:
            result = "failed"
            print(f"  failed     prompt {spec['name']}: {e}")
        else:
            print(f"  {result:<10} prompt {spec['name']} (label {spec['label']})")
        counts[result] += 1

    print("\n" + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
