#!/usr/bin/env python3
"""Readiness check for query-router: reads Langfuse back and says READY or NOT READY.

    python demos/query-router/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check
    python demos/query-router/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root. It reads the root `.env` (a container demo) and ignores
shell-exported LANGFUSE_* keys. An HTTP 200 proves little on its own (a trace can exist
before its observations land), so every check counts real rows. Standard library only, so
it runs in any Python 3.9+ without the demo's dependencies. No model calls; about fifteen
Langfuse reads and five local health checks, done in seconds.

Read APIs: Observations v2 and Scores v3. The self-hosted stack pins Langfuse server 3.x,
where `GET /api/public/v2/observations` answers 404, so observation reads fall back to the
v1 observations list there and the output says so. No legacy `/traces` endpoint is used.
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

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with server.py, router.py,
# handlers.py and seed_all.py.
ROOT_SPAN = "route-and-dispatch"          # server.run() opens this span on every live run
ROUTER_OBS = "route-query"                # router.classify()'s generation
ROUTES = ("analytics_sql", "docs_simple", "docs_complex")   # router.ROUTES
PROMPT_NAME = "query-router-classifier"
EXPECTED_PROMPT_LABELS = ("production", "baseline")          # seed-router-prompt.py sets both
DATASET_NAME = "query-router-accuracy"
MIN_DATASET_ITEMS = 30                    # seed-router-dataset.py ITEMS; pinned misroutes add more
EXPECTED_SCORES = ["router_confidence"]   # the runtime score every live run writes
MIN_OBSERVATIONS = 3                      # route-and-dispatch + route-query + dispatch-* or fallback-handler
MIN_NESTED_OBSERVATIONS = 5               # a dispatched run also carries the handler's own subtree
HISTORY_DAYS = 14                         # seed-router-history.py --days default
HISTORY_MIN_PER_WEEK = 100                # it seeds ~150 route-query rows per week; the dashboard compares the two
LOOKBACK_DAYS = 7                         # how far back to look for a live run
RUNS_SCANNED = 10                         # newest live runs scanned for Act 1 (main.py runs 10 questions)
FRESH_HOURS = 24                          # older than this only warns: re-run the day before
SEED_CMD = "python demos/query-router/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm query-router python main.py"
UP_CMD = "docker compose --profile langfuse --profile demo up -d"
EXPERIMENT_CMD = "python scripts/run-router-experiment.py --sample 30"
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


def services(environ: Optional[MutableMapping[str, str]] = None) -> List[Tuple[str, str]]:
    """The local services the acts need, on the host ports docker-compose.yaml maps."""
    env = os.environ if environ is None else environ
    port = lambda var, default: env.get(var) or default  # noqa: E731
    return [
        ("query-router", f"http://localhost:{port('QUERY_ROUTER_PORT', '8008')}/health"),
        ("text-to-sql (analytics_sql)", f"http://localhost:{port('TEXT_TO_SQL_PORT', '8002')}/health"),
        ("vector-rag (docs_simple)", f"http://localhost:{port('VECTOR_RAG_PORT', '8003')}/health"),
        ("agentic-rag (docs_complex)", f"http://localhost:{port('AGENTIC_RAG_PORT', '8006')}/health"),
    ]


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
    """The few public-API calls the demo scripts need: projects, prompts, datasets,
    Observations v2 (v1 list only where the server has no v2) and Scores v3."""

    def __init__(self, base_url: str, public_key: str, secret_key: str, timeout: float = TIMEOUT_S) -> None:
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "demo-verify/1"}
        self.timeout = timeout
        self.observations_api: Optional[str] = None   # "v2" or "v1", decided on first read

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
            d = self.get(path, **dict(params, limit=min(100, cap), **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                break
        return out[:cap]

    def pages(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = 20) -> List[dict]:
        """Follow page-numbered lists (dataset items, the v1 observations list)."""
        out: List[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params, page=page, limit=min(100, cap))) or {}
            rows = d.get("data") or []
            out += rows
            total = (d.get("meta") or {}).get("totalPages") or 0
            if not rows or page >= total or len(out) >= cap:
                break
        return out[:cap]

    def observations(self, cap: int = 100, **filters: Any) -> List[dict]:
        """Observations, newest first, filtered by name / traceId / time window.

        Observations v2 first. Langfuse server 3.x (the self-hosted stack) answers 404 on
        v2, so on that 404 this switches to the v1 list for the rest of the run. Either way
        each row has id, traceId, parentObservationId, name and startTime.
        """
        if self.observations_api != "v1":
            try:
                rows = self.cursor("/api/public/v2/observations",
                                   dict(filters, fields="core,basic"), cap=cap)
                self.observations_api = "v2"
                return rows
            except ApiError as e:
                if e.status != 404 or self.observations_api == "v2":
                    raise
                self.observations_api = "v1"
        return self.pages("/api/public/observations", filters, cap=cap)


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per act of the demo script, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = []
    if state.get("dispatched_trace_id"):
        links.append(("Act 1 · a dispatched run", f"{p}/traces/{state['dispatched_trace_id']}"))
    elif state.get("trace_id"):
        links.append(("Act 1 · the latest run", f"{p}/traces/{state['trace_id']}"))
    links.append(("Act 2 · traces (find the fallback)", f"{p}/traces"))
    if state.get("dataset_id"):
        links.append(("Act 3 · router-accuracy dataset", f"{p}/datasets/{state['dataset_id']}"))
    links.append(("Act 4 · dashboards (Router Ops)", f"{p}/dashboards"))
    links.append(("Act 5 · classifier prompt", f"{p}/prompts/{urllib.parse.quote(PROMPT_NAME, safe='')}"))
    return links


# --- Checks -------------------------------------------------------------------------------

def _health(url: str) -> Tuple[Optional[int], Any]:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError) as e:
        return None, type(e).__name__
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def service_checks() -> None:
    print("Services")
    for name, url in services():
        status, body = _health(url)
        if status is None:
            check(False, f"{name} not reachable at {url}: {body}", hint=f"run {UP_CMD}")
            continue
        check(status == 200, f"{name} up at {url} ({status})", hint=f"run {UP_CMD}")
        if name == "query-router" and isinstance(body, dict):
            # The router's own view of its handlers, from inside the compose network.
            handlers = body.get("handlers") or {}
            down = sorted(f"{r}={s}" for r, s in handlers.items() if s != "ok")
            check(bool(handlers) and not down,
                  "router reaches all three handlers" + (f"; not ok: {', '.join(down)}" if down else ""),
                  hint="a down handler degrades its route to the fallback (that is Act 2's planned "
                       "fault, not the default)")
            check(bool(body.get("langfuse")), "router has Langfuse keys (tracing on)",
                  hint="the router no-ops tracing without LANGFUSE_PUBLIC_KEY/SECRET_KEY in .env")


def seeded_checks(lf: Langfuse, state: Dict[str, Any], now: datetime) -> None:
    print("Seeded artifacts")
    quoted = urllib.parse.quote(PROMPT_NAME, safe="")
    for label in EXPECTED_PROMPT_LABELS:
        try:
            p = lf.get(f"/api/public/v2/prompts/{quoted}", label=label) or {}
            check(True, f"prompt {PROMPT_NAME}: {label} = v{p.get('version')}")
        except ApiError as e:
            check(False, f"prompt {PROMPT_NAME} with label {label}: {e}",
                  hint=f"run {SEED_CMD}; without it the router silently uses its local template")

    try:
        ds = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(DATASET_NAME, safe='')}") or {}
        state["dataset_id"] = ds.get("id")
        items = lf.pages("/api/public/dataset-items", {"datasetName": DATASET_NAME})
        active = [i for i in items if (i.get("status") or "ACTIVE") == "ACTIVE"]
        check(len(active) >= MIN_DATASET_ITEMS,
              f"dataset {DATASET_NAME}: {len(active)} active item(s) (want {MIN_DATASET_ITEMS}+)",
              hint=f"run {SEED_CMD}")
    except ApiError as e:
        check(False, f"dataset {DATASET_NAME}: {e}", hint=f"run {SEED_CMD}")

    # Act 4: the Router Ops dashboard compares week 1 with week 2 of seeded history.
    mid, start = now - timedelta(days=HISTORY_DAYS / 2), now - timedelta(days=HISTORY_DAYS)
    older = lf.observations(cap=HISTORY_MIN_PER_WEEK, name=ROUTER_OBS,
                            fromStartTime=iso(start), toStartTime=iso(mid))
    recent = lf.observations(cap=HISTORY_MIN_PER_WEEK, name=ROUTER_OBS,
                             fromStartTime=iso(mid), toStartTime=iso(now + timedelta(minutes=5)))
    if lf.observations_api == "v1":
        print("  note  this server has no Observations v2 (Langfuse 3.x); reading the v1 list")
    shown = lambda rows: f"{len(rows)}{'+' if len(rows) >= HISTORY_MIN_PER_WEEK else ''}"  # noqa: E731
    check(len(older) >= HISTORY_MIN_PER_WEEK and len(recent) >= HISTORY_MIN_PER_WEEK,
          f"router history: {shown(older)} '{ROUTER_OBS}' rows in days 8-14, {shown(recent)} in days 1-7 "
          f"(want {HISTORY_MIN_PER_WEEK}+ each, for the Act 4 drift)",
          hint=f"history is backdated from the day it was seeded and ages out; run {SEED_CMD}")


def live_run_checks(lf: Langfuse, state: Dict[str, Any], now: datetime) -> None:
    """Live runs are found by their `route-and-dispatch` span, never by trace name.

    A dispatched trace usually ends up named after the handler (`text-to-sql-handler`, ...),
    and seed-router-history.py writes synthetic `route-query` and `dispatch-*` rows with no
    such span, so the span is the one marker every live run has and history never has.
    """
    print("Latest run")
    end = iso(now + timedelta(minutes=5))
    roots = lf.observations(cap=RUNS_SCANNED, name=ROOT_SPAN,
                            fromStartTime=iso(now - timedelta(days=LOOKBACK_DAYS)), toStartTime=end)
    if not check(bool(roots), f"a live '{ROOT_SPAN}' run in the last {LOOKBACK_DAYS} days", hint=f"run {RUN_CMD}"):
        return
    roots = sorted(roots, key=lambda r: r.get("startTime") or "", reverse=True)
    trace_obs: Dict[str, List[dict]] = {}

    def observations_of(root: dict) -> List[dict]:
        if root["traceId"] not in trace_obs:
            start = iso(parse_time(root["startTime"]) - timedelta(hours=1))
            trace_obs[root["traceId"]] = lf.observations(cap=500, traceId=root["traceId"],
                                                         fromStartTime=start, toStartTime=end)
        return trace_obs[root["traceId"]]

    latest = roots[0]
    state["trace_id"] = trace_id = latest["traceId"]
    obs = observations_of(latest)
    names = {o.get("name") or "" for o in obs}
    branch = any(n.startswith("dispatch-") or n == "fallback-handler" for n in names)
    check(len(obs) >= MIN_OBSERVATIONS and ROUTER_OBS in names and branch,
          f"latest run has {len(obs)} observation(s) incl. {ROUTER_OBS} and a dispatch or fallback "
          f"(want {MIN_OBSERVATIONS}+)", hint="observations may still be landing: wait 30 s and re-run")
    scores = sorted({s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})})
    missing = [n for n in EXPECTED_SCORES if n not in scores]
    check(not missing, f"latest run's scores: {', '.join(scores) or 'none'}"
          + (f"; missing {', '.join(missing)}" if missing else ""), hint="scores land after the trace: wait and re-run")
    age_h = (now - parse_time(latest["startTime"])).total_seconds() / 3600
    check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
          required=False, hint=f"run {RUN_CMD} the day before")

    # Act 1: one question, one specialist, the handler's subtree nested in the same trace.
    print("Act 1 · dispatched runs")
    seen: Dict[str, int] = {}          # route -> observation count of its newest live trace
    for root in roots:
        rows = observations_of(root)
        for n in {o.get("name") or "" for o in rows}:
            route = n[len("dispatch-"):] if n.startswith("dispatch-") else None
            if route in ROUTES and route not in seen:
                seen[route] = len(rows)
                if len(rows) >= MIN_NESTED_OBSERVATIONS and not state.get("dispatched_trace_id"):
                    state["dispatched_trace_id"] = root["traceId"]
        if len(seen) == len(ROUTES):
            break
    for route in ROUTES:
        check(route in seen, f"a recent live run dispatched to {route}", required=False,
              hint=f"run {RUN_CMD}; if that question fell back, is its handler up?")
    best = max(seen.values()) if seen else 0
    check(best >= MIN_NESTED_OBSERVATIONS,
          f"a dispatched run carries the handler subtree: {best} observation(s) in one trace "
          f"(want {MIN_NESTED_OBSERVATIONS}+)",
          hint="no dispatch, or only the router's spans landed: bring the handlers up and re-run "
               + RUN_CMD if not seen else "the handler joins via trace_context; is it on the same Langfuse project?")


def loop_checks(lf: Langfuse, now: datetime) -> None:
    """Acts 3-5 lean on scores that appear only after live traffic or an experiment."""
    print("Acts 3-5 · scores in the last 14 days")
    since = {"fromTimestamp": iso(now - timedelta(days=HISTORY_DAYS)), "toTimestamp": iso(now + timedelta(minutes=5))}
    for name, why, hint in [
        ("routing_correct", "Act 3/4 misroute rate", f"seeded by history; run {SEED_CMD}"),
        ("route-plausibility", "the managed route judge", f"it samples 25% of NEW route-query rows: run {RUN_CMD}"),
        ("route-match", "Act 5 experiment", f"run {EXPERIMENT_CMD} (cents on Haiku)"),
    ]:
        rows = lf.cursor("/api/public/v3/scores", dict(since, name=name), cap=1, max_pages=1)
        check(bool(rows), f"'{name}' scores exist ({why})", required=False, hint=hint)


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or wrong LANGFUSE_BASE_URL; network error: is Langfuse up?")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in the root .env, or fix langfuse.project in demo.yaml"):
        return state

    now = datetime.now(timezone.utc)
    try:
        seeded_checks(lf, state, now)
        live_run_checks(lf, state, now)
        loop_checks(lf, now)
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
    print("query-router readiness")
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
