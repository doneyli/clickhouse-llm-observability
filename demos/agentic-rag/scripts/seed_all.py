#!/usr/bin/env python3
"""Seed everything agentic-rag needs before it runs. Idempotent, and it reconciles drift.

    python demos/agentic-rag/scripts/seed_all.py                   # from the repo root; exit 1 on failure
    python demos/agentic-rag/scripts/seed_all.py --dry-run         # report what would change, write nothing
    python demos/agentic-rag/scripts/seed_all.py --skip-ingest     # keep the vector index as it is
    python demos/agentic-rag/scripts/seed_all.py --skip-librechat  # no LibreChat (Act 4) agent

A wrapper over the seeders this demo already has, in pre-flight order:

1. Vector index: `ingest.py` in the agentic-rag container. It truncates and re-inserts the
   shared vector-rag corpus into `agentic_rag.kb_chunks`, so a re-run is safe and picks up
   corpus changes. Loads the embedding model; no model calls. A content fingerprint of the
   table before and after tells created, updated and unchanged apart.
2. Generation prompt `agentic-rag-generation`: reconciled here, with the text and config
   of `scripts/seed-langfuse-prompt.py`. That script appends two versions on every run;
   this adds the baseline only when the prompt does not exist, and a new `production`
   version only when the text or config it serves differs.
3. Managed judges: `scripts/seed-agentic-rag-evaluators.sh`, a Postgres upsert keyed on
   the rule id, so it updates a drifted rule in place. Self-hosted only.
4. LibreChat agents: `scripts/seed-librechat-agents.sh`, which creates missing agents and
   reconciles the model, MCP tools and instructions of existing ones. It seeds all five
   repo agents; this demo uses "Agentic RAG Assistant". `./setup.sh` already runs it.

Steps 3 and 4 exit non-zero when anything they own is left missing or incomplete, and
any failed step makes this script exit 1.

Standard library only, like verify_demo.py, whose .env loading and API client this reuses.
Makes no model calls.
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
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

INGEST_CMD = ["docker", "compose", "--profile", "demo", "run", "--rm", "agentic-rag", "python", "ingest.py"]
JUDGES_CMD = ["./scripts/seed-agentic-rag-evaluators.sh"]
LIBRECHAT_CMD = ["./scripts/seed-librechat-agents.sh"]
PROMPT_SEEDER = v.REPO_ROOT / "scripts" / "seed-langfuse-prompt.py"
PRODUCTION = "production"
BASELINE_MESSAGE = "Baseline RAG generation prompt"
PRODUCTION_MESSAGE = "Add grounding rules + source citation; promote to production"


def prompt_definitions() -> ModuleType:
    """Load NAME, V1, V2 and CONFIG from scripts/seed-langfuse-prompt.py (one source of truth).

    Importing it makes no request. Load it after the .env, since CONFIG reads ANTHROPIC_MODEL.
    """
    spec = importlib.util.spec_from_file_location("seed_langfuse_prompt", PROMPT_SEEDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def reconcile_prompt(lf: v.Langfuse, name: str, baseline: str, production: str,
                     config: Dict[str, Any], dry_run: bool = False) -> str:
    """Return "created", "updated" or "unchanged" for the generation prompt."""
    try:
        current: Optional[Dict[str, Any]] = lf.get(
            f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=PRODUCTION)
    except v.ApiError as e:
        if e.status != 404:
            raise
        current = None  # no version carries the label yet
    if current and (current.get("type"), current.get("prompt"), current.get("config") or {}) == (
            "text", production, config):
        return "unchanged"
    exists = current is not None or bool((lf.get("/api/public/v2/prompts", name=name) or {}).get("data"))
    if not dry_run:
        if not exists:  # Act 3b shows a baseline next to the production version
            lf.post("/api/public/v2/prompts", {"name": name, "type": "text", "prompt": baseline,
                                               "labels": [], "config": config, "commitMessage": BASELINE_MESSAGE})
        lf.post("/api/public/v2/prompts", {"name": name, "type": "text", "prompt": production,
                                           "labels": [PRODUCTION], "config": config,
                                           "commitMessage": PRODUCTION_MESSAGE})
    return "updated" if exists else "created"


def run_step(cmd: List[str], dry_run: bool) -> str:
    """Run one existing seeder from the repo root, its output passed through."""
    shown = " ".join(cmd)
    if dry_run:
        print(f"    would run: {shown}")
        return "skipped"
    print(f"    running: {shown}", flush=True)
    try:
        code = subprocess.run(cmd, cwd=v.REPO_ROOT).returncode
    except OSError as e:
        print(f"    {type(e).__name__}: {e}")
        return "failed"
    return "reconciled" if code == 0 else "failed"


def chunk_state(env: Dict[str, str]) -> Optional[Tuple[int, str]]:
    """(rows, content fingerprint) of the vector table, or None when it is not there.

    The fingerprint hashes every row's id, title and chunk text, so a re-ingest of an
    unchanged corpus reads back the same value and is reported as unchanged.
    """
    base = f"http://localhost:{env.get('CLICKHOUSE_VECTORS_HTTP_PORT') or 8125}"
    try:
        out = v.ch_query(base, env.get("CH_VECTORS_USER") or "agentic",
                         env.get("CH_VECTORS_PASSWORD") or "agentic123",
                         "SELECT count(), groupBitXor(cityHash64(id, doc_title, chunk)) "
                         f"FROM {v.VECTORS_DB}.{v.VECTORS_TABLE}")
        rows, fingerprint = out.split("\t")
        return int(rows), fingerprint.strip()
    except (v.ApiError, ValueError):
        return None


def ingest_result(step: str, before: Optional[Tuple[int, str]], after: Optional[Tuple[int, str]]) -> str:
    """What the ingest step did, from the step's result and the table before and after."""
    if step != "reconciled":
        return step  # failed, or skipped in a dry run
    if not after or not after[0]:
        return "failed"  # the step exited 0 but the table is missing or empty
    if not before or not before[0]:
        return "created"
    return "unchanged" if before == after else "updated"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--skip-ingest", action="store_true", help="do not rebuild the vector index")
    ap.add_argument("--skip-librechat", action="store_true", help="do not seed the LibreChat agents (Act 4)")
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
    cloud = os.environ.get("DEPLOY_MODE") == "cloud"

    counts = {k: 0 for k in ("created", "updated", "unchanged", "reconciled", "skipped", "failed")}

    def record(result: str, what: str) -> None:
        counts[result] += 1
        print(f"  {result:<10} {what}")

    print(f"Seeding agentic-rag against {base}" + (" (dry run)" if args.dry_run else ""))

    print("1. Vector index (ingest.py)")
    if args.skip_ingest:
        record("skipped", "vector index (--skip-ingest)")
    else:
        before = chunk_state(dict(os.environ))
        step = run_step(INGEST_CMD, args.dry_run)
        after = chunk_state(dict(os.environ)) if not args.dry_run else before
        rows = (before or (0, ""))[0], (after or (0, ""))[0]
        record(ingest_result(step, before, after),
               f"{v.VECTORS_DB}.{v.VECTORS_TABLE}: {rows[0]} -> {rows[1]} chunks"
               if not args.dry_run else f"{v.VECTORS_DB}.{v.VECTORS_TABLE} ({rows[0]} chunks now)")

    print("2. Generation prompt")
    name = os.environ.get("GEN_PROMPT_NAME") or next(iter(v.EXPECTED_PROMPTS))
    try:
        defs = prompt_definitions()
        name = defs.NAME
        result = reconcile_prompt(lf, defs.NAME, defs.V1, defs.V2, defs.CONFIG, dry_run=args.dry_run)
    except (v.ApiError, OSError, AttributeError, SyntaxError) as e:
        result = "failed"
        print(f"    {type(e).__name__}: {e}")
    record(result, f"prompt {name} (label {PRODUCTION})")

    print("3. Managed judges (seed-agentic-rag-evaluators.sh)")
    if cloud:
        record("skipped", "judges: DEPLOY_MODE=cloud, create them in the UI (the script prints how)")
    else:
        record(run_step(JUDGES_CMD, args.dry_run), f"judge rules {', '.join(v.JUDGE_RULES)}")

    print("4. LibreChat agents (seed-librechat-agents.sh)")
    if args.skip_librechat:
        record("skipped", "LibreChat agents (--skip-librechat)")
    else:
        record(run_step(LIBRECHAT_CMD, args.dry_run), "LibreChat agents, incl. Agentic RAG Assistant")

    print("\n" + ", ".join(f"{n} {k}" for k, n in counts.items() if n))
    if not counts["failed"] and not args.dry_run:
        print("Next: python demos/agentic-rag/scripts/verify_demo.py")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
