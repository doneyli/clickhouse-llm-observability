#!/usr/bin/env python3
"""Readiness check for slow-query-tuner: reads Langfuse and the tuning lab back, says READY or NOT READY.

    python demos/slow-query-tuner/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check
    python demos/slow-query-tuner/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root on the host. It drops shell-exported LANGFUSE_*, loads the root `.env`
and checks every required value before any network call. Then it reads the
`clickhouse-tuning` lab on its host port (as the read-only `tuner_agent`, plus a `SELECT 1` as
`tuner_admin` for Act 5), the checkpoints folder Act 3 resumes from, and Langfuse at
LANGFUSE_BASE_URL. Every check counts real rows: an HTTP 200 proves little on its own,
because a trace can exist before its observations land.

Standard library only, so it runs in any Python 3.9+ without the demo's dependencies. No model
calls and no writes: Langfuse GETs and read-only ClickHouse GETs, done in seconds. The final line
is the full presentation's verdict; the line above it is the short path's (Acts 1, 2, 4).

Reads use the current APIs: health, projects, prompts v2, datasets v2 and dataset items,
experiments, Observations v2 and Scores v3. The root stack pins a Langfuse v3 server, which
answers Observations v2 and experiments with 404; only on a 404 from a server whose health
reports a major version below 4 does the check fall back to the v1 observations read (which
CLAUDE.md names as the self-hosted path) and the dataset runs read. It never reads /traces.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DEMO_DIR.parent.parent

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with agent_loop.py and the seeders.
TRACE_NAME = "tune-clickhouse-query"            # root agent observation (agent_loop.py TRACE_NAME)
EXPERIMENT_RUN_PREFIX = "query-tuner prompt "    # scripts/run_experiment.py run names
# The five trace scores agent_loop.finalize() writes on every run that reaches the end.
EXPECTED_SCORES = ["turns_used", "run_cost_usd", "task_completed", "verified_speedup", "trajectory_efficiency"]
# Written by runaway-loop-guard (./scripts/seed-code-evaluators.sh) on every run; Act 4 shows them.
EVALUATOR_SCORES = ["cap_terminated", "termination_class"]
# The managed judge (./scripts/seed-query-tuner-evaluators.sh); shown in the bonus Act 6 only.
OPTIONAL_SCORES = ["goal_drift"]
MONITORS = ["query-tuner runaway cost", "query-tuner turn count"]  # scripts/seed_monitors.py
MIN_OBSERVATIONS = 2                            # the agent root plus at least one plan-next-action
LOOKBACK_DAYS = 7                               # how far back to look for a run
EXPERIMENT_LOOKBACK_DAYS = 30                   # how far back to look for an Act 6 experiment
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
LAB_ROWS = 30_000_000                           # sql/init/02_seed.sql inserts exactly this many
LAB_TABLE = "tuning_lab.web_events"
CHECKPOINTS = DEMO_DIR / "checkpoints"          # Act 3: run state for --resume, mounted into the container
SHORT_PATH_ACTS = {1, 2, 4}                     # DEMO_SCRIPT.md: Opening, Acts 1 + 2 + 4, Close
SEED_CMD = "python demos/slow-query-tuner/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm slow-query-tuner python main.py --query q1"
TIMEOUT_S = 10
MAX_PAGES = 50                                  # bound on any paginated read

RESULTS: List[Tuple[str, bool, str]] = []       # (PASS | FAIL | WARN, required, what)
ACTS: List[Optional[int]] = []                  # the act each result belongs to; None = every path


# --- Result helpers ---------------------------------------------------------------------

def check(ok: bool, what: str, *, required: bool = True, hint: str = "", act: Optional[int] = None) -> bool:
    """Record one check and print its line. A failed optional check is a WARN, not a FAIL.
    `act` names the one act that needs it; leave it None when every path needs it."""
    status = "PASS" if ok else ("FAIL" if required else "WARN")
    RESULTS.append((status, required, what))
    ACTS.append(act)
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
    return (f"NOT READY ({detail})", 1) if failed or not rs else (f"READY ({detail})", 0)


def short_path_summary() -> str:
    """The same verdict restricted to the checks the short path needs."""
    rs = [r for r, act in zip(RESULTS, ACTS) if act is None or act in SHORT_PATH_ACTS]
    line, _ = summary(rs)
    return f"Short path (Opening, Acts {', '.join(str(a) for a in sorted(SHORT_PATH_ACTS))}, Close): {line}"


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

    def cursor(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = MAX_PAGES) -> List[dict]:
        """Follow `meta.cursor` (Observations v2, Scores v3, experiments). A page may be empty
        while a cursor is still returned, so stop on a missing cursor, not on an empty page.
        A repeated cursor, or a cursor still present after `max_pages`, is an error: the read
        did not finish, so the caller must not treat what it has as complete."""
        out: List[dict] = []
        cur = None
        seen = set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=100, **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:
                raise ApiError(None, f"{path} returned the same cursor twice")
            seen.add(cur)
        raise ApiError(None, f"{path} still had a cursor after {max_pages} pages")

    def pages(self, path: str, params: Dict[str, Any], max_pages: int = MAX_PAGES) -> List[dict]:
        """Follow page-numbered reads (dataset items, and the pinned-v3 fallbacks)."""
        out: List[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params, page=page, limit=100)) or {}
            out += d.get("data") or []
            if page >= int((d.get("meta") or {}).get("totalPages") or 1):
                return out
        raise ApiError(None, f"{path} had more than {max_pages} pages")


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per act of DEMO_SCRIPT.md that opens a page, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = []
    if state.get("trace_id"):
        trace = f"{p}/traces/{state['trace_id']}"
        links.append(("Act 1 · the latest run", trace))
        links.append(("Act 2 · its Agent Graph", trace))
    links.append(("Act 3 · Sessions", f"{p}/sessions"))
    links.append(("Act 4 · the system prompt", f"{p}/prompts/{urllib.parse.quote('query-tuner-system', safe='')}"))
    links.append(("Act 4 · traces, for cost", f"{p}/traces"))
    if state.get("dataset_id"):
        links.append(("Act 6 · the dataset's runs", f"{p}/datasets/{state['dataset_id']}"))
    return links


def server_major(version: str) -> int:
    """Major version from a health response such as '3.221.1' or 'v4.30.0'; 0 if unknown."""
    digits = (version or "").lstrip("vV").split(".")[0]
    return int(digits) if digits.isdigit() else 0


# --- The tuning lab -----------------------------------------------------------------------

def clickhouse_query(url: str, user: str, password: str, sql: str, timeout: float = TIMEOUT_S) -> str:
    """Run one read-only statement over ClickHouse's HTTP interface and return the TSV body.
    A GET request is read-only on the server side as well."""
    req = urllib.request.Request(url.rstrip("/") + "/?" + urllib.parse.urlencode({"query": sql}),
                                 headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": password})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
        raise ApiError(e.code, detail.strip() or str(e.reason)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None


def lab_shape(ddl_tsv: str) -> Tuple[bool, str]:
    """Whether the lab table is still in its seeded, pessimal shape: ORDER BY tuple() with no
    projection and no skip index. Act 5's approved DDL adds one, and a leftover one makes q3
    fast without DDL and can let the --runaway target verify, which breaks Acts 4 and 5."""
    ddl = ddl_tsv.replace("\\n", "\n").replace("\\'", "'")
    m = re.search(r"\bORDER BY\s+([^\n]+)", ddl)
    order = m.group(1).strip() if m else "?"
    extras = [kind for kind in ("PROJECTION", "INDEX") if re.search(rf"^\s*{kind}\s", ddl, re.M)]
    ok = order.startswith("tuple()") and not extras
    detail = f"ORDER BY {order}" + (f", has {' and '.join(e.lower() for e in extras)}" if extras else ", no projection or index")
    return ok, detail


def lab_checks() -> None:
    port = os.environ.get("CLICKHOUSE_TUNING_HTTP_PORT") or "8126"
    url = os.environ.get("TUNING_CH_URL") or f"http://localhost:{port}"
    user = os.environ.get("TUNING_CH_AGENT_USER") or "tuner_agent"
    password = os.environ.get("TUNING_CH_AGENT_PASSWORD") or "tuner_agent123"
    admin = os.environ.get("TUNING_CH_ADMIN_USER") or "tuner_admin"
    admin_password = os.environ.get("TUNING_CH_ADMIN_PASSWORD") or "tuner_admin123"
    print("Tuning lab")
    try:
        count = clickhouse_query(url, user, password, f"SELECT count() FROM {LAB_TABLE}").strip()
    except ApiError as e:
        hint = ("start it: docker compose --profile demo up -d clickhouse-tuning" if e.status is None
                else "the tuner_agent user or its grants are missing: sql/init/03_users.sql did not run "
                     "(recreate the lab volume, see DEMO_SCRIPT.md Reset / re-run)")
        check(False, f"clickhouse-tuning at {url} as {user}: {e}", hint=hint)
        return
    check(count == str(LAB_ROWS), f"{LAB_TABLE} has {count} rows as {user} at {url} (want {LAB_ROWS:,})",
          hint="the seed did not finish: recreate the lab volume (see DEMO_SCRIPT.md Reset / re-run)")
    try:
        ok, detail = lab_shape(clickhouse_query(url, user, password, f"SHOW CREATE TABLE {LAB_TABLE}"))
        check(ok, f"lab table still pessimal: {detail}",
              hint="a rehearsal's approved DDL is still applied: python demos/slow-query-tuner/scripts/reset_demo.py")
    except ApiError as e:
        check(False, f"SHOW CREATE TABLE {LAB_TABLE}: {e}")
    try:
        one = clickhouse_query(url, admin, admin_password, "SELECT 1").strip()
        check(one == "1", f"Act 5: {admin} authenticates (the approved-DDL connection)", act=5)
    except ApiError as e:
        check(False, f"Act 5: {admin} at {url}: {e}", act=5,
              hint="TUNING_CH_ADMIN_* must match the clickhouse-tuning service's CLICKHOUSE_USER/PASSWORD")


def checkpoint_checks() -> None:
    print("Pause/resume")
    usable = CHECKPOINTS.is_dir() and os.access(CHECKPOINTS, os.W_OK | os.X_OK)
    check(usable, f"Act 3: {CHECKPOINTS.relative_to(REPO_ROOT)} exists and is writable (run state for --resume)",
          act=3, hint="restore it from git: git checkout -- demos/slow-query-tuner/checkpoints/.gitignore")


# --- Langfuse -----------------------------------------------------------------------------

def _window(days: float, now: datetime) -> Dict[str, str]:
    return {"fromStartTime": iso(now - timedelta(days=days)), "toStartTime": iso(now + timedelta(minutes=5))}


def latest_root(lf: Langfuse, now: datetime, major: int) -> Tuple[Optional[dict], str]:
    """The newest root `tune-clickhouse-query` observation in the lookback window, and the API
    that found it. Observations v2 first; the v1 read only when v2 answers 404 on a v3 server
    (the pinned self-hosted stack), where v2 needs the v4 write mode."""
    window = _window(LOOKBACK_DAYS, now)
    try:
        rows = lf.cursor("/api/public/v2/observations", dict(window, name=TRACE_NAME, isRootObservation=True,
                                                             fields="core,basic"))
        api = "Observations v2"
    except ApiError as e:
        if e.status != 404 or major >= 4:
            raise
        rows = lf.pages("/api/public/observations", dict(window, name=TRACE_NAME, type="AGENT"))
        rows = [r for r in rows if not r.get("parentObservationId")]
        api = "Observations v1 (v2 is 404 on this v3 server)"
    rows = [r for r in rows if r.get("traceId") and r.get("startTime") and r.get("name") == TRACE_NAME]
    rows.sort(key=lambda r: parse_time(r["startTime"]), reverse=True)
    return (rows[0] if rows else None), api


def observation_count(lf: Langfuse, api: str, trace_id: str, started: datetime, now: datetime) -> int:
    window = {"fromStartTime": iso(started - timedelta(hours=1)), "toStartTime": iso(now + timedelta(minutes=5))}
    if api.startswith("Observations v2"):
        return len(lf.cursor("/api/public/v2/observations", dict(window, traceId=trace_id, fields="core")))
    return len(lf.pages("/api/public/observations", dict(window, traceId=trace_id)))


def prompt_checks(lf: Langfuse) -> None:
    """Every label each seeded definition owns serves one version with its type, text and config."""
    import seed_prompts  # noqa: E402 — stdlib only; the definitions seed_all.py writes
    for name, text, labels, _ in seed_prompts.DEFINITIONS:
        served: Dict[str, dict] = {}
        problems: List[str] = []
        for label in labels:
            try:
                served[label] = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label) or {}
            except ApiError as e:
                problems.append(f"{label}: {'missing' if e.status == 404 else e}")
        for label, p in served.items():
            if p.get("type") != "text" or (p.get("prompt") or "").strip() != text.strip():
                problems.append(f"{label} v{p.get('version')} text differs from prompts.py")
            elif (p.get("config") or {}) != {}:
                problems.append(f"{label} v{p.get('version')} config differs ({p.get('config')})")
        versions = sorted({p.get("version") for p in served.values()})
        if len(served) == len(labels) and len(versions) > 1:
            problems.append(f"labels split across versions {versions}")
        shown = f"v{versions[0]}" if len(versions) == 1 else "?"
        check(not problems, f"prompt {name} [{', '.join(labels)}] = {shown}" + (f": {'; '.join(problems)}" if problems else ""),
              hint=f"run {SEED_CMD} to reconcile the definition and labels")


def dataset_checks(lf: Langfuse, state: Dict[str, Any], now: datetime, major: int) -> None:
    import seed_dataset  # noqa: E402 — stdlib only; the items seed_all.py writes
    name = seed_dataset.DATASET_NAME
    quoted = urllib.parse.quote(name, safe="")
    try:
        state["dataset_id"] = (lf.get(f"/api/public/v2/datasets/{quoted}") or {}).get("id")
        items = lf.pages("/api/public/dataset-items", {"datasetName": name})
    except ApiError as e:
        check(False, f"dataset {name}: {'missing' if e.status == 404 else e}", hint=f"run {SEED_CMD}", act=6)
        return
    stored = {i.get("id"): i for i in items}
    bad = []
    for want in seed_dataset.desired_items():
        got = stored.get(want["id"])
        if got is None:
            bad.append(f"{want['id']} missing")
        elif got.get("status") != "ACTIVE":
            bad.append(f"{want['id']} {got.get('status')}")
        elif (got.get("input"), got.get("expectedOutput"), got.get("metadata")) != (
                want["input"], want["expected_output"], want["metadata"]):
            bad.append(f"{want['id']} differs from seed_dataset.py")
    active = sum(1 for i in items if i.get("status") == "ACTIVE")
    check(not bad, f"Act 6: dataset {name}: {active} ACTIVE item(s)" + (f"; {', '.join(bad)}" if bad else
          ", qt-q1..q3 as defined"), hint=f"run {SEED_CMD}", act=6)
    experiment_checks(lf, state, name, now, major)


def experiment_checks(lf: Langfuse, state: Dict[str, Any], name: str, now: datetime, major: int) -> None:
    """Act 6 (bonus) needs a pre-run experiment. Read through the experiments API; the dataset
    runs read only when that 404s on a v3 server, which has no experiments API."""
    try:
        try:
            rows = lf.cursor("/api/public/experiments", dict(_window(EXPERIMENT_LOOKBACK_DAYS, now),
                                                             datasetId=state.get("dataset_id")))
            api = "experiments API"
        except ApiError as e:
            if e.status != 404 or major >= 4:
                raise
            rows = lf.pages(f"/api/public/datasets/{urllib.parse.quote(name, safe='')}/runs", {})
            api = "dataset runs (no experiments API on this v3 server)"
    except ApiError as e:
        check(False, f"Act 6: experiment read failed: {e}", act=6)
        return
    runs = [r for r in rows if (r.get("name") or "").startswith(EXPERIMENT_RUN_PREFIX)]
    check(bool(runs), f"Act 6 (bonus): {len(runs)} '{EXPERIMENT_RUN_PREFIX}…' experiment(s) on {name} ({api})",
          required=False, act=6,
          hint="pre-run: docker compose --profile demo run --rm slow-query-tuner python scripts/run_experiment.py --sample 1 (real spend)")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        version = (lf.get("/api/public/health") or {}).get("version") or ""
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"Langfuse at {lf.base}: {e}",
              hint="401: wrong keys or LANGFUSE_BASE_URL; network error: docker compose --profile langfuse up -d")
        return state
    major = server_major(version)
    if not check(major > 0, f"Langfuse server version {version or 'unknown'}"):
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="put this project's keys in the root .env, or fix langfuse.project in demo.yaml"):
        return state

    try:
        now = datetime.now(timezone.utc)
        print("Seeded artifacts")
        prompt_checks(lf)
        dataset_checks(lf, state, now, major)

        print("Latest run")
        latest, api = latest_root(lf, now, major)
        if not check(latest is not None, f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days ({api})",
                     hint=f"run {RUN_CMD}"):
            return state
        state["trace_id"] = trace_id = latest["traceId"]
        started = parse_time(latest["startTime"])
        n = observation_count(lf, api, trace_id, started, now)
        check(n >= MIN_OBSERVATIONS, f"latest run has {n} observation(s) (want {MIN_OBSERVATIONS}+)",
              hint="observations may still be landing: wait 30 s and re-run")
        names = {s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})}
        missing = [x for x in EXPECTED_SCORES if x not in names]
        check(not missing, f"latest run's trace scores: {', '.join(x for x in EXPECTED_SCORES if x in names) or 'none'}"
              + (f"; missing {', '.join(missing)}" if missing else ""),
              hint="scores land after the trace: wait and re-run; a run that crashed mid-loop writes none")
        missing = [x for x in EVALUATOR_SCORES if x not in names]
        check(not missing, f"Act 4: runaway-loop-guard scores on the latest run"
              + (f"; missing {', '.join(missing)}" if missing else f" ({', '.join(EVALUATOR_SCORES)})"), act=4,
              hint="run ./scripts/seed-code-evaluators.sh; scores land a few seconds after the trace")
        for score in OPTIONAL_SCORES:
            check(score in names, f"Act 6 (bonus): {score} judge score on the latest run", required=False, act=6,
                  hint="run ./scripts/seed-query-tuner-evaluators.sh; it scores new traces only")
        age_h = (now - started).total_seconds() / 3600
        check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
              required=False, hint=f"run {RUN_CMD} the day before")

        if major < 4:
            fallback = "run_cost_usd" in names and "cap_terminated" in names
            check(fallback, f"Act 4: Langfuse {version} has no Monitors (v4+); the act runs on its trace-cost "
                  "Fallback, which needs run_cost_usd and cap_terminated on a run", act=4)
        else:
            check(False, f"Act 4: no documented API reads Monitors; confirm '{MONITORS[0]}' and '{MONITORS[1]}' "
                  "exist in the UI", required=False, act=4,
                  hint="python demos/slow-query-tuner/scripts/seed_all.py prints their fields")
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}")
    return state


def config_checks(manifest: str) -> Tuple[bool, Optional[str]]:
    """Load the root .env (shell LANGFUSE_* dropped first) and check every required value.
    Returns (complete, expected project). Nothing touches the network before this passes."""
    path = env_file(manifest_value(manifest, "", "profile"))
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    ok = check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
               hint="run ./setup.sh at the repo root")
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        ok &= check(bool(os.environ.get(key)), f"{key} set in {shown}", hint="run ./setup.sh at the repo root")
    ok &= check(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set (the agent's planner)",
                hint="add it to the root .env")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    ok &= check(bool(expected), f"expected project '{expected}' (langfuse.project in demo.yaml)")
    return bool(ok), expected


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    args = ap.parse_args(argv)
    del RESULTS[:]
    del ACTS[:]

    manifest_path = DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    print("slow-query-tuner readiness")
    print("Config")
    complete, expected = config_checks(manifest)
    if not complete:
        print("  (lab and Langfuse checks skipped until the config is complete)")
    else:
        lab_checks()
        checkpoint_checks()
        lf = Langfuse(os.environ["LANGFUSE_BASE_URL"], os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
        state = langfuse_checks(lf, expected)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<28} {url}")

    line, code = summary()
    print(f"\n{short_path_summary()}")
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
