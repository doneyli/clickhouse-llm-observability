#!/usr/bin/env python3
"""Readiness check for agentic-rag: reads ClickHouse and Langfuse back, says READY or NOT READY.

    python demos/agentic-rag/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    python demos/agentic-rag/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root. It reads the root `.env` (this is a `container` demo) and
ignores shell-exported LANGFUSE_* keys.

What it checks: the keys map to the manifest's Langfuse project; clickhouse-vectors is up
and `agentic_rag.kb_chunks` holds chunks behind its HNSW index (Act 1); the
`agentic-rag-generation` prompt serves a `production` version (Act 3b); the three managed
judge rules are ACTIVE (self-hosted, read from Postgres); the latest knowledge-base run
has the typed graph observations and its in-graph scores (Acts 2-3); a self-correcting
run exists (Act 2); the services Act 4 needs are up (warnings only).

An HTTP 200 proves little on its own (a trace can exist before its observations land),
so every check counts real rows. Standard library only, so it runs in any Python 3.9+
without the demo's dependencies. No model calls; a few reads, done in seconds.

Read APIs: Observations v2 and Scores v3. The repo's self-hosted server (3.221.1) answers
Observations v2 with 404, so on that 404 only the observation reads fall back to the
Observations v1 list endpoint, the read CLAUDE.md names for self-hosted. No legacy trace
endpoints are used.
"""
from __future__ import annotations

import argparse
import base64
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
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DEMO_DIR.parent.parent

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with graph.py and seed_all.py.
TRACE_NAME = "agentic-rag"                      # graph.py run(): lf.trace_context("agentic-rag", ...)
ANCHOR_OBSERVATION = "grade-relevance"          # only knowledge-base runs grade, and they carry the scores
ROUTER_HANDLER = "agentic-rag-handler"          # the graph nested under query-router: that demo's trace
EXPECTED_OBSERVATIONS = ["route", "retrieve", "grade-relevance", "generate", "reflect-groundedness"]
EXPECTED_SCORES = ["retrieval_relevance", "groundedness"]                   # in-graph, every kb run
JUDGE_SCORES = ["faithfulness", "context-relevance", "answer-relevance"]   # managed judges, async
JUDGE_RULES = ["arag-faithfulness", "arag-context-relevance", "arag-answer-relevance"]
EXPECTED_PROMPTS = {"agentic-rag-generation": "production"}  # prompt name -> label seed_all.py sets
MIN_PROMPT_VERSIONS = 2                         # Act 3b shows the baseline and the production version
VECTORS_DB, VECTORS_TABLE = "agentic_rag", "kb_chunks"
LOOKBACK_DAYS = 7                               # how far back to look for a run
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
MAX_CANDIDATES = 5                              # traces inspected when looking for the latest run
SEED_CMD = "python demos/agentic-rag/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm agentic-rag python main.py"
JUDGE_SEED_CMD = "./scripts/seed-agentic-rag-evaluators.sh"
TIMEOUT_S = 10

RESULTS: List[Tuple[str, bool, str]] = []       # (PASS | FAIL | WARN, required, what)


def services(env: MutableMapping[str, str]) -> List[Tuple[str, str, bool]]:
    """(name, url, required) for the local services the acts need, on the ports .env sets."""
    return [
        ("clickhouse-vectors", f"http://localhost:{env.get('CLICKHOUSE_VECTORS_HTTP_PORT') or 8125}/ping", True),
        ("agentic-rag API (Act 4, HTTP re-run)", f"http://localhost:{env.get('AGENTIC_RAG_PORT') or 8006}/health", False),
        ("mcp-rag-retriever (Act 4)", f"http://localhost:{env.get('MCP_RAG_RETRIEVER_PORT') or 8007}/sse", False),
        ("LibreChat (Act 4)", "http://localhost:3080/health", False),
    ]


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


def page_v1(lf: Langfuse, path: str, params: Dict[str, Any], cap: int = 500, max_pages: int = 10) -> List[dict]:
    """Follow `meta.totalPages` (Observations v1, the self-hosted 3.x fallback)."""
    out: List[dict] = []
    for page in range(1, max_pages + 1):
        d = lf.get(path, **dict(params, limit=100, page=page)) or {}
        out += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1) or len(out) >= cap:
            break
    return out[:cap]


class Observations:
    """Observation reads: Observations v2, or v1 when the server answers v2 with 404.

    A v3 server (this repo's self-hosted 3.221.1) has no v2 observations; Langfuse Cloud
    and v4 servers do. v2 filters on the trace name; v1 cannot, so on v1 a trace that the
    query-router owns is told apart by its `agentic-rag-handler` observation instead.
    """

    def __init__(self, lf: Langfuse) -> None:
        self.lf = lf
        self.api: Optional[str] = None

    def anchors(self, since: str, until: str) -> List[dict]:
        params = {"name": ANCHOR_OBSERVATION, "fromStartTime": since, "toStartTime": until}
        try:
            trace_name = [{"type": "string", "column": "traceName", "operator": "=", "value": TRACE_NAME}]
            d = self.lf.get("/api/public/v2/observations", **params, fields="core,basic", limit=50,
                            filter=json.dumps(trace_name)) or {}
            self.api = "v2"
            return d.get("data") or []
        except ApiError as e:
            if e.status != 404:
                raise
        self.api = "v1"
        return page_v1(self.lf, "/api/public/observations", params, cap=100, max_pages=1)

    def of_trace(self, trace_id: str, since: str, until: str) -> List[dict]:
        if self.api == "v1":
            return page_v1(self.lf, "/api/public/observations", {"traceId": trace_id})
        return self.lf.cursor("/api/public/v2/observations", {
            "traceId": trace_id, "fields": "core", "fromStartTime": since, "toStartTime": until})


def newest_traces(rows: Iterable[dict]) -> List[Tuple[str, datetime]]:
    """(traceId, startTime) per distinct trace, newest first."""
    seen, out = set(), []
    dated = [(parse_time(r["startTime"]), r.get("traceId")) for r in rows if r.get("startTime")]
    for started, trace_id in sorted(dated, key=lambda x: x[0], reverse=True):
        if trace_id and trace_id not in seen:
            seen.add(trace_id)
            out.append((trace_id, started))
    return out


def find_latest_run(reader: Observations, now: datetime) -> Optional[Dict[str, Any]]:
    """The newest knowledge-base `agentic-rag` run this demo owns, with its observations."""
    until = iso(now + timedelta(minutes=5))
    anchors = reader.anchors(iso(now - timedelta(days=LOOKBACK_DAYS)), until)
    for trace_id, started in newest_traces(anchors)[:MAX_CANDIDATES]:
        rows = reader.of_trace(trace_id, iso(started - timedelta(hours=1)), until)
        if any(r.get("name") == ROUTER_HANDLER for r in rows):
            continue  # the query-router demo's trace, which nests this graph
        return {"trace_id": trace_id, "started": started, "observations": rows}
    return None


def score_trace(score: dict) -> Optional[str]:
    """The trace a Scores v3 row belongs to (needs `fields=subject`)."""
    sub = score.get("subject") or {}
    return sub.get("traceId") or (sub.get("id") if sub.get("kind") == "trace" else None) or score.get("traceId")


def self_correcting_traces(scores: Iterable[dict]) -> List[str]:
    """Traces whose retrieval was graded 0 and then 1: the Act 2 loop. Newest first."""
    values: Dict[str, set] = {}
    latest: Dict[str, str] = {}
    for s in scores:
        t = score_trace(s)
        if not t or s.get("value") is None:
            continue
        values.setdefault(t, set()).add(float(s["value"]))
        latest[t] = max(latest.get(t, ""), s.get("timestamp") or "")
    hits = [t for t, vs in values.items() if {0.0, 1.0} <= vs]
    return sorted(hits, key=lambda t: latest[t], reverse=True)


# --- ClickHouse and Postgres reads ---------------------------------------------------------

def ch_query(base: str, user: str, password: str, sql: str, timeout: float = TIMEOUT_S) -> str:
    """Run a read-only query on the ClickHouse HTTP interface and return the raw text."""
    req = urllib.request.Request(f"{base}/?{urllib.parse.urlencode({'query': sql})}",
                                 headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": password})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:160] if e.fp else ""
        raise ApiError(e.code, detail.strip() or str(e.reason)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None


def parse_rule_rows(text: str) -> Dict[str, str]:
    """`id|status` lines from psql -t -A into {id: status}."""
    out = {}
    for line in text.splitlines():
        if "|" in line:
            rid, status = line.strip().split("|", 1)
            out[rid] = status
    return out


def judge_rule_status(project_id: str, container: str) -> Dict[str, str]:
    """Read-only: the judge rules seed-agentic-rag-evaluators.sh upserts, from Langfuse's Postgres.

    The 3.x server has no public evaluator API, so the seeder writes `job_configurations`
    directly and this reads the same rows back. Raises RuntimeError when Postgres is not
    reachable through docker.
    """
    ids = ", ".join(f"'{i}'" for i in JUDGE_RULES)
    pid = project_id.replace("'", "''")
    sql = f"SELECT id || '|' || status FROM job_configurations WHERE project_id = '{pid}' AND id IN ({ids})"
    try:
        p = subprocess.run(["docker", "exec", container, "sh", "-c",
                            'psql -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$0"', sql],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"docker not usable: {type(e).__name__}") from None
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:160] or f"docker exec exited {p.returncode}")
    return parse_rule_rows(p.stdout)


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per act of the demo script that opens a page, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = []
    if state.get("self_correcting_trace_id"):
        links.append(("Act 2 · self-correcting run", f"{p}/traces/{state['self_correcting_trace_id']}"))
    if state.get("trace_id"):
        links.append(("Act 3 · latest scored run", f"{p}/traces/{state['trace_id']}"))
    links.append(("Act 3 · sessions", f"{p}/sessions"))
    links.append(("Act 3 · judges", f"{p}/evals"))
    first_prompt = next(iter(EXPECTED_PROMPTS))
    links.append(("Act 3b · the prompt", f"{p}/prompts/{urllib.parse.quote(first_prompt, safe='')}"))
    links.append(("Act 4 · LibreChat", "http://localhost:3080"))
    return links


# --- Checks -------------------------------------------------------------------------------

def service_checks(env: MutableMapping[str, str]) -> None:
    print("Services")
    for name, url, required in services(env):
        hint = "docker compose --profile demo up -d clickhouse-vectors agentic-rag mcp-rag-retriever"
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
                check(r.status < 500, f"{name} up at {url} ({r.status})", required=required)
        except urllib.error.HTTPError as e:
            check(e.code < 500, f"{name} up at {url} ({e.code})", required=required, hint=hint)
        except (urllib.error.URLError, OSError) as e:
            check(False, f"{name} not reachable at {url}: {type(e).__name__}", required=required,
                  hint="./setup.sh starts LibreChat" if "LibreChat" in name else hint)


def vector_checks(env: MutableMapping[str, str]) -> None:
    print("Vector store (Act 1)")
    base = f"http://localhost:{env.get('CLICKHOUSE_VECTORS_HTTP_PORT') or 8125}"
    user, password = env.get("CH_VECTORS_USER") or "agentic", env.get("CH_VECTORS_PASSWORD") or "agentic123"
    try:
        count = int(ch_query(base, user, password, f"SELECT count() FROM {VECTORS_DB}.{VECTORS_TABLE}") or 0)
        check(count > 0, f"{VECTORS_DB}.{VECTORS_TABLE} holds {count} chunk(s)", hint=f"run {SEED_CMD}")
        index = ch_query(base, user, password,
                         "SELECT type_full FROM system.data_skipping_indices "
                         f"WHERE database = '{VECTORS_DB}' AND table = '{VECTORS_TABLE}' AND name = 'vec_idx'")
        check(index.startswith("vector_similarity"), f"vec_idx is a vector_similarity index ({index or 'missing'})",
              hint=f"run {SEED_CMD}")
    except (ApiError, ValueError) as e:
        check(False, f"{VECTORS_DB}.{VECTORS_TABLE} readable: {e}", hint=f"run {SEED_CMD}")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str], deploy_mode: str) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
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
                 hint="use this project's keys in .env, or fix langfuse.project in demo.yaml"):
        return state

    try:
        print("Seeded artifacts")
        for name, label in EXPECTED_PROMPTS.items():
            quoted = urllib.parse.quote(name, safe="")
            try:
                p = lf.get(f"/api/public/v2/prompts/{quoted}", label=label) or {}
                text = p.get("prompt") if isinstance(p.get("prompt"), str) else ""
                check("{{context}}" in text and "{{question}}" in text,
                      f"prompt {name}: {label} = v{p.get('version')}, with {{{{context}}}} and {{{{question}}}}",
                      hint=f"run {SEED_CMD}")
            except ApiError as e:
                check(False, f"prompt {name} with label {label}: {e}", hint=f"run {SEED_CMD}")
                continue
            listed = (lf.get("/api/public/v2/prompts", name=name) or {}).get("data") or []
            versions = (listed[0].get("versions") if listed else None) or []
            check(len(versions) >= MIN_PROMPT_VERSIONS,
                  f"prompt {name} has {len(versions)} version(s) (Act 3b shows {MIN_PROMPT_VERSIONS}+)",
                  required=False, hint=f"run {SEED_CMD}")

        if deploy_mode == "cloud":
            check(False, "judge rules: not readable in cloud mode (create them in the Langfuse UI)",
                  required=False, hint=f"{JUDGE_SEED_CMD} prints the settings")
        else:
            try:
                rules = judge_rule_status(state["project_id"] or "",
                                          os.environ.get("LANGFUSE_POSTGRES_CONTAINER") or "langfuse-postgres")
                active = [r for r in JUDGE_RULES if rules.get(r) == "ACTIVE"]
                check(len(active) == len(JUDGE_RULES),
                      f"{len(active)}/{len(JUDGE_RULES)} judge rules ACTIVE"
                      + "".join(f"; {r} {rules.get(r, 'missing')}" for r in JUDGE_RULES if r not in active),
                      hint=f"run {SEED_CMD} (BLOCKED means no default eval model: ./setup.sh)")
            except RuntimeError as e:
                check(False, f"judge rules not readable from Postgres: {e}", required=False,
                      hint="is the langfuse profile up?")

        print("Latest knowledge-base run (Acts 2-3)")
        now = datetime.now(timezone.utc)
        reader = Observations(lf)
        run = find_latest_run(reader, now)
        if not check(run is not None, f"a scored '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days",
                     hint=f"run {RUN_CMD}"):
            return state
        state["trace_id"] = trace_id = run["trace_id"]
        names = {o.get("name") for o in run["observations"]}
        missing = [n for n in EXPECTED_OBSERVATIONS if n not in names]
        check(not missing, f"latest run has {len(run['observations'])} observation(s) (Observations {reader.api})"
              + (f"; missing {', '.join(missing)}" if missing else f", incl. {', '.join(EXPECTED_OBSERVATIONS)}"),
              hint="observations may still be landing: wait 30 s and re-run")
        scores = lf.cursor("/api/public/v3/scores", {"traceId": trace_id})
        score_names = sorted({s.get("name") for s in scores if s.get("name")})
        missing = [n for n in EXPECTED_SCORES if n not in score_names]
        check(not missing, f"latest run's scores: {', '.join(score_names) or 'none'}"
              + (f"; missing {', '.join(missing)}" if missing else ""),
              hint="scores land after the trace: wait and re-run")
        missing = [n for n in JUDGE_SCORES if n not in score_names]
        check(not missing, "managed judges scored the latest run"
              + (f"; missing {', '.join(missing)}" if missing else ""), required=False,
              hint="judges score NEW traces only, about a minute later: re-run the demo")
        age_h = (now - run["started"]).total_seconds() / 3600
        check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
              required=False, hint=f"run {RUN_CMD} the day before")

        rr = lf.cursor("/api/public/v3/scores", {"name": "retrieval_relevance", "fields": "subject",
                                                 "fromTimestamp": iso(now - timedelta(days=LOOKBACK_DAYS))})
        loops = self_correcting_traces(rr)
        if loops:
            state["self_correcting_trace_id"] = loops[0]
        check(bool(loops), f"{len(loops)} self-correcting run(s) (retrieval_relevance 0 then 1) "
              f"in the last {LOOKBACK_DAYS} days", required=False,
              hint="run Act 2's command (DEMO_QUESTIONS.md Q3) and pin that trace")
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
        service_checks(os.environ)
        vector_checks(os.environ)
        lf = Langfuse(base, pk, sk)
        state = langfuse_checks(lf, expected, os.environ.get("DEPLOY_MODE") or "self-hosted")
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<30} {url}")

    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
