#!/usr/bin/env python3
"""Seed everything text-to-sql needs before it runs. Idempotent, and it reconciles drift.

    python demos/text-to-sql/scripts/seed_all.py              # prints created / updated / unchanged per item; exit 1 on failure
    python demos/text-to-sql/scripts/seed_all.py --dry-run    # report what would change, write nothing

Run it from the repo root. It reads the root `.env` (this is a `container` demo) and drops
shell-exported LANGFUSE_* first (verify_demo.runtime_config). It runs the seeders this demo
already has, in this order:

1. `scripts/seed-app-prompts.py` (root): the managed prompts. A prompt is in place when
   the version its label serves is a text prompt with the checked-in text and config.
   This script compares every field first and runs the seeder only when something
   differs, then compares again: a prompt the seeder left different is a failure, never
   reported as updated. An unchanged run writes no version.
2. `scripts/seed-code-evaluators.sh` (root): the code evaluators that score every trace
   at ingest (`chain-gate-check` -> `gate-pass`, `sql-safety-guard` -> `sql-risk`, ...).
   Self-hosted only; it upserts the rows in place. `./setup.sh` also runs it.
3. `seed_step_dataset.py`: the `text-to-sql-analysis-step` dataset and its 8 curated items.
4. `seed_refine_datasets.py`: `text-to-sql/critic-accuracy` and `text-to-sql/converged-sql`.

Steps 3 and 4 run in this process, through dataset_sync.py: only a missing, changed or
archived item is written, and it is written ACTIVE. Every step runs even when an earlier
one failed, and any failure makes the exit code 1. Standard library only.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_refine_datasets  # noqa: E402
import seed_step_dataset  # noqa: E402
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

PROMPT_SEEDER = v.REPO_ROOT / "scripts" / "seed-app-prompts.py"
EVALUATOR_SEEDER = v.REPO_ROOT / "scripts" / "seed-code-evaluators.sh"
STEP_SEEDER = Path(seed_step_dataset.__file__)
REFINE_SEEDER = Path(seed_refine_datasets.__file__)

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
    try:
        return subprocess.call(cmd, cwd=str(v.REPO_ROOT), env=env)
    except OSError as e:
        print(f"  cannot start {cmd[0]}: {e}", file=sys.stderr)
        return 127


def child_env(base: str) -> Dict[str, str]:
    """The environment the subprocess seeders get: the loaded .env, with one Langfuse URL
    under both names. seed-app-prompts.py reads LANGFUSE_HOST, the SDK LANGFUSE_BASE_URL."""
    import os

    env = dict(os.environ)
    env["LANGFUSE_BASE_URL"] = env["LANGFUSE_HOST"] = base
    return env


# --- 1. Prompts ----------------------------------------------------------------------------

PromptSpec = Dict[str, Any]  # name, label, type, prompt, config


def prompt_specs(mod: ModuleType) -> List[PromptSpec]:
    """The complete definition of every prompt the root seeder writes: name, label, type,
    text and config. The seeder writes every prompt as type `text`."""
    out = []
    for entry in mod.PROMPTS:
        name, text, _message, label, config = mod._unpack(entry)
        out.append({"name": name, "label": label, "type": "text", "prompt": text,
                    "config": dict(config or mod.CONFIG)})
    return out


def prompt_drift(served: Optional[dict], spec: PromptSpec, exists: bool = True) -> List[str]:
    """What differs between the version a label serves and the definition: `missing` when
    the prompt has no version at all, `label` when no version carries the label, else any
    of `type`, `text`, `config`. Empty means in place."""
    if served is None:
        return ["label"] if exists else ["missing"]
    drift = []
    if served.get("type") != spec["type"]:
        drift.append("type")
    if served.get("prompt") != spec["prompt"]:       # exact, as the seeder compares it
        drift.append("text")
    if (served.get("config") or {}) != spec["config"]:
        drift.append("config")
    if spec["label"] not in (served.get("labels") or [spec["label"]]):
        drift.append("label")
    return drift


def prompt_plan(lf: Any, specs: List[PromptSpec]) -> List[Tuple[PromptSpec, List[str]]]:
    """Each definition with its drift, read through the label. Only a 404 means missing;
    any other read error propagates."""
    plan = []
    for spec in specs:
        exists = True
        try:
            served = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(spec['name'], safe='')}",
                            label=spec["label"])
        except v.ApiError as e:
            if e.status != 404:
                raise
            served = None
            listed = lf.get("/api/public/v2/prompts", name=spec["name"], limit=1) or {}
            exists = any(p.get("name") == spec["name"] for p in listed.get("data") or [])
        plan.append((spec, prompt_drift(served, spec, exists)))
    return plan


def seed_prompts(lf: Any, specs: List[PromptSpec], env: Dict[str, str], dry_run: bool,
                 runner: Runner, report: Callable[[str, str], None]) -> None:
    before = prompt_plan(lf, specs)
    after = before
    if not dry_run and any(d for _, d in before):
        if runner([sys.executable, str(PROMPT_SEEDER)], env) != 0:
            report("failed", "prompts: scripts/seed-app-prompts.py exited non-zero")
        after = prompt_plan(lf, specs)
    for (spec, was), (_, now) in zip(before, after):
        what = f"prompt {spec['name']} (label {spec['label']})"
        if not was:
            report("unchanged", what)
        elif now and not dry_run:
            report("failed", f"{what}: {', '.join(now)} still differ after scripts/seed-app-prompts.py")
        elif was == ["missing"]:
            report("created", what)
        else:
            report("updated", what + (": label added" if was == ["label"] else f": {', '.join(was)}"))


# --- Main ----------------------------------------------------------------------------------

class Tally:
    def __init__(self) -> None:
        self.counts = {"created": 0, "updated": 0, "unchanged": 0, "ran": 0, "skipped": 0, "failed": 0}

    def add(self, result: str, what: str) -> None:
        self.counts[result] += 1
        print(f"  {result:<10} {what}")

    def line(self) -> str:
        return ", ".join(f"{n} {k}" for k, n in self.counts.items()
                         if n or k in ("created", "updated", "unchanged", "failed"))


def seed(lf: Any, env: Dict[str, str], dry_run: bool, runner: Runner = run,
         prompt_module: Optional[ModuleType] = None) -> Tally:
    t = Tally()

    def step(title: str, fn: Callable[[], None]) -> None:
        print(title)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - any failure is this step's, the rest still run
            t.add("failed", f"{title.split('·', 1)[-1].strip()}: {type(e).__name__}: {e}")

    def prompts() -> None:
        mod = prompt_module or load_module("seed_app_prompts", PROMPT_SEEDER)
        seed_prompts(lf, prompt_specs(mod), env, dry_run, runner, t.add)

    def evaluators() -> None:
        if dry_run:
            t.add("skipped", "code evaluators: not run in a dry run (the seeder upserts Postgres rows)")
        elif runner([str(EVALUATOR_SEEDER)], env) == 0:
            t.add("ran", "code evaluators: upserted in place (cloud mode prints UI steps instead)")
        else:
            t.add("failed", "code evaluators: scripts/seed-code-evaluators.sh exited non-zero")

    step("1 · Managed prompts (scripts/seed-app-prompts.py)", prompts)
    step("2 · Code evaluators (scripts/seed-code-evaluators.sh)", evaluators)
    step("3 · Per-step dataset (seed_step_dataset.py)", lambda: seed_step_dataset.seed(lf, dry_run, report=t.add))
    step("4 · Refine-loop datasets (seed_refine_datasets.py)", lambda: seed_refine_datasets.seed(lf, dry_run, t.add))
    return t


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    cfg = v.runtime_config()
    if cfg.error:
        print(cfg.error, file=sys.stderr)
        return 1
    lf = v.Langfuse(cfg.base, cfg.public_key, cfg.secret_key)
    print(f"Seeding text-to-sql at {cfg.base}" + (" (dry run)" if args.dry_run else ""))
    tally = seed(lf, child_env(cfg.base), args.dry_run)
    print("\n" + tally.line())
    return 1 if tally.counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
