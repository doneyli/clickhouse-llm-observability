#!/usr/bin/env python3
"""Check every demo under demos/ against the demo standard (demos/CONTRIBUTING.md).

Usage:
  python scripts/check-demo-structure.py                    # every demo
  python scripts/check-demo-structure.py --demo real-estate # one demo
  python scripts/check-demo-structure.py --strict           # ignore waivers, for an audit
  python scripts/check-demo-structure.py --format github    # CI annotations; also text, json
  python scripts/check-demo-structure.py --write-waivers    # bootstrap: waive every current failure

Exits 1 on any failure that is not waived. A waiver with an `until` date in the
past counts as a failure. DS-LINK-02 is a warning only.

Python 3.9+ and PyYAML only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_standard as ds  # noqa: E402

BOOTSTRAP_REASON = "predates the standard"


@dataclass
class Finding:
    rule: str
    demo: str
    message: str
    file: str = ""
    line: int = 0
    level: str = "error"  # error | warning | notice


class DemoCheck:
    def __init__(self, demo: ds.Demo, repo_root: Path):
        self.demo = demo
        self.root = repo_root
        self.findings: list[Finding] = []

    # -- helpers -----------------------------------------------------------------

    def rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def fail(self, rule: str, message: str, path: Path | None = None, line: int = 0,
             level: str = "error") -> None:
        self.findings.append(Finding(rule, self.demo.name, message,
                                     self.rel(path) if path else self.rel(self.demo.path), line, level))

    def read(self, name: str) -> str | None:
        p = self.demo.path / name
        return p.read_text(errors="replace") if p.is_file() else None

    @property
    def m(self) -> dict:
        return self.demo.manifest or {}

    def workdir(self, command_key: str) -> Path:
        """§6: seed/run/verify/reset run from the repo root for container demos."""
        if self.demo.profile == "container" and command_key in ("seed", "run", "verify", "reset"):
            return self.root
        return self.demo.path

    def command_script(self, command_key: str) -> tuple[str | None, str | None]:
        """(script path the command names, problem). Both None when nothing to check."""
        cmd = (self.m.get("commands") or {}).get(command_key)
        if not cmd:
            return None, None
        try:
            tokens = shlex.split(cmd, comments=True)
        except ValueError:
            tokens = cmd.split()
        tokens = [t for t in tokens if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)]
        base = self.workdir(command_key)
        for i, tok in enumerate(tokens):
            if tok in ("&&", "||", ";", "|"):
                continue
            if "/bin/" in tok or tok.startswith((".venv", "venv/", "node_modules", "-")):
                continue
            if re.search(r"\.(py|sh|bash|ts|mjs|cjs|js)$", tok) or tok.startswith("./"):
                return tok, None if (base / tok).exists() else f"`{tok}` does not exist (from {'the repo root' if base == self.root else self.rel(base)})"
            if tok == "npm" and i + 1 < len(tokens):
                script = tokens[i + 2] if tokens[i + 1] == "run" and i + 2 < len(tokens) else tokens[i + 1]
                pkg = self.demo.path / "package.json"
                try:
                    scripts = json.loads(pkg.read_text()).get("scripts", {})
                except (OSError, ValueError):
                    scripts = {}
                ok = script in scripts
                return f"npm run {script}", None if ok else f"package.json has no `{script}` script"
        return None, None

    # -- rules -------------------------------------------------------------------

    def run(self) -> list[Finding]:
        d = self.demo
        if not d.valid:
            for err in d.manifest_errors:
                self.fail("DS-MANIFEST-01", err, d.path / "demo.yaml")
            if d.manifest is None:
                return self.findings
        if d.name != self.m.get("name"):
            self.fail("DS-MANIFEST-02", f"name is '{self.m.get('name')}' but the folder is '{d.name}'",
                      d.path / "demo.yaml")
        if not ds.KEBAB.match(d.name):
            self.fail("DS-MANIFEST-02", f"folder name '{d.name}' is not kebab-case")
        if not d.valid:
            return self.findings  # later rules depend on a well-formed manifest

        self.check_readme()
        if d.profile == "workshop":
            self.check_workshop_guide()
        else:
            for f in d.script_files():
                self.check_script(f)
        self.check_env()
        self.check_commands()
        self.check_spec_and_addons()
        self.check_links()
        return self.findings

    def check_readme(self) -> None:
        path = self.demo.path / "README.md"
        text = self.read("README.md")
        if text is None:
            self.fail("DS-README-01", "README.md is missing", path)
            return
        head, secs = ds.sections(text)
        workshop = self.demo.profile == "workshop"
        evaluation_required = bool(set(self.m.get("covers") or []) & {"datasets", "experiment", "evaluate"})
        standard = [
            (["What it demonstrates"], True),
            (["Who it's for"], True),
            (["What runs where"], True),
            (["Set up from scratch"], True),
            (["Before a demo", "Before a session"] if workshop else ["Before a demo"], True),
            (["Run it"], True),
            (["Evaluation"], evaluation_required),
            (["Configuration"], True),
            (["Layout"], True),
            (["Tests"], True),
            (["Troubleshooting"], True),
            (["Known limits"], True),
        ]
        run_it, evaluation = 5, 6

        def slot(title: str) -> int | None:
            for i, (names, _) in enumerate(standard):
                if any(ds.heading_is(title, n) for n in names):
                    return i
            return None

        found = [(s, slot(s.title)) for s in secs]
        present = {i for _, i in found if i is not None}
        missing = [names[0] for i, (names, req) in enumerate(standard) if req and i not in present]
        if missing:
            self.fail("DS-README-02", "missing sections: " + ", ".join(f"'## {m}'" for m in missing), path)

        last, prev_std = -1, None
        for sec, i in found:
            if i is None:
                if prev_std not in (run_it, evaluation):
                    self.fail("DS-README-03", f"'## {sec.title}' is not a standard section; demo-specific "
                              "sections go between '## Run it' and '## Configuration'", path, sec.line)
                continue
            if i <= last:
                self.fail("DS-README-03", f"'## {sec.title}' is out of order (see the README standard)",
                          path, sec.line)
            last, prev_std = max(last, i), i

        block = ds.read_block(text, ds.CARD_START, ds.CARD_END)
        if block is None:
            self.fail("DS-README-04", f"no demo card; add {ds.CARD_START} and {ds.CARD_END} "
                      "above the first section and run scripts/gen-demo-index.py", path)
        elif block.strip() != ds.render_card(self.demo).strip():
            self.fail("DS-README-04", "the demo card is out of date; run scripts/gen-demo-index.py", path)

        target = self.demo.path / self.demo.presenter_file()
        linked = any(ds.is_relative_link(t) and (path.parent / t.split("#")[0]).resolve() == target.resolve()
                     for _, t in ds.links(text))
        if not linked:
            self.fail("DS-README-05", f"README.md does not link to {self.demo.presenter_file()}", path)

    SCRIPT_ORDER = [
        ("How to run this script", lambda t: ds.heading_is(t, "How to run this script")),
        ("0 · Pre-flight", lambda t: ds.heading_is(t, "0 · Pre-flight")),
        ("What each act proves", lambda t: ds.heading_is(t, "What each act proves")),
        ("Opening · <title> (N min)", lambda t: t.startswith("Opening · ")),
        ("Act N · <title> (N min)", lambda t: bool(re.match(r"^Act \d+ · ", t))),
        ("Close · <title> (N min)", lambda t: t.startswith("Close · ")),
        ("Show me the code", lambda t: ds.heading_is(t, "Show me the code")),
        ("Talking points & objections", lambda t: ds.heading_is(t, "Talking points & objections")),
        ("Known limits", lambda t: ds.heading_is(t, "Known limits")),
        ("Reset / re-run", lambda t: ds.heading_is(t, "Reset / re-run")),
    ]
    ACT = 4

    def _ordered(self, path: Path, secs: list, order: list, repeatable: int, rule: str) -> dict:
        """Check presence and order; return {slot: [sections]}."""
        by_slot: dict[int, list] = {}
        last = -1
        for sec in secs:
            i = next((k for k, (_, match) in enumerate(order) if match(sec.title)), None)
            if i is None:
                continue
            if i < last or (i == last and i != repeatable):
                self.fail(rule, f"'## {sec.title}' is out of order or repeated", path, sec.line)
            last = max(last, i)
            by_slot.setdefault(i, []).append(sec)
        missing = [order[i][0] for i in range(len(order)) if i not in by_slot]
        if missing:
            self.fail(rule, "missing sections: " + ", ".join(f"'## {m}'" for m in missing), path)
        return by_slot

    def verify_needle(self) -> str | None:
        script, _ = self.command_script("verify")
        if script:
            return Path(script).name if not script.startswith("npm run") else script
        return (self.m.get("commands") or {}).get("verify")

    def check_script(self, name: str) -> None:
        path = self.demo.path / name
        text = self.read(name)
        if text is None:
            self.fail("DS-SCRIPT-01", f"{name} is missing", path)
            return
        head, secs = ds.sections(text)
        if not any(re.match(r"^\s*[-*]?\s*\*\*Run length:\*\*", line) for _, line in head):
            self.fail("DS-SCRIPT-02", "the header block (above the first '##') has no '**Run length:**' line", path)

        by_slot = self._ordered(path, secs, self.SCRIPT_ORDER, self.ACT, "DS-SCRIPT-03")

        acts = by_slot.get(self.ACT, [])
        if len(acts) < 2:
            self.fail("DS-SCRIPT-04", f"{len(acts)} act(s) found; at least two '## Act N · <title> (N min)' are required", path)
        for act in acts:
            seen: dict[str, int] = {}
            for n, line in act.body:
                # `**Show.**`, or with a qualifier in brackets: `**Show (force a fail).**`
                lab = re.match(r"^\s*\*\*(Frame|Show|Land|Ask)(?: \([^)]*\))?\.\*\*", line)
                if lab and lab.group(1) not in seen:
                    seen[lab.group(1)] = n
            beats = ["Frame", "Show", "Land", "Ask"]
            absent = [b for b in beats if b not in seen]
            if absent:
                self.fail("DS-SCRIPT-04", f"'## {act.title}' lacks " + ", ".join(f"**{b}.**" for b in absent), path, act.line)
            elif [seen[b] for b in beats] != sorted(seen[b] for b in beats):
                self.fail("DS-SCRIPT-04", f"'## {act.title}': beats must first appear as Frame, Show, Land, Ask", path, act.line)

        pre = by_slot.get(1)
        if pre:
            body = "\n".join(line for _, line in pre[0].body)
            # The unset line sits in a fenced block, so read the raw section text too.
            raw = _raw_section(text, pre[0])
            if "unset LANGFUSE_PUBLIC_KEY" not in raw:
                self.fail("DS-SCRIPT-05", "pre-flight has no 'unset LANGFUSE_PUBLIC_KEY …' line", path, pre[0].line)
            needle = self.verify_needle()
            if needle and needle not in raw and needle not in body:
                self.fail("DS-SCRIPT-05", f"pre-flight does not run the verify command (`{needle}`)", path, pre[0].line)

        code = by_slot.get(6)
        if code:
            self.check_citations(path, _raw_section(text, code[0]), code[0].line)

    GUIDE_ORDER = [
        ("Before the session", lambda t: ds.heading_is(t, "Before the session")),
        ("Run of show", lambda t: ds.heading_is(t, "Run of show")),
        ("Module N · <title> (N min)", lambda t: bool(re.match(r"^Module \d+ · ", t))),
        ("Known limits", lambda t: ds.heading_is(t, "Known limits")),
        ("Reset / re-run", lambda t: ds.heading_is(t, "Reset / re-run")),
    ]

    def check_workshop_guide(self) -> None:
        name = "WORKSHOP_GUIDE.md"
        path = self.demo.path / name
        text = self.read(name)
        if text is None:
            self.fail("DS-SCRIPT-01", f"{name} is missing", path)
            return
        _, secs = ds.sections(text)
        by_slot = self._ordered(path, secs, self.GUIDE_ORDER, 2, "DS-SCRIPT-03")
        before = by_slot.get(0)
        needle = self.verify_needle()
        if before and needle and needle not in _raw_section(text, before[0]):
            self.fail("DS-SCRIPT-05", f"'## Before the session' does not run the verify command (`{needle}`)",
                      path, before[0].line)

    def check_citations(self, path: Path, section_text: str, start_line: int) -> None:
        for off, line in enumerate(section_text.splitlines()):
            for m in re.finditer(r"`([\w./@-]+\.[A-Za-z0-9]+):(\d+)(?:[-–](\d+))?`", line):
                ref, first = m.group(1), int(m.group(2))
                last = int(m.group(3) or first)
                cands = [self.demo.path / ref, self.root / ref, path.parent / ref]
                target = next((c for c in cands if c.is_file()), None)
                where = start_line + off
                if target is None:
                    self.fail("DS-LINK-02", f"cited file `{ref}` does not exist", path, where, "warning")
                    continue
                n = sum(1 for _ in target.open(errors="replace"))
                if last > n:
                    self.fail("DS-LINK-02", f"`{ref}:{m.group(2)}` is past the end of the file ({n} lines)",
                              path, where, "warning")

    def check_env(self) -> None:
        if self.demo.profile in ("standalone", "workshop") and not (self.demo.path / ".env.example").is_file():
            self.fail("DS-ENV-01", ".env.example is missing", self.demo.path / ".env.example")
        try:
            out = subprocess.run(["git", "-C", str(self.root), "ls-files", "-z", "--", self.rel(self.demo.path)],
                                 capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return
        for f in filter(None, out.split("\0")):
            base = Path(f).name
            if (base == ".env" or base.startswith(".env.") or base.endswith(".env")) and base != ".env.example":
                self.fail("DS-ENV-02", f"{f} is tracked by git; only .env.example may be committed", self.root / f)

    def check_commands(self) -> None:
        cmds = self.m.get("commands") or {}
        if cmds.get("seed"):
            _, problem = self.command_script("seed")
            if problem:
                self.fail("DS-SEED-01", f"commands.seed: {problem}", self.demo.path / "demo.yaml")
        elif self.demo.profile != "container":
            self.fail("DS-SEED-01", "commands.seed is not set", self.demo.path / "demo.yaml")

        _, problem = self.command_script("verify")
        if problem:
            self.fail("DS-VERIFY-01", f"commands.verify: {problem}", self.demo.path / "demo.yaml")

        if not test_files(self.demo.path):
            self.fail("DS-TEST-01", "no test files (test_*.py, *_test.py, *.test.*, *.spec.*)",
                      self.demo.path / "tests")

    def check_spec_and_addons(self) -> None:
        if self.demo.profile in ("standalone", "workshop") and not (self.demo.path / "SPEC.md").is_file():
            self.fail("DS-SPEC-01", "SPEC.md is missing", self.demo.path / "SPEC.md")
        for addon, files in self.demo.addon_files().items():
            for f in files:
                if not (self.demo.path / f).exists():
                    self.fail("DS-ADDON-01", f"add-on '{addon}' needs {f}", self.demo.path / "demo.yaml")

    def check_links(self) -> None:
        names = ["README.md"] + ([self.demo.presenter_file()] if self.demo.profile == "workshop"
                                 else self.demo.script_files())
        for name in dict.fromkeys(names):
            path = self.demo.path / name
            if not path.is_file():
                continue
            for line, target in ds.links(path.read_text(errors="replace")):
                if not ds.is_relative_link(target):
                    continue
                clean = re.sub(r"%20", " ", target.split("#")[0].split("?")[0])
                if clean and not (path.parent / clean).exists():
                    self.fail("DS-LINK-01", f"link target `{target}` does not exist", path, line)


def _raw_section(text: str, sec: ds.Section) -> str:
    """The section's full text, fenced blocks included, up to the next '##' heading."""
    lines = text.splitlines()[sec.line:]
    out = []
    in_fence = None
    for line in lines:
        f = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if f:
            in_fence = None if in_fence else f.group(1)
        if not in_fence and re.match(r"^##\s", line):
            break
        out.append(line)
    return "\n".join(out)


_SKIP_DIRS = {"node_modules", ".venv", "venv", ".next", "dist", "build", "__pycache__", ".git", ".run"}


def test_files(demo_path: Path) -> list[Path]:
    hits = []
    stack = [demo_path]
    while stack:
        d = stack.pop()
        for p in d.iterdir():
            if p.is_dir():
                if p.name not in _SKIP_DIRS and not p.name.startswith("."):
                    stack.append(p)
            elif re.match(r"^(test_.+\.py|.+_test\.py|.+\.(test|spec)\.(ts|tsx|js|mjs|cjs))$", p.name):
                hits.append(p)
    return hits


def apply_waivers(demo: ds.Demo, findings: list[Finding], strict: bool, today: dt.date) -> list[Finding]:
    if strict or not demo.valid:
        return findings
    waivers = {w.rule: w for w in demo.waivers()}
    used = set()
    out = []
    for f in findings:
        w = waivers.get(f.rule)
        if w and f.level == "error" and f.rule not in ("DS-MANIFEST-01", "DS-MANIFEST-02"):
            used.add(f.rule)
            if w.until and w.until < today:
                out.append(Finding(f.rule, f.demo, f"{f.message} (waiver expired on {w.until})", f.file, f.line))
            else:
                out.append(Finding(f.rule, f.demo, f"waived ({w.reason}): {f.message}", f.file, f.line, "notice"))
        else:
            out.append(f)
    for rule in waivers:
        if rule not in used:
            out.append(Finding(rule, demo.name, f"waiver for {rule} is no longer needed; remove it",
                               f"demos/{demo.name}/demo.yaml", 0, "warning"))
    return out


def write_waivers(demo: ds.Demo, findings: list[Finding]) -> list[str]:
    """Append a bootstrap waiver to demo.yaml for every rule that fails unwaived."""
    have = {w.rule for w in demo.waivers()}
    rules = sorted({f.rule for f in findings if f.level == "error"
                    and f.rule not in ("DS-MANIFEST-01", "DS-MANIFEST-02", "DS-INDEX-01")} - have)
    if not rules:
        return []
    path = demo.path / "demo.yaml"
    text = path.read_text()
    entries = "".join(f"  - rule: {r}\n    reason: {BOOTSTRAP_REASON}\n" for r in rules)
    if re.search(r"^waivers:\s*$", text, re.M):
        lines = text.splitlines(keepends=True)
        start = next(i for i, l in enumerate(lines) if re.match(r"^waivers:\s*$", l))
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].strip() and not lines[i].startswith((" ", "\t", "#"))), len(lines))
        lines.insert(end, entries)
        text = "".join(lines)
    else:
        if re.search(r"^waivers:\s*\[\s*\]\s*$", text, re.M):
            text = re.sub(r"^waivers:\s*\[\s*\]\s*\n?", "", text, flags=re.M)
        text = text.rstrip("\n") + "\nwaivers:\n" + entries
    path.write_text(text)
    return rules


def check_index(demos_dir: Path, demos: list[ds.Demo], root: Path) -> list[Finding]:
    index = demos_dir / "README.md"
    if not index.is_file():
        return [Finding("DS-INDEX-01", "(index)", "demos/README.md is missing", str(index))]
    text = index.read_text()
    block = ds.read_block(text, ds.INDEX_START, ds.INDEX_END)
    rel = str(index.relative_to(root)) if index.is_relative_to(root) else str(index)
    if block is None:
        return [Finding("DS-INDEX-01", "(index)", f"no {ds.INDEX_START} / {ds.INDEX_END} markers", rel)]
    if block.strip() != ds.render_index(demos).strip():
        return [Finding("DS-INDEX-01", "(index)", "the demo index is out of date; run scripts/gen-demo-index.py", rel)]
    return []


def emit(findings: list[Finding], fmt: str, demos_checked: int) -> None:
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    notices = [f for f in findings if f.level == "notice"]
    if fmt == "json":
        print(json.dumps({"demos": demos_checked, "errors": len(errors), "warnings": len(warnings),
                          "waived": len(notices), "findings": [asdict(f) for f in findings]}, indent=2))
        return
    if fmt == "github":
        for f in findings:
            loc = f"file={f.file}" + (f",line={f.line}" if f.line else "")
            msg = f.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::{f.level} {loc},title={f.rule} ({f.demo})::{msg}")
    else:
        label = {"error": "FAIL", "warning": "WARN", "notice": "WAIVED"}
        for demo in dict.fromkeys(f.demo for f in findings):
            print(f"{demo}")
            for f in (x for x in findings if x.demo == demo):
                loc = f" [{f.file}{':' + str(f.line) if f.line else ''}]" if f.file else ""
                print(f"  {label[f.level]:<6} {f.rule:<15} {f.message}{loc}")
    print(f"\n{demos_checked} demo(s) checked: {len(errors)} failure(s), {len(warnings)} warning(s), "
          f"{len(notices)} waived", file=sys.stderr if fmt == "json" else sys.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--demo", action="append", help="check only this demo (repeatable)")
    ap.add_argument("--strict", action="store_true", help="ignore waivers")
    ap.add_argument("--format", choices=("text", "github", "json"), default="text")
    ap.add_argument("--write-waivers", action="store_true",
                    help=f"record every current failure as a waiver ('{BOOTSTRAP_REASON}')")
    ap.add_argument("--demos-dir", type=Path, default=ds.DEFAULT_DEMOS_DIR)
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(),
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    demos_dir = args.demos_dir.resolve()
    root = demos_dir.parent  # container commands resolve from here (§6)
    all_demos = ds.discover(demos_dir)
    selected = all_demos
    if args.demo:
        names = set(args.demo)
        selected = [d for d in all_demos if d.name in names]
        unknown = names - {d.name for d in selected}
        if unknown:
            print(f"unknown demo(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2

    findings: list[Finding] = []
    for demo in selected:
        raw = DemoCheck(demo, root).run()
        if args.write_waivers and demo.valid:
            added = write_waivers(demo, raw)
            if added:
                print(f"{demo.name}: waived {', '.join(added)}")
                ds.load_manifest(demo)
        findings += apply_waivers(demo, raw, args.strict, args.today)
    if not args.demo:
        findings += check_index(demos_dir, all_demos, root)

    if args.write_waivers:
        return 0
    emit(findings, args.format, len(selected))
    return 1 if any(f.level == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
