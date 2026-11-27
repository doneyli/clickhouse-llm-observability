#!/usr/bin/env python3
"""Readiness check for text-to-sql: reads Langfuse back and says READY or NOT READY.

    python demos/text-to-sql/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    python demos/text-to-sql/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root. It reads the root `.env` (this is a `container` demo) and
ignores shell-exported LANGFUSE_* keys.

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows. Standard library only, so it runs in any Python 3.9+ without
the demo's dependencies. No model calls; a handful of Langfuse reads, done in seconds.

Read APIs: prompts v2, datasets v2 and dataset items, Scores API v3, and Observations API
v2. The self-hosted stack pins Langfuse server 3.221.1, where `/api/public/v2/observations`
returns 404, so on a 404 the observation reads fall back to the v1 observations list that
server still serves (the same boundary `api.legacy.observations_v1` covers in the SDK).
Langfuse Cloud answers v2, so the fallback never runs there.

Checks for Act 3 (the refine loop) and Act 6 only warn: both acts are optional.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DEMO_DIR.parent.parent

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with main.py and seed_all.py.
TRACE_NAME = "text-to-sql"                      # root observation langfuse_trace() opens (langfuse_config.py)
# prompt name -> labels scripts/seed-app-prompts.py sets. Required: Acts 1, 2 and 5.
EXPECTED_PROMPTS: Dict[str, List[str]] = {
    "text-to-sql-analysis": ["production", "candidate"],
    "text-to-sql-response": ["production"],
    "text-to-sql-gate-grounding": ["production"],
}
# The refine loop's prompts (Act 3, optional): a missing one only warns.
OPTIONAL_PROMPTS: Dict[str, List[str]] = {
    "text-to-sql-generator": ["production", "candidate"],
    "text-to-sql-critic": ["production", "opinion-only"],
}
# dataset name -> minimum item count (seed_step_dataset.py, seed_refine_datasets.py)
EXPECTED_DATASETS: Dict[str, int] = {"text-to-sql-analysis-step": 8}                   # Act 5
OPTIONAL_DATASETS: Dict[str, int] = {"text-to-sql/critic-accuracy": 10,               # Act 3
                                     "text-to-sql/converged-sql": 6}
# Scores the code evaluators (scripts/seed-code-evaluators.sh) write on every text-to-sql
# trace at ingest: chain-gate-check on each gate-* span, sql-safety-guard on each generation.
EXPECTED_SCORES = ["gate-pass", "sql-risk"]
MIN_OBSERVATIONS = 5                            # root, analysis, gate-database-selection, retrieve-context, response, ...
LOOKBACK_DAYS = 7                               # how far back to look for a run
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
# Local services the acts need: (name, host, port). mcp-clickhouse speaks SSE, so a TCP
# connect is the check (an HTTP GET on /sse would hold a stream open).
SERVICES: List[Tuple[str, str, int]] = [("mcp-clickhouse", "localhost", 8001)]
SEED_CMD = "python demos/text-to-sql/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm text-to-sql python main.py"
TIMEOUT_S = 10

RESULTS: List[Tuple[str, bool, str]] = []       # (PASS | FAIL | WARN, required, what)



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
        """Follow `meta.cursor` (Observations v2, Scores v3). A page may be empty while a cursor
        is still returned, so stop on a missing cursor, not on an empty page."""
        out: List[dict] = []
        cur = None
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=100, **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                break
        return out[:cap]



# --- Observations: v2, or v1 on a server that predates it ----------------------------------

def latest_root(lf: Langfuse, now: datetime) -> Tuple[Optional[dict], str]:
    """The newest root observation named TRACE_NAME in the lookback window, and the API that
    answered ("v2" or "v1"). Server 3.221.1 has no Observations v2, so a 404 falls back to v1."""
    window = {"fromStartTime": iso(now - timedelta(days=LOOKBACK_DAYS)),
              "toStartTime": iso(now + timedelta(minutes=5))}
    try:
        rows = (lf.get("/api/public/v2/observations", name=TRACE_NAME, isRootObservation=True,
                       fields="core,basic", limit=1, **window) or {}).get("data") or []  # newest first
        return (rows[0] if rows else None), "v2"
    except ApiError as e:
        if e.status != 404:
            raise
    rows: List[dict] = []
    for page in range(1, 6):
        d = lf.get("/api/public/observations", name=TRACE_NAME, limit=100, page=page, **window) or {}
        rows += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
    roots = [r for r in rows if not r.get("parentObservationId") and r.get("startTime")]
    roots.sort(key=lambda r: parse_time(r["startTime"]), reverse=True)
    return (roots[0] if roots else None), "v1"


def count_observations(lf: Langfuse, trace_id: str, started: datetime, now: datetime, api: str) -> int:
    """How many observations the trace has, on the same API version latest_root() used."""
    if api == "v2":
        return len(lf.cursor("/api/public/v2/observations", {
            "traceId": trace_id, "fields": "core",
            "fromStartTime": iso(started - timedelta(hours=1)), "toStartTime": iso(now + timedelta(minutes=5))}))
    d = lf.get("/api/public/observations", traceId=trace_id, limit=1, page=1) or {}
    total = (d.get("meta") or {}).get("totalItems")
    return int(total) if total is not None else len(d.get("data") or [])


def find_datasets(lf: Langfuse, names: List[str]) -> Dict[str, dict]:
    """Datasets by name, from the paged v2 list. Listing avoids putting a name such as
    `text-to-sql/critic-accuracy` into a URL path, where the slash would be ambiguous."""
    found: Dict[str, dict] = {}
    for page in range(1, 11):
        d = lf.get("/api/public/v2/datasets", page=page, limit=100) or {}
        for row in d.get("data") or []:
            if row.get("name") in names:
                found[row["name"]] = row
        if len(found) == len(names) or page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
    return found


def dataset_item_count(lf: Langfuse, name: str) -> int:
    d = lf.get("/api/public/dataset-items", datasetName=name, limit=1, page=1) or {}
    total = (d.get("meta") or {}).get("totalItems")
    return int(total) if total is not None else len(d.get("data") or [])


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """DEMO-SPECIFIC: one (label, url) per act of the demo script, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    ds = state.get("dataset_ids") or {}
    trace = f"{p}/traces/{state['trace_id']}" if state.get("trace_id") else f"{p}/traces"
    links = [("Act 1 · the latest run", trace),
             ("Act 2 · its gate spans", trace)]
    if ds.get("text-to-sql/converged-sql"):
        links.append(("Act 3 · converged-sql runs", f"{p}/datasets/{ds['text-to-sql/converged-sql']}"))
    links.append(("Act 4 · scores", f"{p}/scores"))
    links.append(("Act 5 · the analysis prompt", f"{p}/prompts/{urllib.parse.quote('text-to-sql-analysis', safe='')}"))
    if ds.get("text-to-sql-analysis-step"):
        links.append(("Act 5 · step dataset runs", f"{p}/datasets/{ds['text-to-sql-analysis-step']}"))
    links.append(("Act 6 · traces (tag test-scenario)", f"{p}/traces"))
    return links


# --- Checks -------------------------------------------------------------------------------

def service_checks() -> None:
    if not SERVICES:
        return
    print("Services")
    for name, host, port in SERVICES:
        try:
            with socket.create_connection((host, port), timeout=3):
                check(True, f"{name} listening on {host}:{port}")
        except OSError as e:
            check(False, f"{name} not reachable on {host}:{port}: {type(e).__name__}",
                  hint="docker compose --profile langfuse up -d (mcp-clickhouse has no profile and starts with it)")


def prompt_checks(lf: Langfuse, prompts: Dict[str, List[str]], required: bool, act: str) -> None:
    for name, labels in prompts.items():
        for label in labels:
            try:
                p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label) or {}
                check(True, f"prompt {name}: {label} = v{p.get('version')}", required=required)
            except ApiError as e:
                check(False, f"prompt {name} with label {label}{act}: {e}", required=required,
                      hint=f"run {SEED_CMD}")


def dataset_checks(lf: Langfuse, datasets: Dict[str, int], required: bool, act: str,
                   state: Dict[str, Any]) -> None:
    found = find_datasets(lf, list(datasets))
    for name, want in datasets.items():
        row = found.get(name)
        if not row:
            check(False, f"dataset {name}{act} is missing", required=required, hint=f"run {SEED_CMD}")
            continue
        state.setdefault("dataset_ids", {})[name] = row.get("id")
        n = dataset_item_count(lf, name)
        check(n >= want, f"dataset {name}: {n} item(s) (want {want}+)", required=required,
              hint=f"run {SEED_CMD}")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or wrong LANGFUSE_BASE_URL; network error: is Langfuse up? (./setup.sh)")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in the root .env, or set DEMO_LANGFUSE_PROJECT"):
        return state

    try:
        print("Seeded artifacts")
        prompt_checks(lf, EXPECTED_PROMPTS, True, "")
        prompt_checks(lf, OPTIONAL_PROMPTS, False, " (Act 3)")
        dataset_checks(lf, EXPECTED_DATASETS, True, " (Act 5)", state)
        dataset_checks(lf, OPTIONAL_DATASETS, False, " (Act 3)", state)

        print("Latest run")
        now = datetime.now(timezone.utc)
        latest, api = latest_root(lf, now)
        state["observations_api"] = api
        if not check(latest is not None, f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days"
                     + (" (Observations v1: this server predates v2)" if api == "v1" else ""),
                     hint=f"run {RUN_CMD}"):
            return state
        state["trace_id"] = trace_id = latest["traceId"]
        started = parse_time(latest["startTime"])
        n = count_observations(lf, trace_id, started, now, api)
        check(n >= MIN_OBSERVATIONS, f"latest run has {n} observation(s) (want {MIN_OBSERVATIONS}+)",
              hint="observations may still be landing: wait 30 s and re-run")
        names = sorted({s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})})
        missing = [x for x in EXPECTED_SCORES if x not in names]
        check(not missing, f"latest run's scores: {', '.join(names) or 'none'}"
              + (f"; missing {', '.join(missing)}" if missing else ""),
              hint="code evaluators score about 30 s after ingest; if still missing, run "
                   "./scripts/seed-code-evaluators.sh")
        age_h = (now - started).total_seconds() / 3600
        check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
              required=False, hint=f"run {RUN_CMD} the day before")
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}")
    return state


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    args = ap.parse_args(argv)
    del RESULTS[:]

    manifest_path = DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    path = env_file(manifest_value(manifest, "", "profile"))
    print(f"{TRACE_NAME} readiness")
    print("Config")
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="cp .env.example .env and fill it in" if path.parent == DEMO_DIR else "run ./setup.sh at the repo root")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set"):
        service_checks()
        lf = Langfuse(base, pk, sk)
        state = langfuse_checks(lf, expected)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<36} {url}")

    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
