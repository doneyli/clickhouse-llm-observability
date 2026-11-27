#!/usr/bin/env python3
"""Seed everything query-router needs before it runs. Idempotent, and it reconciles drift.

    python demos/query-router/scripts/seed_all.py                     # created / updated / unchanged per item; exit 1 on any failure
    python demos/query-router/scripts/seed_all.py --dry-run           # report what would change, write nothing
    python demos/query-router/scripts/seed_all.py --skip-evaluators   # leave the Postgres-seeded evaluators alone

Run it from the repo root. It reads the root `.env` and ignores shell-exported LANGFUSE_*.
It wraps the root seeders this demo uses:

1. `scripts/seed-router-prompt.py`: `query-router-classifier`. That script adds two new
   versions on every run, so this one reads its V1 / V2 text and config and adds a version
   only for a label (`baseline`, `production`) whose served type, text or config differs.
2. `scripts/seed-router-dataset.py`: `query-router-accuracy`. That script appends all 30
   items on every run, so this one reads its ITEMS (parsed, not imported: it needs the
   Langfuse SDK), reconciles the dataset's description and metadata, and creates missing
   items with a stable id (`qra-<sha1 of the question>`) and updates drifted or archived
   ones in place, matched by that id or by question. Items it does not define, such as
   misroutes pinned by `score_misroute.py`, are left alone.
3. `scripts/seed-code-evaluators.sh`: the repo's code evaluators, including `route-match`
   on this dataset. Idempotent upsert; self-hosted only.
4. The Router Ops dashboard (Act 4) and its four widgets, through the (unstable)
   dashboards API, as defined in verify_demo.WIDGETS: created when missing, patched when a
   definition drifted, placed when not on the dashboard.
5. `scripts/seed-router-history.py`: 300 backdated router traces over 14 days, no model
   calls. It appends, so it runs only when either week of the window has fewer than 100
   `route-query` rows, and then this script reads the window back (bounded wait) before it
   reports the history as created. A failed batch or a rejected event exits non-zero.
6. `scripts/seed-router-judge.sh`: the `route-plausibility` judge. Idempotent upsert;
   self-hosted only.

With DEPLOY_MODE=cloud the two Postgres seeders cannot run; they are reported as
`skipped` (create them in the UI) rather than as seeded. No model calls. Standard library
only, like verify_demo.py, whose .env loading, API client and definitions this reuses.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

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
DATASET_METADATA = {"source": "seed-router-dataset.py"}
HISTORY_WAIT_S = 120          # ingestion is asynchronous: how long to wait for history to be readable
HISTORY_POLL_S = 10
U = "/api/public/unstable"
RESULTS = ("created", "updated", "unchanged", "reconciled", "skipped", "would run", "failed")


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


def item_id(question: str) -> str:
    """A stable id per seeded question, so a re-run finds the item it created."""
    return "qra-" + hashlib.sha1(question.encode()).hexdigest()[:16]


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
                out.append({"id": item_id(question), "input": {"question": question},
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
            spec["type"], spec["prompt"], spec.get("config") or {}) and spec["label"] in (current.get("labels") or []):
        return "unchanged"
    if not dry_run:
        lf.post("/api/public/v2/prompts", {
            "name": spec["name"], "type": spec["type"], "prompt": spec["prompt"],
            "config": spec.get("config") or {}, "labels": [spec["label"]],
            "commitMessage": spec.get("commitMessage"),
        })
    return "created" if current is None else "updated"


def reconcile_dataset(lf: v.Langfuse, name: str, items: List[Dict[str, Any]],
                      dry_run: bool = False) -> Tuple[str, Dict[str, int], List[str]]:
    """Ensure the dataset definition and every defined item exist and match.

    Returns the dataset's own result, per-item counts (created / updated / unchanged /
    failed) and one message per failed item. A failed item does not stop the others.
    """
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    errors: List[str] = []
    want = {"name": name, "description": DATASET_DESCRIPTION, "metadata": DATASET_METADATA}
    try:
        ds = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(name, safe='')}") or {}
    except v.ApiError as e:
        if e.status != 404:
            raise
        ds = None
    if ds is None:
        ds_result, existing = "created", []
    else:
        same = ds.get("description") == DATASET_DESCRIPTION and (ds.get("metadata") or {}) == DATASET_METADATA
        ds_result = "unchanged" if same else "updated"
        existing = lf.pages("/api/public/dataset-items", {"datasetName": name})
    if ds_result != "unchanged" and not dry_run:
        lf.post("/api/public/v2/datasets", want)  # creates, or updates the definition by name

    by_id = {row.get("id"): row for row in existing}
    by_question: Dict[str, dict] = {}
    for row in existing:
        q = row.get("input", {}).get("question") if isinstance(row.get("input"), dict) else None
        if q and q not in by_question:
            by_question[q] = row
    for item in items:
        row = by_id.get(item["id"]) or by_question.get(item["input"]["question"])
        if row is None:
            result = "created"
        elif (row.get("input") == item["input"] and row.get("expectedOutput") == item["expected_output"]
              and (row.get("metadata") or {}) == item["metadata"] and (row.get("status") or "ACTIVE") == "ACTIVE"):
            result = "unchanged"
        else:
            result = "updated"
        if result != "unchanged" and not dry_run:
            body = {"datasetName": name, "id": row["id"] if row else item["id"], "input": item["input"],
                    "expectedOutput": item["expected_output"], "metadata": item["metadata"], "status": "ACTIVE"}
            try:
                lf.post("/api/public/dataset-items", body)  # POST with an id upserts that item
            except v.ApiError as e:
                counts["failed"] += 1
                errors.append(f"item {item['input']['question'][:50]!r}: {e}")
                continue
        counts[result] += 1
    return ds_result, counts, errors


def reconcile_dashboard(lf: v.Langfuse, dry_run: bool = False) -> List[Tuple[str, str]]:
    """The Router Ops dashboard and its widgets. Returns (result, what) per artifact."""
    out: List[Tuple[str, str]] = []
    dash = v.find_dashboard(lf)
    if dash is None:
        if dry_run:
            return [("created", f"dashboard {v.DASHBOARD_NAME}")] + [
                ("created", f"widget {w['name']}") for w in v.WIDGETS]
        dash = lf.post(f"{U}/dashboards", {"name": v.DASHBOARD_NAME, "description": v.DASHBOARD_DESCRIPTION})
        out.append(("created", f"dashboard {v.DASHBOARD_NAME}"))
    elif dash.get("description") != v.DASHBOARD_DESCRIPTION:
        if not dry_run:
            lf.patch(f"{U}/dashboards/{dash['id']}", {"name": v.DASHBOARD_NAME, "description": v.DASHBOARD_DESCRIPTION})
        out.append(("updated", f"dashboard {v.DASHBOARD_NAME}"))
    else:
        out.append(("unchanged", f"dashboard {v.DASHBOARD_NAME}"))
    placed = set(v.placed_widget_ids(lf.get(f"{U}/dashboards/{dash['id']}") or {}))
    library = {w.get("name"): w for w in lf.pages(f"{U}/dashboard-widgets", {})}
    for spec in v.WIDGETS:
        body = {k: spec[k] for k in ("name",) + v.WIDGET_FIELDS}
        body["chartConfig"] = {"type": spec["chartType"]}
        try:
            stored = library.get(spec["name"])
            if stored is None:
                result = "created"
                stored = {} if dry_run else lf.post(f"{U}/dashboard-widgets", body)
            elif v.widget_drift(spec, stored):
                result = "updated"
                if not dry_run:
                    lf.patch(f"{U}/dashboard-widgets/{stored['id']}", body)
            else:
                result = "unchanged"
            if stored.get("id") not in placed:
                result = "updated" if result == "unchanged" else result
                if not dry_run:
                    lf.post(f"{U}/dashboards/{dash['id']}/placements", {
                        "type": "widget", "widgetId": stored["id"], "x": spec["x"], "y": spec["y"],
                        "width": spec["width"], "height": spec["height"]})
        except v.ApiError as e:  # one bad widget must not hide the others
            out.append(("failed", f"widget {spec['name']}: {e}"))
            continue
        out.append((result, f"widget {spec['name']}"))
    return out


def history_present(lf: v.Langfuse, now: Optional[datetime] = None) -> Tuple[bool, str]:
    return v.history_counts(lf, now or datetime.now(timezone.utc))


def wait_for_history(lf: v.Langfuse, sleep: Callable[[float], None] = time.sleep,
                     wait_s: float = HISTORY_WAIT_S, poll_s: float = HISTORY_POLL_S) -> Tuple[bool, str]:
    """Read the history window back until both weeks are there, for at most `wait_s`."""
    waited = 0.0
    while True:
        ok, detail = history_present(lf)
        if ok or waited >= wait_s:
            return ok, detail
        sleep(poll_s)
        waited += poll_s


def run_seeder(cmd: List[str], dry_run: bool = False) -> str:
    """Run one root seeder from the repo root with the .env-derived environment."""
    if dry_run:
        return "would run"
    try:
        proc = subprocess.run(cmd, cwd=str(v.REPO_ROOT), env=dict(os.environ))
    except OSError as e:
        print(f"  could not start {cmd[-1]}: {e}", file=sys.stderr)
        return "failed"
    return "reconciled" if proc.returncode == 0 else "failed"


# --- Main ---------------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None, sleep: Callable[[float], None] = time.sleep) -> int:
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
    cloud = os.environ.get("DEPLOY_MODE", "self-hosted") == "cloud"
    lf = v.Langfuse(base, pk, sk)

    counts = {k: 0 for k in RESULTS}

    def report(result: str, what: str) -> None:
        counts[result] += 1
        print(f"  {result:<10} {what}")

    def postgres_seeder(script: Path, what: str) -> None:
        if args.skip_evaluators:
            return
        if cloud:
            report("skipped", f"{what}: DEPLOY_MODE=cloud, create it in the Langfuse UI ({script.name} prints the steps)")
            return
        report(run_seeder([str(script)], args.dry_run), f"{what} via {script.name}")

    print(f"Seeding query-router against {base}" + (" (dry run)" if args.dry_run else ""))
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except v.ApiError as e:
        print(f"  failed     keys do not authenticate against {base}: {e}", file=sys.stderr)
        return 1
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or v.manifest_value(manifest, "langfuse", "project")
    got = (projects[0] if projects else {}).get("name")
    if got != expected:
        print(f"  failed     keys map to project {got!r}, not {expected!r}; nothing written", file=sys.stderr)
        return 1

    try:
        specs = prompt_specs(load_prompt_seeder())
    except (OSError, AttributeError, SyntaxError) as e:
        report("failed", f"prompt definitions from {PROMPT_SEEDER.name}: {e}")
        specs = []
    for spec in specs:
        try:
            report(reconcile_prompt(lf, spec, dry_run=args.dry_run), f"prompt {spec['name']} (label {spec['label']})")
        except v.ApiError as e:
            report("failed", f"prompt {spec['name']} (label {spec['label']}): {e}")

    name = dataset_name()
    try:
        items = dataset_items(DATASET_SEEDER.read_text())
        ds_result, item_counts, errors = reconcile_dataset(lf, name, items, dry_run=args.dry_run)
        report(ds_result, f"dataset {name}")
        for result, n in item_counts.items():
            if n:
                counts[result] += n
                print(f"  {result:<10} {n} item(s) in {name}")
        for err in errors:
            print(f"    {err}")
    except (v.ApiError, OSError, ValueError, SyntaxError) as e:
        report("failed", f"dataset {name}: {e}")

    postgres_seeder(EVALUATOR_SEEDER, "code evaluators (route-match on the dataset)")

    try:
        for result, what in reconcile_dashboard(lf, dry_run=args.dry_run):
            report(result, what)
    except v.ApiError as e:
        report("failed", f"dashboard {v.DASHBOARD_NAME}: {e}"
               + (" (this server has no dashboards API; build it by hand, see DEMO_SCRIPT Act 4)"
                  if e.status == 404 else ""))

    try:
        present, detail = history_present(lf)
        if present:
            report("unchanged", f"router history: {detail}")
        elif args.dry_run:
            report("would run", f"router history via {HISTORY_SEEDER.name} (now {detail})")
        elif run_seeder([sys.executable, str(HISTORY_SEEDER)]) == "failed":
            report("failed", f"router history: {HISTORY_SEEDER.name} exited non-zero (a batch failed or events were rejected)")
        else:
            landed, after = wait_for_history(lf, sleep=sleep)
            report("created" if landed else "failed",
                   f"router history: {after}" + ("" if landed else f", still short after {HISTORY_WAIT_S}s; is langfuse-worker processing?"))
    except v.ApiError as e:
        report("failed", f"router history: {e}")

    postgres_seeder(JUDGE_SEEDER, "route-plausibility judge")

    print("\n" + ", ".join(f"{n} {k}" for k, n in counts.items() if n or k in ("created", "updated", "unchanged", "failed")))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
