#!/usr/bin/env python3
"""Seed what a bank-assistant-workshop session needs. Idempotent, and it reconciles drift.

    .venv/bin/python scripts/seed_all.py                 # dataset upload requires --reviewed markers cleared
    .venv/bin/python scripts/seed_all.py --allow-draft   # synthetic rehearsal: keeps review markers pending
    .venv/bin/python scripts/seed_all.py --dry-run       # report what would change, write nothing

A thin wrapper over the workshop's own sub-commands, which stay the only code that writes:

  * prompt `bank-workshop/<WORKSHOP_ID>/assistant`, labels `workshop-baseline` and
    `workshop-candidate`: `workshop.py prompt --variant ... --label ...` runs only when the
    label is missing or serves text or config that differs from src/northwind/prompts.py.
    (Run directly, that command creates a new version every time.)
  * dataset `bank-workshop/<WORKSHOP_ID>/regression-v1`: `workshop.py dataset` runs only when
    an item in data/seed-dataset.json is missing remotely or differs. It upserts by a
    deterministic item id and never deletes remote items.

Prints created / updated / unchanged per item and exits 1 on any failure. Makes no model
calls. Run it with the demo's .venv/bin/python: it imports the prompt text from src/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

WORKSHOP = v.DEMO_DIR / "workshop.py"


class SeedError(Exception):
    """A wrapped sub-command failed."""


def desired_prompts() -> Dict[str, str]:
    """variant -> the text `workshop.py prompt --variant` publishes (helpers.publish_prompt)."""
    sys.path.insert(0, str(v.DEMO_DIR / "src"))
    from northwind import prompts  # imported late: it loads the workshop .env
    return {"baseline": prompts.V1_BASELINE, "candidate": prompts.WORKSHOP_CANDIDATE}


def item_id(dataset: str, local_id: str) -> str:
    """The deterministic remote id helpers.seed_dataset upserts each item under."""
    return hashlib.sha256(f"{dataset}/{local_id}".encode()).hexdigest()[:32]


def run_workshop(env_path: Path, *args: str) -> None:
    cmd = [sys.executable, str(WORKSHOP), "--env-file", str(env_path), *args]
    done = subprocess.run(cmd, cwd=v.DEMO_DIR, capture_output=True, text=True)
    if done.returncode != 0:
        out = (done.stderr or done.stdout).strip()
        raise SeedError(out.splitlines()[-1] if out else f"workshop.py {' '.join(args)} exited {done.returncode}")


def reconcile_prompt(lf: v.Langfuse, name: str, label: str, variant: str, text: str,
                     publish: Callable[[str, str], None], dry_run: bool = False) -> str:
    """Return "created", "updated" or "unchanged" for one prompt label."""
    try:
        current: Optional[Dict[str, Any]] = lf.get(
            f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label)
    except v.ApiError as e:
        if e.status != 404:
            raise
        current = None  # no version carries the label yet
    if current and current.get("type") == "text" and current.get("prompt") == text \
            and (current.get("config") or {}).get("variant") == variant:
        return "unchanged"
    if not dry_run:
        publish(variant, label)
    return "created" if current is None else "updated"


def dataset_drift(lf: v.Langfuse, name: str, items: List[dict]) -> str:
    """Return "created", "updated" or "unchanged" for the dataset as a whole."""
    try:
        lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(name, safe='')}")
    except v.ApiError as e:
        if e.status != 404:
            raise
        return "created"
    remote = {r.get("id"): r for r in v.dataset_items(lf, name)}
    for item in items:
        r = remote.get(item_id(name, item["id"]))
        if r is None or r.get("status", "ACTIVE") != "ACTIVE" or (
                r.get("input"), r.get("expectedOutput"), r.get("metadata")) != (
                item["input"], item["expected_output"], item["metadata"]):
            return "updated"
    return "unchanged"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--env-file", type=Path, help="the private .env to use (default: the demo folder's .env)")
    review = ap.add_mutually_exclusive_group()
    review.add_argument("--reviewed", action="store_true",
                        help="upload reviewed references (the default; markers must be cleared)")
    review.add_argument("--allow-draft", action="store_true",
                        help="synthetic rehearsal upload that keeps human-review markers pending")
    args = ap.parse_args(argv)

    env_path = args.env_file.resolve() if args.env_file else v.DEMO_DIR / ".env"
    if not v.load_env(env_path):
        print(f"no env file at {env_path}", file=sys.stderr)
        return 1
    os.environ["WORKSHOP_ENV_FILE"] = str(env_path)  # the imported config reads the same file
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (base and pk and sk):
        print(f"LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL must be set in {env_path}",
              file=sys.stderr)
        return 1
    lf = v.Langfuse(base, pk, sk)
    names = v.workshop_names()
    texts = desired_prompts()
    items = json.loads(v.DATASET_FILE.read_text())

    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    print(f"Seeding {base}, WORKSHOP_ID={names['workshop_id']}" + (" (dry run)" if args.dry_run else ""))

    def record(what: str, fn: Callable[[], str]) -> None:
        try:
            result = fn()
        except (v.ApiError, SeedError) as e:
            result = "failed"
            print(f"  failed     {what}: {e}")
        else:
            print(f"  {result:<10} {what}")
        counts[result] += 1

    for label, variant in v.PROMPT_LABELS.items():
        record(f"prompt {names['prompt']} (label {label}, {variant})",
               lambda label=label, variant=variant: reconcile_prompt(
                   lf, names["prompt"], label, variant, texts[variant],
                   lambda var, lab: run_workshop(env_path, "prompt", "--variant", var, "--label", lab),
                   dry_run=args.dry_run))

    def seed_dataset() -> str:
        result = dataset_drift(lf, names["dataset"], items)
        if result != "unchanged" and not args.dry_run:
            run_workshop(env_path, "dataset", "--allow-draft" if args.allow_draft else "--reviewed")
        return result

    record(f"dataset {names['dataset']} ({len(items)} items)", seed_dataset)

    print("\n" + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
