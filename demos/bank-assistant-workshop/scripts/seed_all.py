#!/usr/bin/env python3
"""Seed what a bank-assistant-workshop session needs. Idempotent, and it reconciles drift.

    .venv/bin/python scripts/seed_all.py                 # dataset gate: references must be reviewed
    .venv/bin/python scripts/seed_all.py --allow-draft   # synthetic rehearsal: keeps review markers pending
    .venv/bin/python scripts/seed_all.py --dry-run       # report what would change, write nothing

A thin wrapper over the workshop's own sub-commands, which stay the only code that writes:

  * prompt `bank-workshop/<WORKSHOP_ID>/assistant`, labels `workshop-baseline` and
    `workshop-candidate`: `workshop.py prompt --variant ... --label ...` runs only when the
    label is missing or serves another type, text or config than src/northwind/prompts.py.
    (Run directly, that command creates a new version every time.)
  * dataset `bank-workshop/<WORKSHOP_ID>/regression-v1`: the reviewed/draft gate in
    helpers.validate_review runs first, every time, so an unchanged dataset is no way around
    it. Then `workshop.py dataset` runs only when an item in data/seed-dataset.json is
    missing remotely, not ACTIVE, or differs. It upserts by a deterministic id and never
    deletes remote items.

Every write is read back: "created" or "updated" is printed only once Langfuse serves the
definition. Prints one line per item and exits 1 on any failure. Makes no model calls. Run
it with the demo's .venv/bin/python: it imports the prompt text and the gate from the app.
"""
from __future__ import annotations

import argparse
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
    """A wrapped sub-command failed, or its write did not take effect."""


def desired_prompts() -> Dict[str, str]:
    """variant -> the text `workshop.py prompt --variant` publishes (helpers.publish_prompt)."""
    sys.path.insert(0, str(v.DEMO_DIR / "src"))
    from northwind import prompts  # imported late: it loads the workshop .env
    return {"baseline": prompts.V1_BASELINE, "candidate": prompts.WORKSHOP_CANDIDATE}


def review_gate(items: List[dict], *, allow_draft: bool) -> None:
    """helpers.validate_review, the same gate `workshop.py dataset` applies before writing."""
    import helpers  # imported late: it loads the workshop .env
    helpers.validate_review(items, allow_draft=allow_draft)


def run_workshop(env_path: Path, *args: str) -> None:
    cmd = [sys.executable, str(WORKSHOP), "--env-file", str(env_path), *args]
    done = subprocess.run(cmd, cwd=v.DEMO_DIR, capture_output=True, text=True)
    if done.returncode != 0:
        out = (done.stderr or done.stdout).strip()
        raise SeedError(out.splitlines()[-1] if out else f"workshop.py {' '.join(args)} exited {done.returncode}")


def read_prompt(lf: v.Langfuse, name: str, label: str) -> Optional[Dict[str, Any]]:
    try:
        return lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label)
    except v.ApiError as e:
        if e.status != 404:
            raise
        return None  # no version carries the label yet


def reconcile_prompt(lf: v.Langfuse, name: str, label: str, variant: str, text: str,
                     publish: Callable[[str, str], None], dry_run: bool = False) -> str:
    """Return "created", "updated" or "unchanged" for one prompt label."""
    current = read_prompt(lf, name, label)
    if v.prompt_matches(current, variant, text):
        return "unchanged"
    if not dry_run:
        publish(variant, label)
        if not v.prompt_matches(read_prompt(lf, name, label), variant, text):
            raise SeedError(f"published, but label {label} still does not serve the {variant} definition")
    return "created" if current is None else "updated"


def dataset_state(lf: v.Langfuse, name: str, items: List[dict]) -> str:
    """"created" (no dataset), "updated" (an item missing, not ACTIVE or different) or "unchanged"."""
    try:
        lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(name, safe='')}")
    except v.ApiError as e:
        if e.status != 404:
            raise
        return "created"
    return "updated" if v.dataset_diff(name, items, v.dataset_items(lf, name)) else "unchanged"


def reconcile_dataset(lf: v.Langfuse, name: str, items: List[dict], *, allow_draft: bool,
                      upload: Callable[[], None], gate: Callable[..., None] = review_gate,
                      dry_run: bool = False) -> str:
    gate(items, allow_draft=allow_draft)  # before the unchanged shortcut, on every run
    result = dataset_state(lf, name, items)
    if result != "unchanged" and not dry_run:
        upload()
        if dataset_state(lf, name, items) != "unchanged":
            raise SeedError("uploaded, but some items are still missing, not ACTIVE or different")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--env-file", type=Path, help="the private .env to use (default: the demo folder's .env)")
    review = ap.add_mutually_exclusive_group()
    review.add_argument("--reviewed", action="store_true",
                        help="reviewed references only (the default; every marker must be cleared)")
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
    try:
        items = v.local_items()
        texts = desired_prompts()
    except (OSError, KeyError, TypeError, ValueError) as e:
        print(f"local definitions unreadable: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    lf = v.Langfuse(base, pk, sk)
    names = v.workshop_names()

    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    print(f"Seeding {base}, WORKSHOP_ID={names['workshop_id']}" + (" (dry run)" if args.dry_run else ""))

    def record(what: str, fn: Callable[[], str]) -> None:
        try:
            result = fn()
        except (v.ApiError, SeedError, ValueError) as e:
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

    flag = "--allow-draft" if args.allow_draft else "--reviewed"
    record(f"dataset {names['dataset']} ({len(items)} items, {flag})",
           lambda: reconcile_dataset(lf, names["dataset"], items, allow_draft=args.allow_draft,
                                     upload=lambda: run_workshop(env_path, "dataset", flag), dry_run=args.dry_run))

    print("\n" + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
