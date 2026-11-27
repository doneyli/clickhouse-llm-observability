#!/usr/bin/env python3
"""Readiness check for slow-query-tuner: reads Langfuse and the tuning lab back, says READY or NOT READY.

    python demos/slow-query-tuner/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check
    python demos/slow-query-tuner/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root on the host. It reads the root `.env` (shell-exported LANGFUSE_* are
ignored), Langfuse at LANGFUSE_BASE_URL, and the `clickhouse-tuning` lab on its host port as the
read-only `tuner_agent` user. Every check counts real rows: an HTTP 200 proves little on its own,
because a trace can exist before its observations land.

Standard library only, so it runs in any Python 3.9+ without the demo's dependencies. No model
calls and no writes: Langfuse GETs and two read-only ClickHouse queries, done in seconds.

The latest run is read through Observations API v2. The root stack pins a Langfuse v3 server,
which answers that endpoint with 404; only then does the check fall back to the v1 observations
read, which CLAUDE.md names as the self-hosted path. It never reads the legacy /traces endpoints.
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
# (prompt, label) -> other labels that version must also carry (scripts/seed_prompts.py)
EXPECTED_PROMPTS: Dict[Tuple[str, str], List[str]] = {
    ("query-tuner-system", "production"): ["v2-disciplined"],
    ("query-tuner-system", "v1-naive"): [],      # Act 4's --runaway and Act 6's losing arm
    ("query-tuner-goal", "production"): [],
}
DATASET_NAME = "query-tuner/goals"               # scripts/seed_dataset.py
DATASET_ITEMS = ["qt-q1", "qt-q2", "qt-q3"]
EXPERIMENT_RUN_PREFIX = "query-tuner prompt "    # scripts/run_experiment.py run names
# The five trace scores agent_loop.finalize() writes on every run that reaches the end.
EXPECTED_SCORES = ["turns_used", "run_cost_usd", "task_completed", "verified_speedup", "trajectory_efficiency"]
# Written by evaluators Langfuse runs after the trace lands; they only warn when missing.
OPTIONAL_SCORES = {
    "goal_drift": "./scripts/seed-query-tuner-evaluators.sh (managed judge, scores new traces only)",
    "cap_terminated": "./scripts/seed-code-evaluators.sh (runaway-loop-guard)",
}
MONITORS = ["query-tuner runaway cost", "query-tuner turn count"]  # scripts/seed_monitors.py
MIN_OBSERVATIONS = 2                            # the agent root plus at least one plan-next-action
LOOKBACK_DAYS = 7                               # how far back to look for a run
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
LAB_ROWS = 30_000_000                           # sql/init/02_seed.sql inserts exactly this many
LAB_TABLE = "tuning_lab.web_events"
SEED_CMD = "python demos/slow-query-tuner/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm slow-query-tuner python main.py --query q1"
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
    print("Tuning lab")
    try:
        count = clickhouse_query(url, user, password, f"SELECT count() FROM {LAB_TABLE}").strip()
    except ApiError as e:
        hint = ("start it: docker compose --profile demo up -d clickhouse-tuning" if e.status is None
                else "the tuner_agent user or its grants are missing: sql/init/03_users.sql did not run "
                     "(docker compose down -v the lab and bring it up again)")
        check(False, f"clickhouse-tuning at {url} as {user}: {e}", hint=hint)
        return
    check(count == str(LAB_ROWS), f"{LAB_TABLE} has {count} rows as {user} at {url} (want {LAB_ROWS:,})",
          hint="the seed did not finish: recreate the lab volume (see Reset / re-run)")
    try:
        ok, detail = lab_shape(clickhouse_query(url, user, password, f"SHOW CREATE TABLE {LAB_TABLE}"))
    except ApiError as e:
        check(False, f"SHOW CREATE TABLE {LAB_TABLE}: {e}")
        return
    check(ok, f"lab table still pessimal: {detail}",
          hint="a rehearsal's approved DDL is still applied: python demos/slow-query-tuner/scripts/reset_demo.py")


# --- Langfuse -----------------------------------------------------------------------------

def latest_root(lf: Langfuse, now: datetime) -> Tuple[Optional[dict], str]:
    """The newest root `tune-clickhouse-query` observation in the lookback window, and the API
    that found it. Observations v2 first; v1 only when v2 answers 404 (a Langfuse v3 server)."""
    window = {"fromStartTime": iso(now - timedelta(days=LOOKBACK_DAYS)), "toStartTime": iso(now + timedelta(minutes=5))}
    try:
        rows = (lf.get("/api/public/v2/observations", name=TRACE_NAME, isRootObservation=True,
                       fields="core,basic", limit=50, **window) or {}).get("data") or []
        api = "Observations v2"
    except ApiError as e:
        if e.status != 404:
            raise
        rows = (lf.get("/api/public/observations", name=TRACE_NAME, type="AGENT", limit=100, **window)
                or {}).get("data") or []
        rows = [r for r in rows if not r.get("parentObservationId")]
        api = "Observations v1 (v2 is 404 on this v3 server)"
    rows = [r for r in rows if r.get("traceId") and r.get("startTime")]
    rows.sort(key=lambda r: parse_time(r["startTime"]), reverse=True)
    return (rows[0] if rows else None), api


def observation_count(lf: Langfuse, api: str, trace_id: str, started: datetime, now: datetime) -> int:
    window = {"fromStartTime": iso(started - timedelta(hours=1)), "toStartTime": iso(now + timedelta(minutes=5))}
    if api.startswith("Observations v2"):
        return len(lf.cursor("/api/public/v2/observations", dict(window, traceId=trace_id, fields="core")))
    d = lf.get("/api/public/observations", traceId=trace_id, limit=100, **window) or {}
    return int((d.get("meta") or {}).get("totalItems") or len(d.get("data") or []))


def prompt_checks(lf: Langfuse) -> None:
    for (name, label), also in EXPECTED_PROMPTS.items():
        try:
            p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label) or {}
        except ApiError as e:
            check(False, f"prompt {name} with label {label}: {e}", hint=f"run {SEED_CMD}")
            continue
        labels = p.get("labels") or []
        missing = [x for x in also if x not in labels]
        check(not missing, f"prompt {name}: {label} = v{p.get('version')}"
              + (f" (also {', '.join(also)})" if also and not missing else "")
              + (f"; that version lacks {', '.join(missing)}" if missing else ""),
              hint=f"run {SEED_CMD} to put the labels back")


def dataset_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    quoted = urllib.parse.quote(DATASET_NAME, safe="")
    try:
        state["dataset_id"] = (lf.get(f"/api/public/v2/datasets/{quoted}") or {}).get("id")
        items = (lf.get("/api/public/dataset-items", datasetName=DATASET_NAME, limit=100) or {}).get("data") or []
    except ApiError as e:
        check(False, f"dataset {DATASET_NAME}: {e}", hint=f"run {SEED_CMD}")
        return
    active = {i.get("id") for i in items if (i.get("status") or "ACTIVE") == "ACTIVE"}
    missing = [i for i in DATASET_ITEMS if i not in active]
    check(not missing, f"dataset {DATASET_NAME}: {len(active)} active item(s)"
          + (f"; missing {', '.join(missing)}" if missing else f" incl. {', '.join(DATASET_ITEMS)}"),
          hint=f"run {SEED_CMD}")
    try:
        runs = (lf.get(f"/api/public/datasets/{quoted}/runs", limit=100) or {}).get("data") or []
        names = [r.get("name") or "" for r in runs if (r.get("name") or "").startswith(EXPERIMENT_RUN_PREFIX)]
        check(bool(names), f"Act 6 (bonus): {len(names)} experiment run(s) on {DATASET_NAME}", required=False,
              hint="pre-run: docker compose --profile demo run --rm slow-query-tuner python scripts/run_experiment.py --sample 1 (real spend)")
    except ApiError as e:
        check(False, f"Act 6 (bonus): experiment runs on {DATASET_NAME} not readable: {e}", required=False)


def monitor_checks(lf: Langfuse) -> None:
    try:
        rows = (lf.get("/api/public/monitors") or {}).get("data") or []
    except ApiError as e:
        check(False, f"Act 4: Monitors not readable here ({e.status or 'no response'}); a v3 server has no Monitors API",
              required=False, hint="create the two Monitors by hand from the seed output, or use Act 4's Fallback")
        return
    have = {m.get("name") for m in rows}
    missing = [m for m in MONITORS if m not in have]
    check(not missing, f"Act 4: Monitors {', '.join(MONITORS)}" + (f"; missing {', '.join(missing)}" if missing else ""),
          required=False, hint=f"run {SEED_CMD}, or create them from its printed fields")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or LANGFUSE_BASE_URL; network error: docker compose --profile langfuse up -d")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="put this project's keys in the root .env, or fix langfuse.project in demo.yaml"):
        return state

    try:
        print("Seeded artifacts")
        prompt_checks(lf)
        dataset_checks(lf, state)
        monitor_checks(lf)

        print("Latest run")
        now = datetime.now(timezone.utc)
        latest, api = latest_root(lf, now)
        if not check(latest is not None, f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days ({api})",
                     hint=f"run {RUN_CMD}"):
            return state
        state["trace_id"] = trace_id = latest["traceId"]
        started = parse_time(latest["startTime"])
        n = observation_count(lf, api, trace_id, started, now)
        check(n >= MIN_OBSERVATIONS, f"latest run has {n} observation(s) (want {MIN_OBSERVATIONS}+)",
              hint="observations may still be landing: wait 30 s and re-run")
        names = sorted({s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})})
        missing = [x for x in EXPECTED_SCORES if x not in names]
        check(not missing, f"latest run's scores: {', '.join(names) or 'none'}"
              + (f"; missing {', '.join(missing)}" if missing else ""),
              hint="scores land after the trace: wait and re-run; a run that crashed mid-loop writes none")
        for score, source in OPTIONAL_SCORES.items():
            check(score in names, f"evaluator score {score} on the latest run", required=False,
                  hint=f"seeded by {source}; it can take a minute to land")
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
    print("slow-query-tuner readiness")
    print("Config")
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="run ./setup.sh at the repo root")
    check(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set (the agent's planner)",
          hint="add it to the root .env")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    lab_checks()
    if check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set"):
        lf = Langfuse(base, pk, sk)
        state = langfuse_checks(lf, expected)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<28} {url}")

    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
