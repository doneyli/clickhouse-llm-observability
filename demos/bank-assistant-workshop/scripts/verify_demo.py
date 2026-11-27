#!/usr/bin/env python3
"""Readiness check for bank-assistant-workshop: reads Langfuse back and says READY or NOT READY.

    .venv/bin/python scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    .venv/bin/python scripts/verify_demo.py --links    # also print the link each module opens
    .venv/bin/python scripts/verify_demo.py --prep     # before `workshop.py serve` is up: services not checked, verdict says so
    .venv/bin/python scripts/verify_demo.py --env-file ../../.env   # from a work/<checkpoint> folder

Session readiness means: `workshop.py preflight` passes (packages, model key present,
Langfuse authentication; no paid model call); the portal and the banking MCP server are
serving; the keys map to the expected project; both prompt labels serve the text in
src/northwind/prompts.py; every item in data/seed-dataset.json is ACTIVE and matches; a
recent traced turn has its observations and the three signal scores on its root
observation; and a prepared experiment run exists for each label's current version
(modules 06-07). An HTTP 200 proves little on its own, so every check counts real rows.

Standard library only. The preflight step runs `workshop.py` with this same interpreter,
so run it with the demo's `.venv/bin/python`. No model calls and no writes; a handful of
Langfuse reads, done in seconds. tests/test_verify_demo.py covers the helpers and checks.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DEMO_DIR.parent.parent

# --- DEMO-SPECIFIC: what a ready workshop looks like. Keep in sync with the code it names.
ROOT_NAME = "northwind-assistant"                 # agent.TRACE_NAME: root observation of every turn
ENVIRONMENT = "workshop"                          # config.get_langfuse(environment=...); experiments use sdk-experiment
PROMPT_LABELS = {"workshop-baseline": "baseline",  # label -> workshop.py prompt --variant (modules 03 and 07)
                 "workshop-candidate": "candidate"}
PROMPT_VARS = {"baseline": "V1_BASELINE",          # variant -> the prompts.py constant helpers.publish_prompt sends
               "candidate": "WORKSHOP_CANDIDATE"}
PROMPTS_FILE = DEMO_DIR / "src" / "northwind" / "prompts.py"
EXPECTED_SCORES = ["guardrail-blocked", "pii-in-input", "all-caps-signal"]  # monitoring.record, module 04
DATASET_FILE = DEMO_DIR / "data" / "seed-dataset.json"  # the items `workshop.py dataset` upserts (module 05)
MIN_OBSERVATIONS = 3                              # root, input guardrail, and at least one more (a blocked turn has 5)
LOOKBACK_DAYS = 7                                 # how far back to look for a traced turn
EXPERIMENT_LOOKBACK_DAYS = 90                     # how far back to look for the prepared experiment runs
FRESH_HOURS = 24                                  # older than this only warns: send a question the day before
DEFAULT_WORKSHOP_ID = "bank-workshop"             # config.WORKSHOP_ID default
DEFAULT_PORTAL_PORT, DEFAULT_MCP_PORT = 8090, 8765  # config.PORT, config.MCP_PORT defaults
SEED_CMD = ".venv/bin/python scripts/seed_all.py"
SERVE_CMD = ".venv/bin/python workshop.py serve"
RUN_CMD = f"{SERVE_CMD}, then ask a question at http://127.0.0.1:8090"
EXPERIMENT_CMD = ".venv/bin/python workshop.py experiment --prompt-label {label}"
TIMEOUT_S = 10

RESULTS: List[Tuple[str, bool, str]] = []         # (PASS | FAIL | WARN, required, what)


def workshop_names(environ: Optional[MutableMapping[str, str]] = None) -> Dict[str, str]:
    """The per-attendee names prompts.PROMPT_NAME and helpers.DATASET_NAME derive from WORKSHOP_ID."""
    env = os.environ if environ is None else environ
    wid = env.get("WORKSHOP_ID") or DEFAULT_WORKSHOP_ID
    return {"workshop_id": wid, "prompt": f"bank-workshop/{wid}/assistant",
            "dataset": f"bank-workshop/{wid}/regression-v1"}


# --- Local definitions (what seed_all.py publishes) --------------------------------------

def local_prompts(path: Path = PROMPTS_FILE) -> Dict[str, str]:
    """variant -> prompt text, read from prompts.py with `ast` so this stays stdlib only.

    Evaluates top-level string constants and `+` concatenations of them, which is how
    WORKSHOP_CANDIDATE is built from V1_BASELINE. Raises KeyError or ValueError when a
    constant is missing or not a plain string.
    """
    values: Dict[str, str] = {}

    def ev(node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return ev(node.left) + ev(node.right)
        raise ValueError("not a string expression")

    for stmt in ast.parse(path.read_text(), filename=str(path)).body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            try:
                values[stmt.targets[0].id] = ev(stmt.value)
            except ValueError:
                pass
    return {variant: values[name] for variant, name in PROMPT_VARS.items()}


def local_items(path: Path = DATASET_FILE) -> List[dict]:
    """The dataset definitions, validated enough that every later comparison is meaningful."""
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path.name} holds no items")
    ids = [item.get("id") if isinstance(item, dict) else None for item in data]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError(f"{path.name}: every item needs a unique string id")
    for item in data:
        if not isinstance(item.get("input"), dict) or not item.get("expected_output") \
                or not isinstance(item.get("metadata"), dict):
            raise ValueError(f"{path.name}: item {item['id']} needs input, expected_output and metadata")
    return data


def item_id(dataset: str, local_id: str) -> str:
    """The deterministic remote id helpers.seed_dataset upserts each item under."""
    return hashlib.sha256(f"{dataset}/{local_id}".encode()).hexdigest()[:32]


def prompt_matches(current: Optional[Dict[str, Any]], variant: str, text: str) -> bool:
    """Type, text and the whole config, as helpers.publish_prompt writes them."""
    return bool(current) and current.get("type") == "text" and current.get("prompt") == text \
        and (current.get("config") or {}) == {"variant": variant}


def dataset_diff(dataset: str, items: List[dict], rows: List[dict]) -> List[str]:
    """Local ids whose remote item is missing, not ACTIVE, or differs in input, expected output or metadata."""
    remote = {r.get("id"): r for r in rows}
    bad = []
    for item in items:
        r = remote.get(item_id(dataset, item["id"]))
        if r is None or r.get("status") != "ACTIVE" or (r.get("input"), r.get("expectedOutput"), r.get("metadata")) \
                != (item["input"], item["expected_output"], item["metadata"]):
            bad.append(item["id"])
    return bad


# --- Result helpers ---------------------------------------------------------------------

def check(ok: bool, what: str, *, required: bool = True, hint: str = "") -> bool:
    """Record one check and print its line. A failed optional check is a WARN, not a FAIL."""
    status = "PASS" if ok else ("FAIL" if required else "WARN")
    RESULTS.append((status, required, what))
    print(f"  {status}  {what}" + (f"  ->  {hint}" if hint and not ok else ""))
    return ok


def summary(results: Optional[List[Tuple[str, bool, str]]] = None) -> Tuple[str, int]:
    """The closing line and the exit code: 0 when no required check failed, 1 otherwise."""
    rs = RESULTS if results is None else results
    passed = sum(1 for s, _, _ in rs if s == "PASS")
    failed = sum(1 for s, _, _ in rs if s == "FAIL")
    warned = sum(1 for s, _, _ in rs if s == "WARN")
    detail = f"{passed}/{len(rs)} checks passed"
    if failed:
        detail += f", {failed} required failed"
    if warned:
        detail += f", {warned} warning{'s' if warned != 1 else ''}"
    return (f"NOT READY ({detail})", 1) if failed else (f"READY ({detail})", 0)


# --- Environment and manifest -----------------------------------------------------------

def _strip_comment(line: str) -> str:
    """Drop a trailing ` # comment` that is not inside quotes."""
    quote = ""
    for i, ch in enumerate(line):
        if ch in "\"'" and not quote:
            quote = ch
        elif ch == quote:
            quote = ""
        elif ch == "#" and not quote and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if value[0] == '"' else inner
    return value


def parse_env(text: str) -> Dict[str, str]:
    """Parse KEY=value lines: blank lines, comments, `export`, quotes and inline comments."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        value = value.strip()
        out[key.strip()] = _unquote(value) if value[:1] in "\"'" else _strip_comment(value)
    return out


def manifest_value(text: str, section: str, key: str) -> Optional[str]:
    """Read `section.key` from demo.yaml (`section=""` for a top-level key) without PyYAML.

    Handles the manifest's flat shape only: top-level scalars and one level of nesting.
    """
    current = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        nested = line[0].isspace()
        m = re.match(r"\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        if not nested:
            current = m.group(1)
            if section == "" and m.group(1) == key:
                return _unquote(m.group(2).strip()) or None
        elif current == section and m.group(1) == key:
            return _unquote(m.group(2).strip()) or None
    return None


def env_file(profile: Optional[str]) -> Path:
    """Container demos read the root stack's .env; standalone and workshop demos their own."""
    return REPO_ROOT / ".env" if profile == "container" else DEMO_DIR / ".env"


def load_env(path: Path, environ: Optional[MutableMapping[str, str]] = None) -> bool:
    """Make `path` the only source of LANGFUSE_*: drop shell exports, then apply the file.

    A shell-exported key for another project would otherwise send traces there with no
    error, while these reads look at a different project. Every key the file sets also
    overrides the shell. Shell LANGFUSE_* are dropped even when the file is missing, so a
    missing file fails the key check instead of reading another project. Returns whether
    the file was loaded.
    """
    env = os.environ if environ is None else environ
    for k in [k for k in env if k.startswith("LANGFUSE_")]:
        del env[k]
    if not path.is_file():
        return False
    env.update(parse_env(path.read_text()))
    return True


# --- Langfuse reads -----------------------------------------------------------------------

def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    """Parse an API timestamp such as 2026-11-26T10:00:00.123Z on Python 3.9 as well."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", value)
    if not m:
        raise ValueError(f"unrecognised timestamp {value!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = m.group(3) or "Z"
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{'+00:00' if tz == 'Z' else tz}")


class ApiError(Exception):
    """An HTTP error (status set) or a network failure (status None)."""

    def __init__(self, status: Optional[int], message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


class Langfuse:
    """The few public-API calls the demo scripts need, on APIs Langfuse Cloud still serves:
    projects, prompts, Observations API v2 and Scores API v3."""

    def __init__(self, base_url: str, public_key: str, secret_key: str, timeout: float = TIMEOUT_S) -> None:
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "demo-verify/1"}
        self.timeout = timeout

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                body: Optional[Any] = None) -> Any:
        query = {k: (str(v).lower() if isinstance(v, bool) else v)
                 for k, v in (params or {}).items() if v is not None}
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or e.reason) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def cursor(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = 20) -> List[dict]:
        """Follow `meta.cursor` (Observations v2, Scores v3, experiments). A page may be empty
        while a cursor is still returned, so stop on a missing cursor, not on an empty page.
        A repeated cursor or more than `max_pages` pages is an ApiError, not a silent cut."""
        out: List[dict] = []
        cur = None
        seen = set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=100, **({"cursor": cur} if cur else {}))) or {}
            rows = d.get("data") or []
            if not isinstance(rows, list):
                raise ApiError(None, f"{path}: 'data' is not a list")
            out += rows
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:
                raise ApiError(None, f"{path}: the API repeated cursor {cur!r}")
            seen.add(cur)
        raise ApiError(None, f"{path}: still paging after {max_pages} pages")


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per module that opens Langfuse, in module order."""
    p = f"{base}/project/{state.get('project_id')}"
    names = state.get("names") or workshop_names()
    links = []
    if state.get("trace_id"):
        links.append(("Module 02 · latest turn", f"{p}/traces/{state['trace_id']}"))
    links.append(("Module 03 · the prompt", f"{p}/prompts/{urllib.parse.quote(names['prompt'], safe='')}"))
    if state.get("trace_id"):
        links.append(("Module 04 · signals (Scores)", f"{p}/traces/{state['trace_id']}"))
    if state.get("dataset_id"):
        links.append(("Module 05 · the dataset", f"{p}/datasets/{state['dataset_id']}"))
        for label, run_id in (state.get("runs") or {}).items():
            links.append((f"Module 06-07 · {label}", f"{p}/datasets/{state['dataset_id']}/runs/{run_id}"))
    return links


# --- Checks -------------------------------------------------------------------------------

def http_get(url: str, timeout: float = 4) -> Tuple[Optional[int], bytes]:
    """(status, body) for a local service; (None, b"") when nothing answers."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                    timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError):
        return None, b""


def preflight_check(env_path: Path, python: str = sys.executable) -> bool:
    """Run `workshop.py preflight` with this interpreter and the same env file."""
    print("Preflight")
    cmd = [python, str(DEMO_DIR / "workshop.py"), "--env-file", str(env_path), "preflight"]
    try:
        done = subprocess.run(cmd, cwd=DEMO_DIR, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        return check(False, f"workshop.py preflight did not finish: {type(e).__name__}")
    for line in (done.stdout + done.stderr).strip().splitlines()[-8:]:
        print(f"        {line}")
    return check(done.returncode == 0, "workshop.py preflight: packages, model key, Langfuse auth",
                 hint="read the error above; run with .venv/bin/python after commands.install")


def service_checks(names: Dict[str, str], environ: Optional[MutableMapping[str, str]] = None) -> None:
    """The facilitator's portal and banking MCP server, started by `workshop.py serve`."""
    env = os.environ if environ is None else environ
    print("Services")
    port = int(env.get("WORKSHOP_PORT") or DEFAULT_PORTAL_PORT)
    status, body = http_get(f"http://127.0.0.1:{port}/health")
    try:
        health = json.loads(body) if status == 200 else {}
    except ValueError:
        health = {}
    ok = status == 200 and health.get("status") == "ok" and health.get("tracing") is True \
        and health.get("workshop") == names["workshop_id"]
    check(ok, f"portal on 127.0.0.1:{port}/health: HTTP {status}, tracing {health.get('tracing')}, "
              f"workshop {health.get('workshop')} (want {names['workshop_id']})",
          hint=f"{SERVE_CMD} from the reference root with this .env")
    mcp = int(env.get("NORTHWIND_MCP_PORT") or DEFAULT_MCP_PORT)
    status, _ = http_get(f"http://127.0.0.1:{mcp}/mcp")
    check(status is not None and status < 500, f"banking MCP server on 127.0.0.1:{mcp}/mcp: HTTP {status}",
          hint=SERVE_CMD)


def dataset_items(lf: Langfuse, name: str, max_pages: int = 20) -> List[dict]:
    """Every item of a dataset (page-based paging on /dataset-items), bounded."""
    out: List[dict] = []
    for page in range(1, max_pages + 1):
        d = lf.get("/api/public/dataset-items", datasetName=name, page=page, limit=100) or {}
        rows = d.get("data") or []
        if not isinstance(rows, list):
            raise ApiError(None, "/api/public/dataset-items: 'data' is not a list")
        out += rows
        if page >= int((d.get("meta") or {}).get("totalPages") or 1):
            return out
    raise ApiError(None, f"/api/public/dataset-items: more than {max_pages} pages")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str], names: Dict[str, str],
                    prompts: Dict[str, str], items: List[dict]) -> Dict[str, Any]:
    state: Dict[str, Any] = {"names": names, "versions": {}, "runs": {}}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or wrong LANGFUSE_BASE_URL region; network error: is Langfuse up?")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in .env, or set DEMO_LANGFUSE_PROJECT to the project's name"):
        return state

    now = datetime.now(timezone.utc)
    try:
        print(f"Seeded artifacts (WORKSHOP_ID={names['workshop_id']})")
        for label, variant in PROMPT_LABELS.items():
            try:
                p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(names['prompt'], safe='')}",
                           label=label) or {}
            except ApiError as e:
                if e.status != 404:
                    raise
                check(False, f"prompt {names['prompt']} has no '{label}' label", hint=f"run {SEED_CMD}")
                continue
            state["versions"][label] = p.get("version")
            check(prompt_matches(p, variant, prompts[variant]),
                  f"prompt {names['prompt']}: {label} = v{p.get('version')} serves the {variant} text and config",
                  hint=f"the label serves other text or config: run {SEED_CMD}")

        try:
            ds = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(names['dataset'], safe='')}") or {}
        except ApiError as e:
            if e.status != 404:
                raise
            check(False, f"dataset {names['dataset']} exists",
                  hint=f"run {SEED_CMD} --allow-draft (rehearsal) or --reviewed")
        else:
            state["dataset_id"] = ds.get("id")
            rows = dataset_items(lf, names["dataset"])
            bad = dataset_diff(names["dataset"], items, rows)
            check(not bad, f"dataset {names['dataset']}: {len(items) - len(bad)}/{len(items)} items ACTIVE and"
                           f" matching data/seed-dataset.json" + (f"; not: {', '.join(bad)}" if bad else ""),
                  hint=f"run {SEED_CMD}")

        print("Latest traced turn")
        window = {"fromStartTime": iso(now - timedelta(days=LOOKBACK_DAYS)), "toStartTime": iso(now + timedelta(minutes=5))}
        roots = (lf.get("/api/public/v2/observations", name=ROOT_NAME, isRootObservation=True,
                        environment=ENVIRONMENT, fields="core,basic", limit=1, **window)
                 or {}).get("data") or []  # newest first
        if check(bool(roots), f"a '{ROOT_NAME}' turn in environment '{ENVIRONMENT}' in the last {LOOKBACK_DAYS} days",
                 hint=RUN_CMD):
            latest = roots[0]
            state["trace_id"] = trace_id = latest["traceId"]
            root_id = latest["id"]
            started = parse_time(latest["startTime"])
            obs = lf.cursor("/api/public/v2/observations", {
                "traceId": trace_id, "fields": "core",
                "fromStartTime": iso(started - timedelta(hours=1)), "toStartTime": iso(now + timedelta(minutes=5))})
            check(len(obs) >= MIN_OBSERVATIONS, f"latest turn has {len(obs)} observation(s) (want {MIN_OBSERVATIONS}+)",
                  hint="observations may still be landing: wait 30 s and re-run")
            scores = lf.cursor("/api/public/v3/scores", {"traceId": trace_id, "fields": "subject"})
            on_root = {s.get("name") for s in scores
                       if (s.get("subject") or {}).get("kind") == "observation" and (s.get("subject") or {}).get("id") == root_id}
            missing = [n for n in EXPECTED_SCORES if n not in on_root]
            check(not missing, f"latest turn's signals on its root observation: {', '.join(sorted(on_root)) or 'none'}"
                  + (f"; missing {', '.join(missing)}" if missing else ""),
                  hint="scores land after the trace: wait and re-run; module 04 adds them")
            age_h = (now - started).total_seconds() / 3600
            check(age_h <= FRESH_HOURS, f"latest turn is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
                  required=False, hint="ask one question the day before")

        if state.get("dataset_id"):
            print("Prepared experiment runs (modules 06-07)")
            runs = lf.cursor("/api/public/experiments", {
                "datasetId": state["dataset_id"], "fields": "core,metadata",
                "fromStartTime": iso(now - timedelta(days=EXPERIMENT_LOOKBACK_DAYS))}, cap=500)
            for label in PROMPT_LABELS:
                version = state["versions"].get(label)
                match = next((r for r in runs if (r.get("metadata") or {}).get("prompt_label") == label
                              and str((r.get("metadata") or {}).get("prompt_version")) == str(version)), None)
                if match:
                    state["runs"][label] = match.get("id")
                check(version is not None and match is not None,
                      f"a run of {label} v{version} in the last {EXPERIMENT_LOOKBACK_DAYS} days"
                      + (f": {match.get('name')}" if match else ""),
                      hint=EXPERIMENT_CMD.format(label=label) + " (model calls; cost not measured)")
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}")
    except (KeyError, TypeError, ValueError) as e:
        check(False, f"unexpected Langfuse response: {type(e).__name__}: {e}")
    return state


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each module opens")
    ap.add_argument("--env-file", type=Path, help="the private .env to read (default: the demo folder's .env)")
    ap.add_argument("--prep", action="store_true",
                    help="preparation only: skip the portal and MCP checks; the verdict says so")
    args = ap.parse_args(argv)
    del RESULTS[:]

    manifest_path = DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    path = args.env_file.resolve() if args.env_file else env_file(manifest_value(manifest, "", "profile"))
    print("bank-assistant-workshop readiness" + (" (preparation only)" if args.prep else ""))
    print("Config")
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="cp .env.example .env and fill it in")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    keys_ok = check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set")

    print("Local definitions")
    prompts: Dict[str, str] = {}
    items: List[dict] = []
    try:
        prompts = local_prompts()
        check(True, f"prompt text for {', '.join(prompts)} read from src/northwind/prompts.py")
    except (OSError, SyntaxError, KeyError, ValueError) as e:
        check(False, f"prompt text in src/northwind/prompts.py: {type(e).__name__}: {e}")
    try:
        items = local_items()
        check(True, f"{len(items)} dataset items read from data/seed-dataset.json")
    except (OSError, KeyError, TypeError, ValueError) as e:
        check(False, f"data/seed-dataset.json: {type(e).__name__}: {e}")

    if keys_ok and path.is_file():
        preflight_check(path)
        names = workshop_names()
        if not args.prep:
            service_checks(names)
        if prompts and items:
            lf = Langfuse(base, pk, sk)
            state = langfuse_checks(lf, expected, names, prompts, items)
            if args.links and state.get("project_id"):
                print("\nLinks")
                for label, url in act_links(lf.base, state):
                    print(f"  {label:<32} {url}")

    line, code = summary()
    if args.prep:
        line += " · preparation only: portal and MCP server not checked, run without --prep once serve is up"
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
