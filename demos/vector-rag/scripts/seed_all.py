#!/usr/bin/env python3
"""Seed what vector-rag needs: the `vector-rag-generation` prompt under `production`.

Run from the repo root:

    python demos/vector-rag/scripts/seed_all.py             # created / updated / unchanged; exit 1 on failure
    python demos/vector-rag/scripts/seed_all.py --dry-run   # report what would change, write nothing

A thin wrapper over the root helper `scripts/seed-app-prompts.py`, which does the
reconciling (type, text, config and label; a new version only when one differs). This
wrapper supplies what the helper does not: it drops shell-exported LANGFUSE_* (a key for
another project would otherwise seed there), loads the root stack's .env, which is the
only source of keys for a container demo, checks the keys and URL are there before any
request, and runs the helper from the repo root for this demo's prompt only, so the
text-to-sql prompts in the same project are left alone. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only: .env and manifest parsing)

HELPER = v.REPO_ROOT / "scripts" / "seed-app-prompts.py"
PROMPT_NAMES = list(v.EXPECTED_PROMPTS)  # what verify_demo checks is exactly what this seeds


def child_env(environ: Dict[str, str]) -> Dict[str, str]:
    """The helper's environment: the loaded .env, with one normalised Langfuse URL under both
    names. The helper reads LANGFUSE_BASE_URL first, then LANGFUSE_HOST."""
    env = dict(environ)
    base = (env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "").strip().rstrip("/")
    env["LANGFUSE_BASE_URL"] = env["LANGFUSE_HOST"] = base
    return env


def helper_command(python: str, dry_run: bool) -> List[str]:
    cmd = [python, str(HELPER)]
    for name in PROMPT_NAMES:
        cmd += ["--only", name]
    return cmd + (["--dry-run"] if dry_run else [])


def main(argv: Optional[List[str]] = None, run=subprocess.call) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    manifest_path = v.DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    profile = v.manifest_value(manifest, "", "profile")
    if profile != "container":
        print(f"{manifest_path} must declare profile: container (found {profile!r}); "
              "only a container demo seeds from the root .env", file=sys.stderr)
        return 1
    env_path = v.env_file(profile)
    if not v.load_env(env_path):  # drops shell LANGFUSE_* even when the file is missing
        print(f"no env file at {env_path}: run ./setup.sh at the repo root", file=sys.stderr)
        return 1
    env = child_env(dict(os.environ))
    missing = [k for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not env.get(k)]
    if not env["LANGFUSE_BASE_URL"]:
        missing.append("LANGFUSE_BASE_URL (or LANGFUSE_HOST)")
    if missing:
        print(f"{', '.join(missing)} not set in {env_path}", file=sys.stderr)
        return 1
    if not HELPER.is_file():
        print(f"missing {HELPER}", file=sys.stderr)
        return 1

    print(f"vector-rag seed: {', '.join(PROMPT_NAMES)} at {env['LANGFUSE_BASE_URL']} (keys from {env_path})")
    try:
        code = run(helper_command(sys.executable, args.dry_run), cwd=str(v.REPO_ROOT), env=env)
    except OSError as e:
        print(f"could not run {HELPER.name}: {e}", file=sys.stderr)
        return 1
    if code != 0:
        print(f"{HELPER.name} exited {code}: not seeded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
