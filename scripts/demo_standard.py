"""Shared model for the demo standard (docs/DEMO_STANDARD_SPEC.md, demos/CONTRIBUTING.md).

Used by scripts/check-demo-structure.py and scripts/gen-demo-index.py. It holds the
manifest schema, the Markdown helpers both tools need, and the renderers for the
demo card and the demo index, so the checker and the generator can never disagree
about what the generated blocks should contain.

Python 3.9+ and PyYAML only.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEMOS_DIR = REPO_ROOT / "demos"

# --------------------------------------------------------------------------- schema

PROFILES = ("container", "standalone", "workshop")
STATUSES = ("active", "draft", "archived")
COVERS = (
    "trace", "monitor", "datasets", "experiment", "evaluate",
    "deploy", "gateway", "governance", "operations",
)
TARGETS = ("self-hosted", "cloud", "either", "dual")

# Default files for each add-on (paths relative to the demo folder). A manifest may
# point an add-on at differently named files (decision D8) with a one-key mapping:
#   addons:
#     - questions: DEMO_SYMPTOMS.md
#     - cicd: [cicd/README.md, cicd/thresholds.json, ../../.github/workflows/x.yml]
ADDON_FILES = {
    "spec": ["SPEC.md"],
    "cicd": ["cicd/README.md", "cicd/thresholds.json",
             "../../.github/workflows/{name}-prompt-gate.yml"],
    "capability-matrix": ["CAPABILITY_MATRIX.md"],
    "architecture": ["docs/ARCHITECTURE.md"],
    "measurements": ["docs/MEASUREMENTS.md"],
    "questions": ["DEMO_QUESTIONS.md"],
    "labs": ["LABS.md"],
    "portal": ["scripts/run_portal.sh"],
}

RULES = {
    "DS-MANIFEST-01": "demo.yaml exists, parses, has every required key and no unknown key",
    "DS-MANIFEST-02": "name equals the folder name and is kebab-case",
    "DS-README-01": "README.md exists",
    "DS-README-02": "every required README section is present",
    "DS-README-03": "README sections are in order; other sections only between Run it and Configuration",
    "DS-README-04": "the demo card is present and matches the generator's output",
    "DS-README-05": "the README links to the demo script or workshop guide",
    "DS-SCRIPT-01": "DEMO_SCRIPT.md exists (WORKSHOP_GUIDE.md for workshops)",
    "DS-SCRIPT-02": "the script header block has a **Run length:** line",
    "DS-SCRIPT-03": "every required script section is present and in order",
    "DS-SCRIPT-04": "at least two acts, each with Frame, Show, Land and Ask in order",
    "DS-SCRIPT-05": "pre-flight contains the unset LANGFUSE_PUBLIC_KEY line and the verify command",
    "DS-ENV-01": ".env.example exists for standalone and workshop demos",
    "DS-ENV-02": "no .env file other than .env.example is tracked by git",
    "DS-SEED-01": "commands.seed is set and the script it names exists",
    "DS-VERIFY-01": "commands.verify is set and the script it names exists",
    "DS-TEST-01": "commands.test is set and at least one test file exists",
    "DS-SPEC-01": "SPEC.md exists for standalone and workshop demos",
    "DS-ADDON-01": "each declared add-on's files exist",
    "DS-INDEX-01": "the generated index in demos/README.md is up to date",
    "DS-LINK-01": "relative links in the README and scripts resolve",
    "DS-LINK-02": "file:line citations in Show me the code resolve (warning only)",
}

KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LENGTH = re.compile(r"^\d+(?:\s*-\s*\d+)?$")

_SCHEMA: dict[str, Any] = {
    "name": (True, str),
    "title": (True, str),
    "pitch": (True, str),
    "profile": (True, str),
    "status": (True, str),
    "covers": (True, list),
    "stack": (True, list),
    "langfuse": (True, {"target": (True, str), "project": (True, str)}),
    "length": (True, {"full_min": (True, (str, int)), "short_min": (False, (str, int))}),
    "commands": (True, {
        "run": (True, str), "verify": (True, str), "test": (True, str),
        "install": (False, str), "seed": (False, str), "reset": (False, str),
    }),
    "scripts": (False, list),
    "addons": (False, list),
    "waivers": (False, list),
    "index_order": (False, int),
}


@dataclass
class Waiver:
    rule: str
    reason: str
    until: _dt.date | None = None


@dataclass
class Demo:
    """One folder under demos/ and, when it parses, its manifest."""

    name: str
    path: Path
    manifest: dict[str, Any] | None = None
    manifest_errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.manifest is not None and not self.manifest_errors

    @property
    def profile(self) -> str:
        return (self.manifest or {}).get("profile", "")

    def waivers(self) -> list[Waiver]:
        out = []
        for w in (self.manifest or {}).get("waivers") or []:
            if isinstance(w, dict) and isinstance(w.get("rule"), str):
                out.append(Waiver(w["rule"], str(w.get("reason", "")), _as_date(w.get("until"))))
        return out

    def script_files(self) -> list[str]:
        """DEMO_SCRIPT.md plus declared variants (workshops: none)."""
        if self.profile == "workshop":
            return []
        files = ["DEMO_SCRIPT.md"]
        for s in (self.manifest or {}).get("scripts") or []:
            if isinstance(s, dict) and isinstance(s.get("file"), str):
                files.append(s["file"])
        return files

    def presenter_file(self) -> str:
        return "WORKSHOP_GUIDE.md" if self.profile == "workshop" else "DEMO_SCRIPT.md"

    def addon_files(self) -> dict[str, list[str]]:
        """Declared add-on name -> files it requires, relative to the demo folder."""
        out: dict[str, list[str]] = {}
        for item in (self.manifest or {}).get("addons") or []:
            if isinstance(item, str):
                out[item] = [f.format(name=self.name) for f in ADDON_FILES.get(item, [])]
            elif isinstance(item, dict) and len(item) == 1:
                (key, val), = item.items()
                out[key] = [val] if isinstance(val, str) else list(val or [])
        return out


def _as_date(value: Any) -> _dt.date | None:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def discover(demos_dir: Path = DEFAULT_DEMOS_DIR) -> list[Demo]:
    """Every direct subfolder of demos/ not starting with '_' or '.' is a demo."""
    demos = []
    for p in sorted(demos_dir.iterdir()):
        if p.is_dir() and not p.name.startswith(("_", ".")):
            demo = Demo(p.name, p)
            load_manifest(demo)
            demos.append(demo)
    return demos


def load_manifest(demo: Demo) -> None:
    path = demo.path / "demo.yaml"
    if not path.is_file():
        demo.manifest_errors = ["demo.yaml is missing"]
        return
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        demo.manifest_errors = [f"demo.yaml does not parse: {exc}".splitlines()[0]]
        return
    if not isinstance(data, dict):
        demo.manifest_errors = ["demo.yaml must be a mapping"]
        return
    demo.manifest = data
    demo.manifest_errors = validate_manifest(data)


def _check_shape(data: dict, schema: dict, prefix: str, errors: list[str]) -> None:
    for key in data:
        if key not in schema:
            errors.append(f"unknown key '{prefix}{key}'")
    for key, (required, kind) in schema.items():
        if key not in data or data[key] is None:
            if required:
                errors.append(f"missing required key '{prefix}{key}'")
            continue
        val = data[key]
        if isinstance(kind, dict):
            if not isinstance(val, dict):
                errors.append(f"'{prefix}{key}' must be a mapping")
            else:
                _check_shape(val, kind, f"{prefix}{key}.", errors)
        elif kind is int and isinstance(val, bool):
            errors.append(f"'{prefix}{key}' must be an integer")
        elif not isinstance(val, kind):
            want = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
            errors.append(f"'{prefix}{key}' must be a {want}")


def validate_manifest(data: dict) -> list[str]:
    """Schema errors for DS-MANIFEST-01 (shape, enums, value formats)."""
    errors: list[str] = []
    _check_shape(data, _SCHEMA, "", errors)

    def enum(key: str, value: Any, allowed: tuple) -> None:
        if isinstance(value, str) and value not in allowed:
            errors.append(f"'{key}' is '{value}'; expected one of: {', '.join(allowed)}")

    enum("profile", data.get("profile"), PROFILES)
    enum("status", data.get("status"), STATUSES)
    lf = data.get("langfuse") if isinstance(data.get("langfuse"), dict) else {}
    enum("langfuse.target", lf.get("target"), TARGETS)

    pitch = data.get("pitch")
    if isinstance(pitch, str):
        if len(pitch) > 160:
            errors.append(f"'pitch' is {len(pitch)} characters; the limit is 160")
        if pitch.rstrip().endswith("."):
            errors.append("'pitch' must not end with a period")

    covers = data.get("covers")
    if isinstance(covers, list):
        unknown = [c for c in covers if c not in COVERS]
        if unknown:
            errors.append(f"'covers' has unknown values {unknown}; allowed: {', '.join(COVERS)}")
        elif not covers:
            errors.append("'covers' must not be empty")
        elif [COVERS.index(c) for c in covers] != sorted(COVERS.index(c) for c in covers):
            errors.append(f"'covers' must follow the order {', '.join(COVERS)}")
    stack = data.get("stack")
    if isinstance(stack, list) and (not stack or not all(isinstance(s, str) for s in stack)):
        errors.append("'stack' must be a non-empty list of strings")

    length = data.get("length") if isinstance(data.get("length"), dict) else {}
    for key in ("full_min", "short_min"):
        val = length.get(key)
        if val is not None and not _LENGTH.match(str(val)):
            errors.append(f"'length.{key}' must be minutes like 15 or 15-20, got '{val}'")

    for i, s in enumerate(data.get("scripts") or [] if isinstance(data.get("scripts"), list) else []):
        if not isinstance(s, dict):
            errors.append(f"'scripts[{i}]' must be a mapping with file, title, length_min")
            continue
        extra = set(s) - {"file", "title", "length_min"}
        if extra:
            errors.append(f"'scripts[{i}]' has unknown keys {sorted(extra)}")
        for k in ("file", "title"):
            if not isinstance(s.get(k), str):
                errors.append(f"'scripts[{i}].{k}' is required")
        if s.get("length_min") is not None and not _LENGTH.match(str(s["length_min"])):
            errors.append(f"'scripts[{i}].length_min' must be minutes like 30 or 25-30")

    for i, a in enumerate(data.get("addons") or [] if isinstance(data.get("addons"), list) else []):
        name = a if isinstance(a, str) else (next(iter(a)) if isinstance(a, dict) and len(a) == 1 else None)
        if name is None:
            errors.append(f"'addons[{i}]' must be an add-on name or a one-key mapping name: files")
        elif name not in ADDON_FILES:
            errors.append(f"'addons[{i}]' is '{name}'; expected one of: {', '.join(ADDON_FILES)}")
        elif isinstance(a, dict):
            files = a[name]
            if not (isinstance(files, str) or (isinstance(files, list) and files
                                               and all(isinstance(f, str) for f in files))):
                errors.append(f"'addons[{i}].{name}' must be a path or a list of paths")

    for i, w in enumerate(data.get("waivers") or [] if isinstance(data.get("waivers"), list) else []):
        if not isinstance(w, dict):
            errors.append(f"'waivers[{i}]' must be a mapping with rule, reason, until")
            continue
        extra = set(w) - {"rule", "reason", "until"}
        if extra:
            errors.append(f"'waivers[{i}]' has unknown keys {sorted(extra)}")
        if w.get("rule") not in RULES:
            errors.append(f"'waivers[{i}].rule' is '{w.get('rule')}', which is not a known rule ID")
        if not isinstance(w.get("reason"), str) or not w["reason"].strip():
            errors.append(f"'waivers[{i}].reason' is required")
        if w.get("until") is not None and _as_date(w["until"]) is None:
            errors.append(f"'waivers[{i}].until' must be a date like 2027-01-31")
    return errors


# ------------------------------------------------------------------------ markdown

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def prose_lines(text: str) -> list[tuple[int, str]]:
    """(1-based line number, line) for lines outside fenced code blocks."""
    out, fence = [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        out.append((n, line))
    return out


@dataclass
class Section:
    title: str
    line: int
    body: list[tuple[int, str]]  # prose lines up to the next `##` heading


def sections(text: str) -> tuple[list[tuple[int, str]], list[Section]]:
    """Split into (prose lines above the first `##` heading, the `##` sections)."""
    head: list[tuple[int, str]] = []
    secs: list[Section] = []
    for n, line in prose_lines(text):
        m = _HEADING.match(line)
        if m and len(m.group(1)) == 2:
            secs.append(Section(normalize(m.group(2)), n, []))
            continue
        (secs[-1].body if secs else head).append((n, line))
    return head, secs


def normalize(title: str) -> str:
    return title.replace("\u2019", "'").replace("\u2018", "'").strip()


_SUFFIX = re.compile(r"^\s*(?:[\u2014\u2013-]|\(|\[|$)")


def heading_is(title: str, standard: str) -> bool:
    """A standard heading may carry a suffix after a dash or in brackets (§7.2)."""
    return title == standard or (title.startswith(standard) and bool(_SUFFIX.match(title[len(standard):])))


_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")


def links(text: str) -> list[tuple[int, str]]:
    """(line, target) for Markdown links outside code."""
    out = []
    for n, line in prose_lines(text):
        for m in _LINK.finditer(_INLINE_CODE.sub("", line)):
            out.append((n, m.group(1)))
    return out


def is_relative_link(target: str) -> bool:
    return not (re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "/", "{{")))


def replace_block(text: str, start: str, end: str, content: str) -> str | None:
    """Replace what sits between two marker lines. None when the markers are missing."""
    i, j = text.find(start), text.find(end)
    if i < 0 or j < i:
        return None
    return text[: i + len(start)] + "\n" + content.rstrip("\n") + "\n" + text[j:]


def read_block(text: str, start: str, end: str) -> str | None:
    i, j = text.find(start), text.find(end)
    if i < 0 or j < i:
        return None
    return text[i + len(start): j].strip("\n")


# ---------------------------------------------------------------------- rendering

CARD_START, CARD_END = "<!-- demo-card:start -->", "<!-- demo-card:end -->"
INDEX_START, INDEX_END = "<!-- demo-index:start -->", "<!-- demo-index:end -->"

_STACK_NAMES = {
    "python": "Python", "typescript": "TypeScript", "javascript": "JavaScript",
    "langchain": "LangChain", "langgraph": "LangGraph", "crewai": "CrewAI",
    "anthropic-sdk": "Anthropic SDK", "openai-sdk": "OpenAI SDK", "vercel-ai-sdk": "Vercel AI SDK",
    "mcp": "MCP", "chromadb": "ChromaDB", "clickhouse": "ClickHouse", "litellm": "LiteLLM",
    "strands": "Strands", "a2a": "A2A", "fastapi": "FastAPI", "nextjs": "Next.js",
    "n8n": "n8n", "asyncio": "asyncio", "docker": "Docker", "react": "React", "node": "Node.js",
    "uv": "uv", "pytest": "pytest", "otel": "OpenTelemetry", "opentelemetry": "OpenTelemetry",
    "temporal": "Temporal", "sqlite": "SQLite", "postgres": "PostgreSQL", "redis": "Redis",
}
_TARGET_NAMES = {
    "self-hosted": "Self-hosted", "cloud": "Cloud",
    "either": "Cloud or self-hosted", "dual": "Dual (Cloud + self-hosted)",
}
_RUNS_AS = {
    "container": "Container in the root stack (`demo` profile)",
    "standalone": "Standalone: own toolchain and `.env`",
    "workshop": "Workshop kit: facilitator-led, own toolchain and `.env`",
}
_PROFILE_HEADINGS = {
    "container": "Container demos",
    "standalone": "Standalone demos",
    "workshop": "Workshops",
}


def _minutes(value: Any) -> str:
    return re.sub(r"\s*-\s*", "\u2013", str(value)) + " min"


def _covers(m: dict) -> str:
    return " · ".join(c.capitalize() for c in m.get("covers") or [])


def _length(m: dict) -> str:
    length = m.get("length") or {}
    out = _minutes(length.get("full_min"))
    if length.get("short_min") is not None:
        out += " · short path " + _minutes(length["short_min"])
    return out


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _present_links(demo: Demo, prefix: str) -> str:
    """Links to the presenter files that exist; a missing one is named, not linked."""
    m = demo.manifest or {}
    entries = [(demo.presenter_file(), demo.presenter_file())]
    if demo.profile != "workshop":
        for s in m.get("scripts") or []:
            label = s["title"] + (f" ({_minutes(s['length_min'])})" if s.get("length_min") else "")
            entries.append((label, s["file"]))
    out = []
    for label, file in entries:
        if (demo.path / file).is_file():
            out.append(f"[{label}]({prefix}{file})")
        else:
            out.append(f"{label} *(not written yet)*")
    return " · ".join(out)


def render_card(demo: Demo) -> str:
    m = demo.manifest or {}
    lf = m.get("langfuse") or {}
    stack = " · ".join(_STACK_NAMES.get(s, s) for s in m.get("stack") or [])
    rows = [
        ("Covers", _covers(m)),
        ("Stack", stack),
        ("Runs as", _RUNS_AS.get(m.get("profile"), m.get("profile", ""))),
        ("Langfuse", f"{_TARGET_NAMES.get(lf.get('target'), lf.get('target'))} · project `{lf.get('project')}`"),
        ("Length", _length(m)),
        ("Present it", _present_links(demo, "")),
    ]
    if m.get("status") == "draft":
        rows.insert(0, ("Status", "Draft"))
    lines = ["| | |", "|---|---|"]
    lines += [f"| **{k}** | {_cell(v) if k != 'Present it' else v} |" for k, v in rows]
    return "\n".join(lines)


def _index_key(demo: Demo) -> tuple:
    m = demo.manifest or {}
    order = m.get("index_order")
    return (order if isinstance(order, int) else 10**9, str(m.get("title", demo.name)).lower())


def render_index(demos: list[Demo]) -> str:
    listed = [d for d in demos if d.valid and d.manifest.get("status") != "archived"]
    parts = []
    for profile in PROFILES:
        group = sorted((d for d in listed if d.profile == profile), key=_index_key)
        if not group:
            continue
        parts.append(f"### {_PROFILE_HEADINGS[profile]}")
        parts.append("")
        parts.append("| Demo | What it shows | Covers | Length | Run | Present |")
        parts.append("|---|---|---|---|---|---|")
        for d in group:
            m = d.manifest
            title = f"**[{_cell(m['title'])}]({d.name}/)**"
            if m.get("status") == "draft":
                title += " *(draft)*"
            run = m["commands"]["run"]
            if profile != "container":
                run = f"cd demos/{d.name} && {run}"
            parts.append(
                f"| {title} | {_cell(m['pitch'])} | {_covers(m)} | {_length(m)} "
                f"| `{_cell(run)}` | {_present_links(d, d.name + '/')} |"
            )
        parts.append("")
    return "\n".join(parts).rstrip("\n")
