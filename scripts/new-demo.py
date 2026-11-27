#!/usr/bin/env python3
"""Scaffold a new demo from demos/_template/ (docs/DEMO_STANDARD_SPEC.md §11, deliverable 3).

    python3 scripts/new-demo.py <name> --profile {container,standalone,workshop}
        [--title T] [--pitch P] [--covers trace,evaluate] [--project P] [--target T]
        [--demos-dir demos] [--force] [--no-index]

Copies the template to <demos-dir>/<name>/, fills the tokens below in every file, keeps
the blocks marked for the profile, drops the files the profile does not need, then runs
scripts/gen-demo-index.py so the README's demo card and demos/README.md are current.
Python 3.9+, standard library only.

Tokens the scaffolder fills (any other {{...}} is left for the author to replace):

    {{NAME}}         folder name, kebab-case
    {{TITLE}}        display name (default: the name in title case)
    {{PITCH}}        one sentence, at most 160 characters, no trailing period
    {{PROFILE}}      container | standalone | workshop
    {{COVERS}}       YAML flow list for demo.yaml, such as [trace, evaluate]
    {{TARGET}}       langfuse.target: self-hosted | cloud | either | dual
    {{PROJECT}}      langfuse.project: the Langfuse project the demo writes to
    {{BASE_URL}}     default LANGFUSE_BASE_URL for .env.example
    {{DATE}}         today, for SPEC.md's header
    {{CMD_INSTALL}}  commands.install  (run from the demo folder)
    {{CMD_SEED}}     commands.seed     (repo root for container, else the demo folder)
    {{CMD_RUN}}      commands.run      (same rule as seed)
    {{CMD_VERIFY}}   commands.verify   (same rule as seed)
    {{CMD_TEST}}     commands.test     (run from the demo folder)

Conditional blocks are whole lines, in the comment syntax of the file:

    <!-- @if container standalone -->  ...  <!-- @endif -->     (Markdown)
    # @if workshop                     ...  # @endif            (YAML, .env, Python)

A block is kept when any of its words matches the profile, or the word `evaluation` and
covers includes datasets, experiment or evaluate. Blocks do not nest.
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "demos" / "_template"

PROFILES = ("container", "standalone", "workshop")
TARGETS = ("self-hosted", "cloud", "either", "dual")
COVERS = ("trace", "monitor", "datasets", "experiment", "evaluate", "deploy", "gateway", "governance", "operations")
EVALUATION_COVERS = {"datasets", "experiment", "evaluate"}
ROOT_STACK_PROJECT = "LLM Observability Demo"  # LANGFUSE_INIT_PROJECT_NAME in docker-compose.yaml

# Files the template ships that a profile does not use (§5).
DROP: Dict[str, List[str]] = {
    "container": [".env.example", "SPEC.md", "WORKSHOP_GUIDE.md"],
    "standalone": ["WORKSHOP_GUIDE.md", "Dockerfile"],
    "workshop": ["DEMO_SCRIPT.md", "Dockerfile"],
}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".mypy_cache"}
YAML_QUOTED = {"TITLE", "PITCH", "PROJECT"}  # sit inside "..." in demo.yaml
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IF_RE = re.compile(r"^\s*(?:<!--|#)\s*@if\s+([\w\s-]+?)\s*(?:-->)?\s*$")
ENDIF_RE = re.compile(r"^\s*(?:<!--|#)\s*@endif\s*(?:-->)?\s*$")


class ScaffoldError(Exception):
    pass


def commands(profile: str, name: str) -> Dict[str, str]:
    """commands.* in the profile's working-directory convention (§6)."""
    if profile == "container":
        return {
            "CMD_INSTALL": "pip install -r requirements.txt -r requirements-dev.txt",
            "CMD_SEED": f"python demos/{name}/scripts/seed_all.py",
            "CMD_RUN": f"docker compose --profile demo run --rm {name} python main.py",
            "CMD_VERIFY": f"python demos/{name}/scripts/verify_demo.py",
            "CMD_TEST": "python -m pytest -q",
        }
    return {
        "CMD_INSTALL": "python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt",
        "CMD_SEED": ".venv/bin/python scripts/seed_all.py",
        "CMD_RUN": ".venv/bin/python main.py",
        "CMD_VERIFY": ".venv/bin/python scripts/verify_demo.py",
        "CMD_TEST": ".venv/bin/python -m pytest -q",
    }


def validate_name(name: str) -> str:
    if name.startswith("_"):
        raise ScaffoldError(f"'{name}': names starting with '_' are reserved for non-demo folders")
    if not NAME_RE.match(name):
        raise ScaffoldError(f"'{name}' is not kebab-case (lowercase letters and digits, joined by single hyphens)")
    return name


def validate_pitch(pitch: str) -> str:
    pitch = " ".join(pitch.split()).rstrip(".")
    if len(pitch) > 160:
        raise ScaffoldError(f"pitch is {len(pitch)} characters; the limit is 160")
    return pitch


def parse_covers(raw: str) -> List[str]:
    items = [c.strip() for c in raw.split(",") if c.strip()]
    bad = [c for c in items if c not in COVERS]
    if bad or not items:
        raise ScaffoldError(f"--covers: unknown {bad or 'empty list'}; choose from {', '.join(COVERS)}")
    return sorted(set(items), key=COVERS.index)


def tokens_for(args: argparse.Namespace) -> Dict[str, str]:
    target = args.target or ("self-hosted" if args.profile == "container" else "cloud")
    project = args.project or (ROOT_STACK_PROJECT if args.profile == "container" else args.name)
    covers = parse_covers(args.covers)
    t = {
        "NAME": args.name,
        "TITLE": " ".join(args.title.split()) if args.title else args.name.replace("-", " ").title(),
        "PITCH": validate_pitch(args.pitch) if args.pitch else
                 "{{One-sentence pitch: what the demo shows, 160 characters at most, no trailing period}}",
        "PROFILE": args.profile,
        "COVERS": "[" + ", ".join(covers) + "]",
        "TARGET": target,
        "PROJECT": project,
        "BASE_URL": "http://localhost:3001" if target == "self-hosted" else "https://cloud.langfuse.com",
        "DATE": datetime.date.today().isoformat(),
    }
    t.update(commands(args.profile, args.name))
    t["_conditions"] = " ".join([args.profile] + (["evaluation"] if EVALUATION_COVERS & set(covers) else []))
    return t


def apply_blocks(text: str, conditions: set, where: str) -> str:
    out: List[str] = []
    keep: Optional[bool] = None  # None: outside a block
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        m = IF_RE.match(line)
        if m:
            if keep is not None:
                raise ScaffoldError(f"{where}:{n}: nested @if")
            keep = bool(set(m.group(1).split()) & conditions)
            continue
        if ENDIF_RE.match(line):
            if keep is None:
                raise ScaffoldError(f"{where}:{n}: @endif without @if")
            keep = None
            continue
        if keep is not False:
            out.append(line)
    if keep is not None:
        raise ScaffoldError(f"{where}: @if without @endif")
    return "".join(out)


def fill(text: str, tokens: Dict[str, str], yaml_file: bool) -> str:
    def value(key: str) -> str:
        v = tokens[key]
        if yaml_file and key in YAML_QUOTED:
            v = v.replace("\\", "\\\\").replace('"', '\\"')
        return v

    names = [k for k in tokens if not k.startswith("_")]
    return re.sub(r"\{\{(" + "|".join(map(re.escape, names)) + r")\}\}", lambda m: value(m.group(1)), text)


def render(src: Path, rel: str, tokens: Dict[str, str]) -> str:
    text = apply_blocks(src.read_text(), set(tokens["_conditions"].split()), rel)
    text = fill(text, tokens, yaml_file=src.suffix in (".yaml", ".yml"))
    if src.suffix == ".md":
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def scaffold(args: argparse.Namespace) -> Path:
    validate_name(args.name)
    if not TEMPLATE.is_dir():
        raise ScaffoldError(f"template not found at {TEMPLATE}")
    demos_dir = Path(args.demos_dir).resolve()
    dest = demos_dir / args.name
    if dest.exists() and not args.force:
        raise ScaffoldError(f"{dest} already exists (pass --force to overwrite the template's files in it)")
    tokens = tokens_for(args)
    drop = set(DROP[args.profile])

    for src in sorted(TEMPLATE.rglob("*")):
        rel = src.relative_to(TEMPLATE)
        if src.is_dir() or SKIP_DIRS & set(rel.parts) or rel.as_posix() in drop or src.suffix == ".pyc":
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(src, rel.as_posix(), tokens))
        shutil.copymode(src, out)
        if rel.parts[0] == "scripts" and src.suffix == ".py":
            out.chmod(out.stat().st_mode | 0o111)
    return dest


def run_index(demos_dir: Path) -> None:
    gen = REPO_ROOT / "scripts" / "gen-demo-index.py"
    if not gen.is_file():
        print(f"warning: {gen.relative_to(REPO_ROOT)} not found; run it once it exists to fill the demo card "
              "and the index", file=sys.stderr)
        return
    r = subprocess.run([sys.executable, str(gen), "--demos-dir", str(demos_dir)], cwd=REPO_ROOT)
    if r.returncode:
        print(f"warning: gen-demo-index.py exited {r.returncode}", file=sys.stderr)


def next_steps(dest: Path, args: argparse.Namespace, tokens: Dict[str, str]) -> str:
    rel = dest.relative_to(REPO_ROOT) if REPO_ROOT in dest.parents else dest
    steps = [
        f"Scaffolded {rel} ({args.profile}). Next:",
        "  1. Fill in demo.yaml (pitch, covers, stack, langfuse.project) and confirm the project name.",
    ]
    if args.profile == "container":
        steps.append(f"  2. Add a `{args.name}` service to the root docker-compose.yaml under the `demo` profile "
                     "(env_file: .env, the folder mounted at /app); copy the pattern of support-triage-parallel. "
                     "SPEC.md is optional for container demos: add it and the `spec` add-on if you want one.")
    else:
        steps.append("  2. Write SPEC.md first and confirm it with whoever asked for the demo, before building.")
    steps += [
        "  3. Build: replace answer() in main.py with the real app, and keep TRACE_NAME, EXPECTED_PROMPTS and "
        "EXPECTED_SCORES in scripts/verify_demo.py in step with it. Add seeders to scripts/seed_all.py.",
        f"  4. Run it live, then write README.md from that run: replace every {{{{...}}}} placeholder.",
        "  5. Rehearse once from a clean state, then write "
        + ("WORKSHOP_GUIDE.md" if args.profile == "workshop" else "DEMO_SCRIPT.md")
        + " with the measured minutes; re-check its file:line citations.",
        f"  6. python3 scripts/gen-demo-index.py && python3 scripts/check-demo-structure.py --demo {args.name}",
        "  7. Definition of done: the checker passes with no waivers, verify prints READY on a fresh setup, "
        "tests pass in CI, and the index is regenerated and committed (demos/CONTRIBUTING.md).",
    ]
    if not args.pitch:
        steps.insert(1, "  0. No --pitch given: demo.yaml and README.md carry a pitch placeholder.")
    return "\n".join(steps)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="folder name, kebab-case")
    ap.add_argument("--profile", required=True, choices=PROFILES)
    ap.add_argument("--title", help="display name (default: the name in title case)")
    ap.add_argument("--pitch", help="one sentence, 160 characters at most")
    ap.add_argument("--covers", default="trace", help=f"comma list from: {', '.join(COVERS)} (default: trace)")
    ap.add_argument("--project", help="Langfuse project name (default: the root stack's for container, "
                                      "else the demo name)")
    ap.add_argument("--target", choices=TARGETS, help="langfuse.target (default: self-hosted for container, "
                                                      "else cloud)")
    ap.add_argument("--demos-dir", default=str(REPO_ROOT / "demos"), help="where demos live (default: demos/)")
    ap.add_argument("--force", action="store_true", help="write into an existing folder, overwriting template files")
    ap.add_argument("--no-index", action="store_true", help="skip scripts/gen-demo-index.py")
    args = ap.parse_args(argv)
    try:
        tokens = tokens_for(args)
        dest = scaffold(args)
    except ScaffoldError as e:
        print(f"new-demo: {e}", file=sys.stderr)
        return 2
    if not args.no_index:
        run_index(dest.parent)
    print(next_steps(dest, args, tokens))
    return 0


if __name__ == "__main__":
    sys.exit(main())
