#!/usr/bin/env python3
"""Readiness check for bank-assistant-workshop: reads Langfuse back and says READY or NOT READY.

    .venv/bin/python scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    .venv/bin/python scripts/verify_demo.py --links    # also print the link each module opens
    .venv/bin/python scripts/verify_demo.py --prep     # before `workshop.py serve` is up: services not checked, verdict says so
    .venv/bin/python scripts/verify_demo.py --env-file ../../.env   # from a work/<checkpoint> folder

Session readiness means: `workshop.py preflight` passes (packages, model key present,
Langfuse authentication; no paid model call); the portal's /health reports tracing on for
this WORKSHOP_ID; the banking MCP server answers an MCP `initialize` handshake as
northwind-core-banking (no tool is listed or called; the session is closed again); the
env file and LANGFUSE_BASE_URL parse before anything is requested; the keys map to the expected project; both prompt labels serve the text in
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
import itertools
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
from typing import Any, Dict, Iterator, List, MutableMapping, Optional, Tuple

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
MCP_SERVER_NAME = "northwind-core-banking"        # mcp_server.FastMCP(...) name, sent back by initialize
MCP_PROTOCOL_VERSION = "2025-06-18"               # what the verifier offers; the server answers with its own
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
        try:
            data = None if body is None else json.dumps(body).encode()
            headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
            # Building the request parses the URL: a malformed base such as http://[::gg]
            # raises ValueError here (http.client.InvalidURL is one too), not at send time.
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or e.reason) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        except ValueError as e:
            raise ApiError(None, f"cannot build a request to {self.base}{path}: {e}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def rows(self, path: str, params: Dict[str, Any], limit: int = 100, max_pages: int = 20) -> Iterator[dict]:
        """Yield rows across `meta.cursor` pages (Observations v2, Scores v3, experiments), so a
        caller can stop at the first row it wants. A page may be empty while a cursor is still
        returned, so stop on a missing cursor, not on an empty page. A repeated cursor or more
        than `max_pages` pages is an ApiError, not a silent cut."""
        cur = None
        seen = set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=limit, **({"cursor": cur} if cur else {}))) or {}
            page = d.get("data") or []
            if not isinstance(page, list):
                raise ApiError(None, f"{path}: 'data' is not a list")
            yield from page
            cur = (d.get("meta") or {}).get("cursor")
            if not cur:
                return
            if cur in seen:
                raise ApiError(None, f"{path}: the API repeated cursor {cur!r}")
            seen.add(cur)
        raise ApiError(None, f"{path}: still paging after {max_pages} pages")

    def cursor(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = 20) -> List[dict]:
        """Every row `rows()` yields, up to `cap`."""
        return list(itertools.islice(self.rows(path, params, max_pages=max_pages), cap))


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

def http_request(method: str, url: str, body: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
                 timeout: float = 4) -> Tuple[Optional[int], Dict[str, str], bytes]:
    """(status, lower-cased headers, body) for a local service; (None, {}, b"") when nothing answers."""
    try:
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in (r.headers or {}).items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {}, b""
    except (urllib.error.URLError, OSError, ValueError):
        return None, {}, b""


def http_get(url: str, timeout: float = 4) -> Tuple[Optional[int], bytes]:
    """(status, body) for a local service; (None, b"") when nothing answers."""
    status, _, body = http_request("GET", url, headers={"Accept": "application/json"}, timeout=timeout)
    return status, body


def mcp_initialize(url: str, timeout: float = 4) -> Tuple[Optional[int], Optional[dict]]:
    """POST the JSON-RPC `initialize` every MCP client opens with, and return (status, result).

    The handshake lists and calls no tool, so nothing reaches the model or the fictional
    accounts. FastMCP's streamable HTTP transport answers 200 with one SSE `message` event
    (or JSON, in json_response mode) and opens a transport session, which is closed again
    with DELETE. A bare GET only says 406 to any client that will not take an event stream,
    which an unrelated server can mimic; a server name cannot be mimicked by accident.
    """
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                      "clientInfo": {"name": "demo-verify", "version": "1"}}}
    status, headers, raw = http_request("POST", url, json.dumps(req).encode(), {
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}, timeout)
    if status != 200:
        return status, None
    text = raw.decode("utf-8", "replace")
    if headers.get("content-type", "").startswith("text/event-stream"):
        bodies = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    else:
        bodies = [text]
    result = None
    for chunk in bodies:
        try:
            msg = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == 1 and isinstance(msg.get("result"), dict):
            result = msg["result"]
            break
    session = headers.get("mcp-session-id")
    if session:
        http_request("DELETE", url, headers={"mcp-session-id": session,
                                             "MCP-Protocol-Version": str((result or {}).get("protocolVersion")
                                                                         or MCP_PROTOCOL_VERSION)}, timeout=timeout)
    return status, result


def local_port(environ: MutableMapping[str, str], key: str, default: int) -> int:
    """A port from the env file, or ValueError naming the variable."""
    raw = environ.get(key) or str(default)
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"{key}={raw!r} is not a port number") from None
    if not 0 < port < 65536:
        raise ValueError(f"{key}={raw!r} is not a port number")
    return port


def base_url_problem(base: str) -> Optional[str]:
    """Why LANGFUSE_BASE_URL cannot be requested, or None. Parses it the way urllib will."""
    try:
        parts = urllib.parse.urlsplit(base)
        parts.port  # raises ValueError for a non-numeric or out-of-range port
    except ValueError as e:
        return f"{base!r} is not a valid URL: {e}"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"{base!r} is not an http(s) URL with a host"
    return None


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


def service_checks(names: Dict[str, str], ports: Dict[str, int]) -> None:
    """The facilitator's portal and banking MCP server, started by `workshop.py serve`."""
    print("Services")
    port = ports["portal"]
    status, body = http_get(f"http://127.0.0.1:{port}/health")
    try:
        health = json.loads(body) if status == 200 else {}
    except ValueError:
        health = {}
    health = health if isinstance(health, dict) else {}
    ok = status == 200 and health.get("status") == "ok" and health.get("tracing") is True \
        and health.get("workshop") == names["workshop_id"]
    check(ok, f"portal on 127.0.0.1:{port}/health: HTTP {status}, tracing {health.get('tracing')}, "
              f"workshop {health.get('workshop')} (want {names['workshop_id']})",
          hint=f"{SERVE_CMD} from the reference root with this .env")
    mcp = ports["mcp"]
    status, result = mcp_initialize(f"http://127.0.0.1:{mcp}/mcp")
    server = ((result or {}).get("serverInfo") or {}).get("name") if isinstance(result, dict) else None
    protocol = (result or {}).get("protocolVersion") if isinstance(result, dict) else None
    if status != 200:
        detail = f"initialize got HTTP {status}"
    elif result is None:
        detail = "initialize got HTTP 200 but no MCP result"
    else:
        detail = f"initialize HTTP 200, server {server}, protocol {protocol}"
    check(status == 200 and server == MCP_SERVER_NAME and bool(protocol),
          f"banking MCP server on 127.0.0.1:{mcp}/mcp: {detail} (want {MCP_SERVER_NAME})",
          hint=f"{SERVE_CMD}; another process may hold port {mcp} (NORTHWIND_MCP_PORT)")


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
        # Newest first. Follow the cursor to the first eligible root: a page can come back
        # empty with a continuation cursor, and the prepared turn is then on a later page.
        roots = lf.rows("/api/public/v2/observations", dict(
            name=ROOT_NAME, isRootObservation=True, environment=ENVIRONMENT, fields="core,basic", **window),
            limit=10)
        latest = next((r for r in roots if r.get("name") == ROOT_NAME and r.get("environment") == ENVIRONMENT
                       and r.get("isRootObservation") is True), None)
        if check(latest is not None,
                 f"a '{ROOT_NAME}' turn in environment '{ENVIRONMENT}' in the last {LOOKBACK_DAYS} days",
                 hint=RUN_CMD):
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


def readiness(args: argparse.Namespace) -> None:
    """Every check, in order. Expected failures become FAIL lines here; main() catches the rest."""
    manifest_path = DEMO_DIR / "demo.yaml"
    print("Config")
    try:
        manifest = manifest_path.read_text()
    except OSError as e:
        manifest = ""
        check(False, f"demo.yaml unreadable: {type(e).__name__}: {e}")
    path = args.env_file.resolve() if args.env_file else env_file(manifest_value(manifest, "", "profile"))
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    try:
        loaded = load_env(path)
    except (OSError, UnicodeDecodeError) as e:
        loaded = False
        check(False, f"env file {shown} unreadable: {type(e).__name__}: {e}")
    else:
        check(loaded, f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
              hint="cp .env.example .env and fill it in")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    keys_ok = check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set")
    if keys_ok:
        problem = base_url_problem(base)
        keys_ok = check(problem is None, f"LANGFUSE_BASE_URL is an http(s) URL" + (f": {problem}" if problem else ""),
                        hint="e.g. https://us.cloud.langfuse.com")
    ports: Dict[str, int] = {}
    for key, name, default in (("WORKSHOP_PORT", "portal", DEFAULT_PORTAL_PORT),
                               ("NORTHWIND_MCP_PORT", "mcp", DEFAULT_MCP_PORT)):
        try:
            ports[name] = local_port(os.environ, key, default)
        except ValueError as e:
            check(False, str(e), hint="set a number from 1 to 65535, or remove the line for the default")

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

    if keys_ok and loaded:
        preflight_check(path)
        names = workshop_names()
        if not args.prep and len(ports) == 2:
            service_checks(names, ports)
        if prompts and items:
            lf = Langfuse(base, pk, sk)
            state = langfuse_checks(lf, expected, names, prompts, items)
            if args.links and state.get("project_id"):
                print("\nLinks")
                for label, url in act_links(lf.base, state):
                    print(f"  {label:<32} {url}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each module opens")
    ap.add_argument("--env-file", type=Path, help="the private .env to read (default: the demo folder's .env)")
    ap.add_argument("--prep", action="store_true",
                    help="preparation only: skip the portal and MCP checks; the verdict says so")
    args = ap.parse_args(argv)  # --help and usage errors exit here, as usual
    del RESULTS[:]
    print("bank-assistant-workshop readiness" + (" (preparation only)" if args.prep else ""))
    try:
        readiness(args)
    except Exception as e:  # noqa: BLE001  an unexpected error is a failed check; the verdict still prints
        check(False, f"verifier stopped early: {type(e).__name__}: {e}",
              hint="the checks above ran; the rest did not")
    line, code = summary()
    if args.prep:
        line += " · preparation only: portal and MCP server not checked, run without --prep once serve is up"
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
