#!/usr/bin/env python3
"""Seed everything query-router needs before it runs. Idempotent, and it reconciles drift.

    python demos/query-router/scripts/seed_all.py              # created / updated / unchanged per item; exit 1 on failure
    python demos/query-router/scripts/seed_all.py --dry-run    # report what would change, write nothing
    python demos/query-router/scripts/seed_all.py --skip-evaluators   # leave the Postgres-seeded evaluators alone

Run it from the repo root. It reads the root `.env` and ignores shell-exported LANGFUSE_*.
It wraps the root seeders this demo uses, without changing them:

1. `scripts/seed-router-prompt.py`: `query-router-classifier`. That script adds two new
   versions on every run, so this one reads its V1 / V2 text and config and adds a version
   only for a label (`baseline`, `production`) whose served text or config differs.
2. `scripts/seed-router-dataset.py`: `query-router-accuracy`. That script appends all 30
   items on every run, so this one reads its ITEMS (parsed, not imported: it needs the
   Langfuse SDK) and creates the missing ones and updates the drifted ones by question.
   Items it does not define, such as misroutes pinned by `score_misroute.py`, are left alone.
3. `scripts/seed-code-evaluators.sh`: the repo's code evaluators, including `route-match`
   on this dataset. Idempotent upsert; self-hosted only (prints UI steps on Cloud).
4. `scripts/seed-router-history.py`: 300 backdated router traces over 14 days, no model
   calls. It appends, so it runs only when either week of the window has fewer than 100
   `route-query` rows. History ages out, so a re-run a week later tops it up.
5. `scripts/seed-router-judge.sh`: the `route-plausibility` judge. Idempotent upsert;
   self-hosted only (prints UI steps on Cloud).

No model calls. Standard library only, like verify_demo.py, whose .env loading and API
client this reuses.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

ROOT_SCRIPTS = v.REPO_ROOT / "scripts"
PROMPT_SEEDER = ROOT_SCRIPTS / "seed-router-prompt.py"
DATASET_SEEDER = ROOT_SCRIPTS / "seed-router-dataset.py"
HISTORY_SEEDER = ROOT_SCRIPTS / "seed-router-history.py"
JUDGE_SEEDER = ROOT_SCRIPTS / "seed-router-judge.sh"
EVALUATOR_SEEDER = ROOT_SCRIPTS / "seed-code-evaluators.sh"
DATASET_DESCRIPTION = ("Router-only accuracy set: raw question -> expected route label. "
                       "Regression-tests ONLY the classifier, independent of specialist quality.")


# --- Definitions, read from the root seeders so there is one source of truth -------------

def load_prompt_seeder(path: Path = PROMPT_SEEDER) -> ModuleType:
    """Import seed-router-prompt.py (stdlib only; main() is not run). It reads
    ROUTER_PROMPT_NAME and ROUTER_MODEL from the environment at import time."""
    spec = importlib.util.spec_from_file_location("seed_router_prompt", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def prompt_specs(mod: ModuleType) -> List[Dict[str, Any]]:
    """One definition per label, as seed-router-prompt.py creates them."""
    return [
        {"name": mod.NAME, "label": "baseline", "type": "text", "prompt": mod.V1, "config": dict(mod.CONFIG),
         "commitMessage": "Baseline router: taxonomy + JSON schema (seed_all reconcile)"},
        {"name": mod.NAME, "label": "production", "type": "text", "prompt": mod.V2, "config": dict(mod.CONFIG),
         "commitMessage": "Few-shot + confidence calibration (seed_all reconcile)"},
    ]


def dataset_items(source: str) -> List[Dict[str, Any]]:
    """The ITEMS list of seed-router-dataset.py, from its source.

    Every Item(...) there takes literal arguments, so the AST gives the definitions without
    importing the module (which exits when the Langfuse SDK is missing).
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == "ITEMS" for t in targets):
            out = []
            for call in node.value.elts:  # type: ignore[union-attr]
                args = [ast.literal_eval(a) for a in call.args]
                kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
                question, route, category = args[:3]
                extra = args[3] if len(args) > 3 else kwargs.get("extra", {})
                out.append({"input": {"question": question},
                            "expected_output": {"route": route},
                            "metadata": {"category": category, "route": route, **extra}})
            return out
    raise ValueError("no ITEMS list found")


def dataset_name() -> str:
    return os.getenv("ROUTER_DATASET_NAME", v.DATASET_NAME)


# --- Reconcilers --------------------------------------------------------------------------

def reconcile_prompt(lf: v.Langfuse, spec: Dict[str, Any], dry_run: bool = False) -> str:
    """Return "created", "updated" or "unchanged" for one prompt label."""
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
            "commitMessage": spec.get("commitMessage"),
        })
    return "created" if current is None else "updated"


def reconcile_dataset(lf: v.Langfuse, name: str, items: List[Dict[str, Any]],
                      dry_run: bool = False) -> Tuple[str, Dict[str, int]]:
    """Ensure the dataset and every defined item exist, matched by question.

    Returns the dataset's own result and per-item counts.
    """
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    try:
        lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(name, safe='')}")
        ds_result = "unchanged"
        existing = lf.pages("/api/public/dataset-items", {"datasetName": name})
    except v.ApiError as e:
        if e.status != 404:
            raise
        ds_result, existing = "created", []
        if not dry_run:
            lf.post("/api/public/v2/datasets", {"name": name, "description": DATASET_DESCRIPTION,
                                                "metadata": {"source": "seed-router-dataset.py"}})
    by_question: Dict[str, dict] = {}
    for row in existing:
        q = (row.get("input") or {}).get("question") if isinstance(row.get("input"), dict) else None
        if q and q not in by_question:
            by_question[q] = row
    for item in items:
        row = by_question.get(item["input"]["question"])
        if row is None:
            result = "created"
        elif (row.get("expectedOutput") == item["expected_output"]
              and (row.get("metadata") or {}) == item["metadata"]
              and (row.get("status") or "ACTIVE") == "ACTIVE"):
            result = "unchanged"
        else:
            result = "updated"
        if result != "unchanged" and not dry_run:
            body = {"datasetName": name, "input": item["input"],
                    "expectedOutput": item["expected_output"], "metadata": item["metadata"]}
            if row is not None:
                body.update(id=row["id"], status="ACTIVE")  # POST with an id upserts that item
            lf.post("/api/public/dataset-items", body)
        counts[result] += 1
    return ds_result, counts


def history_present(lf: v.Langfuse, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Both weeks of the history window hold enough route-query rows for the drift story."""
    now = now or datetime.now(timezone.utc)
    mid, start = now - timedelta(days=v.HISTORY_DAYS / 2), now - timedelta(days=v.HISTORY_DAYS)
    older = lf.observations(cap=v.HISTORY_MIN_PER_WEEK, name=v.ROUTER_OBS,
                            fromStartTime=v.iso(start), toStartTime=v.iso(mid))
    recent = lf.observations(cap=v.HISTORY_MIN_PER_WEEK, name=v.ROUTER_OBS,
                             fromStartTime=v.iso(mid), toStartTime=v.iso(now + timedelta(minutes=5)))
    ok = len(older) >= v.HISTORY_MIN_PER_WEEK and len(recent) >= v.HISTORY_MIN_PER_WEEK
    return ok, f"{len(older)} rows in days 8-14, {len(recent)} in days 1-7 (want {v.HISTORY_MIN_PER_WEEK}+ each)"


def run_seeder(cmd: List[str], dry_run: bool = False) -> str:
    """Run one root seeder from the repo root with the .env-derived environment."""
    if dry_run:
        return "would run"
    proc = subprocess.run(cmd, cwd=str(v.REPO_ROOT), env=dict(os.environ))
    return "reconciled" if proc.returncode == 0 else "failed"


# --- Main ---------------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--skip-evaluators", action="store_true",
                    help="skip seed-code-evaluators.sh and seed-router-judge.sh (Postgres upserts)")
    args = ap.parse_args(argv)

    manifest_path = v.DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    env_path = v.env_file(v.manifest_value(manifest, "", "profile"))
    if not v.load_env(env_path):
        print(f"no env file at {env_path}; run ./setup.sh at the repo root", file=sys.stderr)
        return 1
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (base and pk and sk):
        print(f"LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL must be set in {env_path}",
              file=sys.stderr)
        return 1
    # The root seeders read LANGFUSE_HOST before LANGFUSE_BASE_URL; make both name the same server.
    os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"] = base
    lf = v.Langfuse(base, pk, sk)

    counts = {"created": 0, "updated": 0, "unchanged": 0, "reconciled": 0, "would run": 0, "failed": 0}

    def report(result: str, what: str) -> None:
        counts[result] += 1
        print(f"  {result:<10} {what}")

    print(f"Seeding query-router against {base}" + (" (dry run)" if args.dry_run else ""))

    try:
        for spec in prompt_specs(load_prompt_seeder()):
            report(reconcile_prompt(lf, spec, dry_run=args.dry_run), f"prompt {spec['name']} (label {spec['label']})")
    except (v.ApiError, OSError, AttributeError) as e:
        report("failed", f"prompt from {PROMPT_SEEDER.name}: {e}")

    name = dataset_name()
    try:
        items = dataset_items(DATASET_SEEDER.read_text())
        ds_result, item_counts = reconcile_dataset(lf, name, items, dry_run=args.dry_run)
        report(ds_result, f"dataset {name}")
        for result, n in item_counts.items():
            if n:
                counts[result] += n
                print(f"  {result:<10} {n} item(s) in {name}")
    except (v.ApiError, OSError, ValueError, SyntaxError) as e:
        report("failed", f"dataset {name}: {e}")

    if not args.skip_evaluators:
        report(run_seeder([str(EVALUATOR_SEEDER)], args.dry_run),
               "code evaluators via seed-code-evaluators.sh (route-match on the dataset)")

    try:
        present, detail = history_present(lf)
    except v.ApiError as e:
        report("failed", f"router history: {e}")
    else:
        if present:
            report("unchanged", f"router history: {detail}")
        else:
            report(run_seeder([sys.executable, str(HISTORY_SEEDER)], args.dry_run),
                   f"router history via seed-router-history.py (was {detail})")

    if not args.skip_evaluators:
        report(run_seeder([str(JUDGE_SEEDER)], args.dry_run), "route-plausibility judge via seed-router-judge.sh")

    print("\n" + ", ".join(f"{n} {k}" for k, n in counts.items() if n or k in ("created", "updated", "unchanged", "failed")))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
