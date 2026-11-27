#!/usr/bin/env python3
"""Seed everything text-to-sql needs before it runs. Idempotent, and it reconciles drift.

    python demos/text-to-sql/scripts/seed_all.py              # prints created / updated / unchanged per item; exit 1 on failure
    python demos/text-to-sql/scripts/seed_all.py --dry-run    # report what would change, write nothing

Run it from the repo root. It reads the root `.env` (this is a `container` demo), ignores
shell-exported LANGFUSE_* keys, and hands the seeders below one Langfuse URL as both
LANGFUSE_BASE_URL and LANGFUSE_HOST (they disagree on which one they read).

It wraps the seeders this demo already has, in this order:

1. `scripts/seed-app-prompts.py` (root): the managed prompts. A prompt gets a new version,
   carrying the label, when its text differs from what the label serves. This script
   compares first and reports per prompt, then runs the seeder only if something differs.
2. `scripts/seed-code-evaluators.sh` (root): the code evaluators that score every trace at
   ingest (`chain-gate-check` -> `gate-pass`, `sql-safety-guard` -> `sql-risk`, ...).
   Self-hosted only; it upserts the rows and reconciles source and filters. `./setup.sh`
   also runs it.
3. `seed_step_dataset.py`: the `text-to-sql-analysis-step` dataset. That seeder appends
   every item on every run, so it runs only when the dataset does not exist yet. Otherwise
   this script adds the curated items that are missing and updates those whose expected
   output drifted, matching on the question.
4. `seed_refine_datasets.py`: the two refine-loop datasets. Items have stable ids, so the
   seeder upserts them; it always runs.

Steps 3 (on a fresh project) and 4 import the `langfuse` package. Run this with a Python
that has the demo's requirements (`pip install -r demos/text-to-sql/requirements.txt`), or
pass `--python`. The rest is standard library only, like verify_demo.py, whose .env loading
and API client this reuses.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

SCRIPTS = Path(__file__).resolve().parent
PROMPT_SEEDER = v.REPO_ROOT / "scripts" / "seed-app-prompts.py"
EVALUATOR_SEEDER = v.REPO_ROOT / "scripts" / "seed-code-evaluators.sh"
STEP_SEEDER = SCRIPTS / "seed_step_dataset.py"
REFINE_SEEDER = SCRIPTS / "seed_refine_datasets.py"

Runner = Callable[[List[str], Dict[str, str]], int]


def load_module(name: str, path: Path) -> ModuleType:
    """Import a seeder by path (the root prompt seeder's file name has hyphens)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod  # dataclasses look their module up while the class is built
    spec.loader.exec_module(mod)
    return mod


def run(cmd: List[str], env: Dict[str, str]) -> int:
    """Run one seeder from the repo root, its output streamed, and return its exit code."""
    return subprocess.call(cmd, cwd=str(v.REPO_ROOT), env=env)


def child_env(base: str) -> Dict[str, str]:
    """The environment the seeders get: the loaded .env, with one Langfuse URL under both
    names. seed-app-prompts.py reads LANGFUSE_HOST, the SDK reads LANGFUSE_BASE_URL first."""
    env = dict(os.environ)
    env["LANGFUSE_BASE_URL"] = env["LANGFUSE_HOST"] = base
    return env


# --- 1. Prompts ----------------------------------------------------------------------------

def prompt_plan(lf: Any, entries: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """(name, label, created | updated | unchanged) per seeder entry, by the seeder's own rule:
    a new version only when the label's text differs from the checked-in text."""
    plan = []
    for name, label, text in entries:
        try:
            current = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label)
        except v.ApiError as e:
            if e.status != 404:
                raise
            current = None
        if current is None:
            plan.append((name, label, "created"))
        elif (current.get("prompt") or "").strip() == text.strip():
            plan.append((name, label, "unchanged"))
        else:
            plan.append((name, label, "updated"))
    return plan


def prompt_entries(mod: ModuleType) -> List[Tuple[str, str, str]]:
    """(name, label, text) for every prompt the root seeder writes."""
    out = []
    for entry in mod.PROMPTS:
        name, text, _message, label, _config = mod._unpack(entry)
        out.append((name, label, text))
    return out


# --- 3. The per-step dataset -----------------------------------------------------------------

def existing_items(lf: v.Langfuse, dataset: str) -> List[dict]:
    rows: List[dict] = []
    for page in range(1, 21):
        d = lf.get("/api/public/dataset-items", datasetName=dataset, limit=100, page=page) or {}
        rows += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
    return rows


def step_plan(curated: List[Any], rows: List[dict]) -> List[Tuple[str, Any, Optional[str]]]:
    """(created | updated | unchanged, curated item, existing item id) per curated item.

    Items are matched on `input.question`. Rows that `seed_step_dataset.py --link-latest`
    minted from traces (`metadata.linked_from`) are left alone, and archived rows are
    ignored. When a question appears more than once, the first match is compared."""
    by_question: Dict[str, dict] = {}
    for row in rows:
        meta = row.get("metadata") or {}
        if row.get("status") == "ARCHIVED" or (isinstance(meta, dict) and meta.get("linked_from")):
            continue
        q = (row.get("input") or {}).get("question") if isinstance(row.get("input"), dict) else None
        if q and q not in by_question:
            by_question[q] = row
    plan = []
    for item in curated:
        row = by_question.get(item.input["question"])
        if row is None:
            plan.append(("created", item, None))
        elif row.get("expectedOutput") != item.expected_output or (row.get("metadata") or {}) != item.metadata:
            plan.append(("updated", item, row.get("id")))
        else:
            plan.append(("unchanged", item, row.get("id")))
    return plan


def apply_step_plan(lf: v.Langfuse, dataset: str, plan: List[Tuple[str, Any, Optional[str]]]) -> None:
    for action, item, item_id in plan:
        if action == "unchanged":
            continue
        body = {"datasetName": dataset, "input": item.input,
                "expectedOutput": item.expected_output, "metadata": item.metadata}
        if item_id:
            body["id"] = item_id  # an existing id upserts in place
        lf.post("/api/public/dataset-items", body)


# --- Main ----------------------------------------------------------------------------------

class Tally:
    def __init__(self) -> None:
        self.counts = {"created": 0, "updated": 0, "unchanged": 0, "ran": 0, "skipped": 0, "failed": 0}

    def add(self, result: str, what: str) -> None:
        self.counts[result] += 1
        print(f"  {result:<10} {what}")

    def line(self) -> str:
        return ", ".join(f"{n} {k}" for k, n in self.counts.items() if n or k in ("created", "updated", "unchanged", "failed"))


def has_langfuse(python: str) -> bool:
    return subprocess.call([python, "-c", "import langfuse"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def seed(lf: v.Langfuse, env: Dict[str, str], python: str, dry_run: bool,
         runner: Runner = run, langfuse_ok: Optional[Callable[[], bool]] = None) -> Tally:
    t = Tally()
    langfuse_ok = langfuse_ok or (lambda: has_langfuse(python))
    need_pkg = (f"{python} cannot import langfuse: pip install -r demos/text-to-sql/requirements.txt, "
                "or pass --python")

    print("1 · Managed prompts (scripts/seed-app-prompts.py)")
    prompts = load_module("seed_app_prompts", PROMPT_SEEDER)
    plan = prompt_plan(lf, prompt_entries(prompts))
    if not dry_run and any(r != "unchanged" for _, _, r in plan):
        if runner([python, str(PROMPT_SEEDER)], env) != 0:
            t.add("failed", "prompts: scripts/seed-app-prompts.py exited non-zero")
            plan = []
    for name, label, result in plan:
        t.add(result, f"prompt {name} (label {label})")

    print("2 · Code evaluators (scripts/seed-code-evaluators.sh)")
    if dry_run:
        t.add("skipped", "code evaluators: not run in a dry run (the seeder upserts Postgres rows)")
    elif runner([str(EVALUATOR_SEEDER)], env) == 0:
        t.add("ran", "code evaluators: upserted (cloud mode prints UI steps instead)")
    else:
        t.add("failed", "code evaluators: scripts/seed-code-evaluators.sh exited non-zero")

    print("3 · Per-step dataset (seed_step_dataset.py)")
    step = load_module("seed_step_dataset", STEP_SEEDER)
    name = step.DATASET_NAME
    if name not in v.find_datasets(lf, [name]):
        if dry_run:
            t.add("created", f"dataset {name} with {len(step.CURATED_ITEMS)} items (would run the seeder)")
        elif not langfuse_ok():
            t.add("failed", f"dataset {name}: {need_pkg}")
        elif runner([python, str(STEP_SEEDER)], env) == 0:
            t.add("created", f"dataset {name} with {len(step.CURATED_ITEMS)} items")
        else:
            t.add("failed", f"dataset {name}: seed_step_dataset.py exited non-zero")
    else:
        splan = step_plan(step.CURATED_ITEMS, existing_items(lf, name))
        if not dry_run:
            apply_step_plan(lf, name, splan)
        for result, item, _ in splan:
            t.add(result, f"item '{item.input['question'][:60]}' in {name}")

    print("4 · Refine-loop datasets (seed_refine_datasets.py)")
    if dry_run:
        t.add("skipped", "refine datasets: not run in a dry run (the seeder upserts items by stable id)")
    elif not langfuse_ok():
        t.add("failed", f"refine datasets: {need_pkg}")
    elif runner([python, str(REFINE_SEEDER)], env) == 0:
        t.add("ran", "refine datasets: text-to-sql/critic-accuracy and text-to-sql/converged-sql upserted")
    else:
        t.add("failed", "refine datasets: seed_refine_datasets.py exited non-zero")
    return t


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter for the seeders that import langfuse (default: this one)")
    args = ap.parse_args(argv)

    manifest_path = v.DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    env_path = v.env_file(v.manifest_value(manifest, "", "profile"))
    if not v.load_env(env_path):
        print(f"no env file at {env_path}: run ./setup.sh at the repo root", file=sys.stderr)
        return 1
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (base and pk and sk):
        print(f"LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL must be set in {env_path}",
              file=sys.stderr)
        return 1
    lf = v.Langfuse(base, pk, sk)

    print(f"Seeding text-to-sql at {base}" + (" (dry run)" if args.dry_run else ""))
    try:
        tally = seed(lf, child_env(base), args.python, args.dry_run)
    except v.ApiError as e:
        print(f"\nfailed: Langfuse read or write: {e}", file=sys.stderr)
        return 1
    print("\n" + tally.line())
    return 1 if tally.counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
