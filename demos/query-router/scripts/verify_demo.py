#!/usr/bin/env python3
"""Readiness check for query-router: reads Langfuse back and says READY or NOT READY.

    python demos/query-router/scripts/verify_demo.py            # the full script (Acts 1-5)
    python demos/query-router/scripts/verify_demo.py --short    # the short path only (Opening, Acts 1-2, Close)
    python demos/query-router/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root. It reads the root `.env` (a container demo) and ignores
shell-exported LANGFUSE_* keys. One PASS / FAIL / WARN line per check, then READY or NOT
READY with counts; exit 0 only when every required check passed. By default every asset the
full script needs is required: the prompt labels, the dataset, two weeks of history, the
Router Ops dashboard and its four widgets, and the Act 5 experiment runs. `--short` makes the
Acts 3-5 assets warnings and says so in the verdict.

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows. Standard library only, so it runs in any Python 3.9+ without
the demo's dependencies. No model calls and no writes; a few dozen Langfuse reads and five
local health checks, done in seconds.

Read APIs: projects, prompts v2, datasets v2, Observations v2, Scores v3, experiments and
experiment items, and the (unstable) dashboards API. The self-hosted stack pins Langfuse
server 3.221.1, where `GET /api/public/v2/observations` and `GET /api/public/experiments`
answer 404 (CLAUDE.md, measured per endpoint). Only on that 404 do reads fall back to the
v1 observations list and to `GET /api/public/datasets/{name}/runs` plus each run's details,
and the output says so. No legacy `/traces` endpoint is used.

Every failure reaches the verdict: unreadable config, a malformed LANGFUSE_BASE_URL, HTTP,
network and parse errors each become a FAIL line, the lines already printed still count, and
the run ends with NOT READY and a nonzero exit (Ctrl-C still interrupts).
"""
from __future__ import annotations

import argparse
import ast
import base64
import ipaddress
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
# handlers.py, seed_all.py and DEMO_SCRIPT.md.
ROOT_SPAN = "route-and-dispatch"          # server.run() opens this span on every live run
ROUTER_OBS = "route-query"                # router.classify()'s generation
ROUTES = ("analytics_sql", "docs_simple", "docs_complex")   # router.ROUTES
ACT1_ROUTES = ("docs_complex", "analytics_sql")             # the two traces Act 1 opens
PROMPT_NAME = "query-router-classifier"
SHORT_PROMPT_LABELS = ("production",)     # what the router serves (Acts 1-2)
FULL_PROMPT_LABELS = ("production", "baseline")              # Act 5 compares baseline with production
DATASET_NAME = "query-router-accuracy"
MIN_DATASET_ITEMS = 30                    # seed-router-dataset.py ITEMS; pinned misroutes add more
VALID_EXPECTED_ROUTES = set(ROUTES) | {"fallback"}
EXPECTED_SCORES = ["router_confidence"]   # the runtime score every live run writes
MIN_OBSERVATIONS = 3                      # route-and-dispatch + route-query + dispatch-* or fallback-handler
MIN_NESTED_OBSERVATIONS = 5               # a dispatched run also carries the handler's own subtree
HISTORY_DAYS = 14                         # seed-router-history.py --days default
HISTORY_MIN_PER_WEEK = 100                # it seeds ~150 route-query rows per week; the dashboard compares the two
LOOKBACK_DAYS = 7                         # how far back to look for a live run
RUNS_SCANNED = 10                         # newest live runs scanned for Act 1 (main.py runs 10 questions)
EXPERIMENT_LOOKBACK_DAYS = 30             # Act 5 experiment runs older than this do not count
FRESH_HOURS = 24                          # older than this only warns: re-run the day before
SEED_CMD = "python demos/query-router/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm query-router python main.py"
UP_CMD = "docker compose --profile langfuse --profile demo up -d"
EXPERIMENT_CMD = "python scripts/run-router-experiment.py --sample 30"
EXPERIMENT_SCRIPT = REPO_ROOT / "scripts" / "run-router-experiment.py"
TIMEOUT_S = 10
MAX_PAGES = 20
SCORE_TRACE_BATCH = 50                    # trace ids per Scores v3 traceId= filter (keeps the URL short)

# The Router Ops dashboard (Act 4). seed_all.py creates exactly these; verify reads them back.
DASHBOARD_NAME = "Router Ops"
DASHBOARD_DESCRIPTION = ("query-router: route drift, fallback rate, router confidence and misroute rate "
                         "(seeded by demos/query-router/scripts/seed_all.py)")
_name_filter = lambda *names: [{"column": "name", "operator": "any of", "value": list(names),  # noqa: E731
                                "type": "stringOptions"}]
WIDGETS: List[Dict[str, Any]] = [
    {"name": "Router Ops · Route distribution over time",
     "description": "Dispatches per route and fallbacks per day: watches route drift",
     "view": "observations", "chartType": "BAR_TIME_SERIES",
     "dimensions": [{"field": "name"}], "metrics": [{"measure": "count", "agg": "count"}],
     "filters": _name_filter(*[f"dispatch-{r}" for r in ROUTES], "fallback-handler"),
     "x": 0, "y": 0, "width": 6, "height": 5},
    {"name": "Router Ops · Fallback rate",
     "description": "fallback-handler runs against route-query decisions per day: threshold too tight or taxonomy gaps",
     "view": "observations", "chartType": "BAR_TIME_SERIES",
     "dimensions": [{"field": "name"}], "metrics": [{"measure": "count", "agg": "count"}],
     "filters": _name_filter("route-query", "fallback-handler"),
     "x": 6, "y": 0, "width": 6, "height": 5},
    {"name": "Router Ops · Avg router_confidence",
     "description": "Daily average of the runtime router_confidence score: degradation before misroutes surface",
     "view": "scores-numeric", "chartType": "LINE_TIME_SERIES",
     "dimensions": [{"field": "name"}], "metrics": [{"measure": "value", "agg": "avg"}],
     "filters": _name_filter("router_confidence"),
     "x": 0, "y": 5, "width": 6, "height": 5},
    {"name": "Router Ops · Misroute rate",
     "description": "routing_correct true vs false per day: silent misclassification",
     "view": "scores-boolean", "chartType": "BAR_TIME_SERIES",
     "dimensions": [{"field": "booleanValue"}], "metrics": [{"measure": "count", "agg": "count"}],
     "filters": _name_filter("routing_correct"),
     "x": 6, "y": 5, "width": 6, "height": 5},
]
WIDGET_FIELDS = ("description", "view", "chartType", "dimensions", "metrics", "filters")

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
    return (f"NOT READY ({detail})", 1) if failed or not rs else (f"READY ({detail})", 0)


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
    """Container demos read the root stack's .env; standalone and workshop demos their own.

    The profile comes from demo.yaml (`profile: container`), a positive declaration, not a
    guess from which files happen to exist.
    """
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


def base_url_problem(base: str) -> Optional[str]:
    """Why `base` is not a usable http(s)://host[:port] URL, or None when it is.

    Checked before any request, so a typo such as `http://[::gg]` is a FAIL line, not a
    ValueError raised while a request is built.
    """
    try:
        parts = urllib.parse.urlsplit(base)
        host, _ = parts.hostname, parts.port      # .port raises on a non-numeric port
    except ValueError as e:
        return str(e)
    if parts.scheme not in ("http", "https") or not host:
        return "want http(s)://host[:port]"
    if ":" in host:                                # a bracketed IPv6 literal
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return f"'{host}' is not an IPv6 address"
    return None


def experiment_run_names(source: Optional[str] = None) -> List[str]:
    """The run names scripts/run-router-experiment.py writes, from its CONFIGS literal.

    Read from the source, not imported: that script needs the Langfuse SDK and router.py.
    It names each run `router-prompt={label}-model={model}`.
    """
    text = EXPERIMENT_SCRIPT.read_text() if source is None else source
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONFIGS" for t in node.targets):
            return [f"router-prompt={label}-model={model}" for label, model in ast.literal_eval(node.value)]
    raise ValueError("no CONFIGS list in run-router-experiment.py")


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
    """An HTTP error (status set) or a network or protocol failure (status None)."""

    def __init__(self, status: Optional[int], message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


class Langfuse:
    """The public-API calls the demo scripts need. Observations v2 and experiments fall back
    to their v1 equivalents only on a 404, which the pinned self-hosted 3.x server returns."""

    def __init__(self, base_url: str, public_key: str, secret_key: str, timeout: float = TIMEOUT_S) -> None:
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "demo-verify/1"}
        self.timeout = timeout
        self.observations_api: Optional[str] = None   # "v2" or "v1", decided on first read
        self.experiments_api: Optional[str] = None    # "experiments" or "dataset-runs"

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                body: Optional[Any] = None) -> Any:
        query = {k: (str(v).lower() if isinstance(v, bool) else v)
                 for k, v in (params or {}).items() if v is not None}
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        try:
            url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or e.reason) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        except ValueError as e:  # a malformed URL (bad host or port) fails while the request is built
            raise ApiError(None, f"invalid request URL for {path}: {e}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body=body)

    def cursor(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = MAX_PAGES) -> List[dict]:
        """Follow `meta.cursor` (Observations v2, Scores v3, experiments).

        A page may be empty while a cursor is still returned, so this stops on a missing
        cursor, not on an empty page. A cursor seen twice, or one still present after
        `max_pages`, raises instead of returning a silently partial list.
        """
        out: List[dict] = []
        cur, seen = None, set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=min(100, cap), **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:
                raise ApiError(None, f"{path} returned the same cursor twice")
            seen.add(cur)
        raise ApiError(None, f"{path} still had more pages after {max_pages}")

    def pages(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = MAX_PAGES) -> List[dict]:
        """Follow page-numbered lists (dataset items, dashboards, the v1 fallbacks)."""
        out: List[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params, page=page, limit=min(100, cap))) or {}
            rows = d.get("data") or []
            out += rows
            total = (d.get("meta") or {}).get("totalPages") or 0
            if not rows or page >= total or len(out) >= cap:
                return out[:cap]
        raise ApiError(None, f"{path} still had more pages after {max_pages}")

    def observations(self, cap: int = 100, **filters: Any) -> List[dict]:
        """Observations, newest first, filtered by name / traceId / time window.

        Observations v2 with the `core,basic` field groups (`name` is in `basic`). Langfuse
        server 3.x answers 404 on v2, so on that 404 this switches to the v1 list for the
        rest of the run. Either way each row has id, traceId, parentObservationId, name and
        startTime.
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

    def experiments(self, dataset_id: str, dataset_name: str, since: datetime) -> List[dict]:
        """A dataset's experiment runs, newest first, each with `name` and run-level `scores`.

        `GET /api/public/experiments` (with `fields=core,scores`). On the self-hosted 3.x
        server it answers 404, and only then this reads `GET /datasets/{name}/runs`, whose
        rows carry no scores; experiment_checks() then looks for the scores in Scores v3.
        """
        if self.experiments_api != "dataset-runs":
            try:
                rows = self.cursor("/api/public/experiments", {
                    "datasetId": dataset_id, "fromStartTime": iso(since), "fields": "core,scores"})
                self.experiments_api = "experiments"
                return rows
            except ApiError as e:
                if e.status != 404 or self.experiments_api == "experiments":
                    raise
                self.experiments_api = "dataset-runs"
        rows = self.pages(f"/api/public/datasets/{urllib.parse.quote(dataset_name, safe='')}/runs", {})
        return [r for r in rows if (r.get("createdAt") or "") >= iso(since)]


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
    if state.get("dashboard_id"):
        links.append(("Act 4 · Router Ops dashboard", f"{p}/dashboards/{state['dashboard_id']}"))
    else:
        links.append(("Act 4 · dashboards", f"{p}/dashboards"))
    if state.get("dataset_id"):
        links.append(("Act 5 · experiment runs", f"{p}/datasets/{state['dataset_id']}/runs"))
    links.append(("Act 5 · classifier prompt", f"{p}/prompts/{urllib.parse.quote(PROMPT_NAME, safe='')}"))
    return links


def widget_drift(spec: Dict[str, Any], stored: Dict[str, Any]) -> List[str]:
    """The definition fields where a stored widget differs from its spec."""
    return [f for f in WIDGET_FIELDS if (stored.get(f) or ([] if f in ("dimensions", "metrics", "filters") else ""))
            != spec[f]]


def run_matches(name: str, run: str) -> bool:
    """An experiment named `run`, or `run` plus a suffix the SDK may add (a space, ` - <time>`)."""
    return name == run or (name.startswith(run) and not name[len(run)].isalnum() and name[len(run)] != "-")


def find_dashboard(lf: Langfuse) -> Optional[dict]:
    rows = lf.pages("/api/public/unstable/dashboards", {})
    return next((d for d in rows if d.get("name") == DASHBOARD_NAME), None)


def placed_widget_ids(dashboard: Dict[str, Any]) -> List[str]:
    """Widget ids placed on a dashboard; `GET /dashboards/{id}` returns them in definition.widgets."""
    entries = ((dashboard.get("definition") or {}).get("widgets")) or []
    return [w.get("widgetId") for w in entries if w.get("widgetId")]


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
    except ValueError as e:  # a malformed URL, e.g. a non-numeric *_PORT
        return None, f"invalid URL ({e})"
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
        if name == "query-router" and status == 200:
            # The router's own view of its handlers, from inside the compose network.
            body = body if isinstance(body, dict) else {}
            handlers = body.get("handlers") or {}
            down = sorted(f"{r}={s}" for r, s in handlers.items() if s != "ok")
            check(set(handlers) >= set(ROUTES) and not down,
                  "router reaches all three handlers" + (f"; not ok: {', '.join(down)}" if down else ""),
                  hint="a down handler degrades its route to the fallback (that is Act 2's planned "
                       "fault, not the default)")
            check(body.get("langfuse") is True, "router has Langfuse keys (tracing on)",
                  hint="the router no-ops tracing without LANGFUSE_PUBLIC_KEY/SECRET_KEY in .env")


def prompt_checks(lf: Langfuse, full: bool) -> None:
    print("Prompt")
    quoted = urllib.parse.quote(PROMPT_NAME, safe="")
    for label in FULL_PROMPT_LABELS:
        required = full or label in SHORT_PROMPT_LABELS
        try:
            p = lf.get(f"/api/public/v2/prompts/{quoted}", label=label) or {}
            ok = (label in (p.get("labels") or [label]) and p.get("type") == "text"
                  and "{{question}}" in (p.get("prompt") or ""))
            check(ok, f"prompt {PROMPT_NAME}: {label} = v{p.get('version')}"
                  + ("" if ok else " (not a text prompt with {{question}})"),
                  required=required, hint=f"run {SEED_CMD}")
        except ApiError as e:
            check(False, f"prompt {PROMPT_NAME} with label {label}: {e}", required=required,
                  hint=f"run {SEED_CMD}; without production the router silently uses its local template")


def usable_items(items: List[dict]) -> List[dict]:
    """ACTIVE items with a question and an expected route the router can produce."""
    out = []
    for i in items:
        inp, exp = i.get("input"), i.get("expectedOutput")
        if ((i.get("status") or "ACTIVE") == "ACTIVE" and isinstance(inp, dict) and (inp.get("question") or "").strip()
                and isinstance(exp, dict) and exp.get("route") in VALID_EXPECTED_ROUTES):
            out.append(i)
    return out


def dataset_checks(lf: Langfuse, state: Dict[str, Any], full: bool) -> None:
    print("Act 3 / Act 5 · dataset")
    try:
        ds = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(DATASET_NAME, safe='')}") or {}
        state["dataset_id"] = ds.get("id")
        items = lf.pages("/api/public/dataset-items", {"datasetName": DATASET_NAME})
    except ApiError as e:
        check(False, f"dataset {DATASET_NAME}: {e}", required=full, hint=f"run {SEED_CMD}")
        return
    usable = usable_items(items)
    check(len(usable) >= MIN_DATASET_ITEMS,
          f"dataset {DATASET_NAME}: {len(usable)} usable ACTIVE item(s) of {len(items)} (want {MIN_DATASET_ITEMS}+)",
          required=full, hint=f"run {SEED_CMD}; archived items or items without a question and route do not count")


def history_checks(lf: Langfuse, now: datetime, full: bool) -> None:
    print("Act 4 · router history")
    ok, detail = history_counts(lf, now)
    if lf.observations_api == "v1":
        print("  note  this server has no Observations v2 (Langfuse 3.x); reading the v1 list")
    check(ok, f"router history: {detail} (for the Act 4 drift)", required=full,
          hint=f"history is backdated from the day it was seeded and ages out; run {SEED_CMD}")


def history_counts(lf: Langfuse, now: datetime) -> Tuple[bool, str]:
    """Both weeks of the history window hold enough route-query rows for the drift story."""
    mid, start = now - timedelta(days=HISTORY_DAYS / 2), now - timedelta(days=HISTORY_DAYS)
    older = lf.observations(cap=HISTORY_MIN_PER_WEEK, name=ROUTER_OBS,
                            fromStartTime=iso(start), toStartTime=iso(mid))
    recent = lf.observations(cap=HISTORY_MIN_PER_WEEK, name=ROUTER_OBS,
                             fromStartTime=iso(mid), toStartTime=iso(now + timedelta(minutes=5)))
    shown = lambda rows: f"{len(rows)}{'+' if len(rows) >= HISTORY_MIN_PER_WEEK else ''}"  # noqa: E731
    ok = len(older) >= HISTORY_MIN_PER_WEEK and len(recent) >= HISTORY_MIN_PER_WEEK
    return ok, (f"{shown(older)} '{ROUTER_OBS}' rows in days 8-14, {shown(recent)} in days 1-7 "
                f"(want {HISTORY_MIN_PER_WEEK}+ each)")


def dashboard_checks(lf: Langfuse, state: Dict[str, Any], full: bool) -> None:
    print("Act 4 · Router Ops dashboard")
    try:
        dash = find_dashboard(lf)
        if not check(dash is not None, f"dashboard '{DASHBOARD_NAME}' exists", required=full,
                     hint=f"run {SEED_CMD}"):
            return
        state["dashboard_id"] = dash["id"]
        placed = set(placed_widget_ids(lf.get(f"/api/public/unstable/dashboards/{dash['id']}") or {}))
        library = {w.get("name"): w for w in lf.pages("/api/public/unstable/dashboard-widgets", {})}
    except ApiError as e:
        hint = (f"run {SEED_CMD}" if e.status != 404 else
                "this server has no dashboards API, so the dashboard cannot be confirmed; build it by hand "
                "(Act 4) and present the short path, or use a server that serves /api/public/unstable/dashboards")
        check(False, f"dashboard '{DASHBOARD_NAME}': {e}", required=full, hint=hint)
        return
    for spec in WIDGETS:
        stored = library.get(spec["name"])
        if stored is None:
            check(False, f"widget '{spec['name']}' exists", required=full, hint=f"run {SEED_CMD}")
            continue
        drift = widget_drift(spec, stored)
        check(not drift and stored.get("id") in placed,
              f"widget '{spec['name']}' placed on the dashboard"
              + (f"; differs in {', '.join(drift)}" if drift else "")
              + ("" if stored.get("id") in placed else "; not placed"),
              required=full, hint=f"run {SEED_CMD}")


def experiment_checks(lf: Langfuse, state: Dict[str, Any], now: datetime, full: bool) -> None:
    print("Act 5 · router experiment")
    try:
        wanted = experiment_run_names()
    except (OSError, ValueError, SyntaxError) as e:
        check(False, f"run names from {EXPERIMENT_SCRIPT.name}: {e}", required=full)
        return
    if not state.get("dataset_id"):
        check(False, "experiment runs: no dataset to read them from", required=full, hint=f"run {SEED_CMD}")
        return
    since = now - timedelta(days=EXPERIMENT_LOOKBACK_DAYS)
    try:
        rows = lf.experiments(state["dataset_id"], DATASET_NAME, since)
        if lf.experiments_api == "dataset-runs":
            print("  note  this server has no experiments API (Langfuse 3.x); reading each run's details and "
                  "the Scores v3 bound to its traces and to the run")
        for run in wanted:
            matches = sorted((r for r in rows if run_matches(r.get("name") or "", run)),
                             key=lambda r: r.get("startTime") or r.get("createdAt") or "", reverse=True)
            if not check(bool(matches), f"experiment run '{run}' in the last {EXPERIMENT_LOOKBACK_DAYS} days",
                         required=full, hint=f"run {EXPERIMENT_CMD} (90 router calls on Haiku and Sonnet)"):
                continue
            latest = matches[0]
            if lf.experiments_api == "experiments":
                run_scores = {s.get("name") for s in latest.get("scores") or []}
                items = lf.cursor("/api/public/experiment-items", {
                    "experimentId": latest["id"], "fromStartTime": iso(since), "fields": "scores"})
                n_items = len(items)
                n_scored = sum(1 for i in items if any(s.get("name") == "route-match" for s in i.get("scores") or []))
                has_run_score = "avg_route_accuracy" in run_scores
            else:
                n_items, n_scored, has_run_score = v3_run_evidence(lf, latest, since, now)
            check(has_run_score and n_scored >= 1,
                  f"run '{run}': {n_items} item(s), {n_scored} with route-match, "
                  f"avg_route_accuracy {'present' if has_run_score else 'missing'}",
                  required=full, hint=f"re-run {EXPERIMENT_CMD}; a run with no scores of its own is not evidence")
    except ApiError as e:
        check(False, f"experiment runs: {e}", required=full)


def v3_run_evidence(lf: Langfuse, run: Dict[str, Any], since: datetime, now: datetime) -> Tuple[int, int, bool]:
    """Item and run scores that belong to ONE dataset run, on a server without the experiments API.

    The run's own items come from its run details (`GET /datasets/{name}/runs/{runName}`),
    whose `datasetRunItems` name the trace of each item. A `route-match` score counts only
    when its Scores v3 subject is one of those traces (or an observation in one), and
    `avg_route_accuracy` only when its subject is this run (`kind: experiment`, the run id).
    A same-named score on any other trace or run proves nothing about this one.
    Returns (items in the run, items with route-match, run has avg_route_accuracy).
    """
    run_id = run.get("id")
    quoted = urllib.parse.quote(DATASET_NAME, safe="")
    detail = lf.get(f"/api/public/datasets/{quoted}/runs/{urllib.parse.quote(run.get('name') or '', safe='')}") or {}
    if not run_id or detail.get("id") != run_id:
        raise ApiError(None, f"run details for '{run.get('name')}' did not return run {run_id}")
    traces = sorted({i["traceId"] for i in detail.get("datasetRunItems") or []
                     if i.get("datasetRunId") == run_id and i.get("traceId")})
    window = {"fromTimestamp": iso(since), "toTimestamp": iso(now + timedelta(minutes=5))}
    scored = set()
    for start in range(0, len(traces), SCORE_TRACE_BATCH):
        batch = set(traces[start:start + SCORE_TRACE_BATCH])
        for s in lf.cursor("/api/public/v3/scores", dict(window, name="route-match", fields="subject",
                                                         traceId=",".join(sorted(batch)))):
            subject = s.get("subject") or {}
            trace = {"trace": subject.get("id"), "observation": subject.get("traceId")}.get(subject.get("kind"))
            if s.get("name") == "route-match" and trace in batch:
                scored.add(trace)
    run_scores = lf.cursor("/api/public/v3/scores", dict(window, name="avg_route_accuracy", fields="subject",
                                                         experimentId=run_id), cap=100)
    has_run_score = any(s.get("name") == "avg_route_accuracy" and (s.get("subject") or {}).get("kind") == "experiment"
                        and (s.get("subject") or {}).get("id") == run_id for s in run_scores)
    return len(traces), len(scored), has_run_score


def live_run_checks(lf: Langfuse, state: Dict[str, Any], now: datetime) -> None:
    """Live runs are found by their `route-and-dispatch` span, never by trace name.

    A dispatched trace usually ends up named after the handler (`text-to-sql-handler`, ...),
    and seed-router-history.py writes synthetic `route-query` and `dispatch-*` rows with no
    such span, so the span is the one marker every live run has and history never has.
    """
    print("Acts 1-2 · live runs")
    end = iso(now + timedelta(minutes=5))
    roots = lf.observations(cap=RUNS_SCANNED, name=ROOT_SPAN,
                            fromStartTime=iso(now - timedelta(days=LOOKBACK_DAYS)), toStartTime=end)
    roots = [r for r in roots if r.get("name", ROOT_SPAN) == ROOT_SPAN and r.get("traceId") and r.get("startTime")]
    if not check(bool(roots), f"a live '{ROOT_SPAN}' run in the last {LOOKBACK_DAYS} days", hint=f"run {RUN_CMD}"):
        return
    roots = sorted(roots, key=lambda r: r["startTime"], reverse=True)
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

    # Act 1 opens a docs_complex run and an analytics_sql run, each with the handler's
    # subtree nested in the router's trace.
    seen: Dict[str, int] = {}          # route -> observation count of its newest live trace
    for root in roots:
        rows = observations_of(root)
        for n in {o.get("name") or "" for o in rows}:
            route = n[len("dispatch-"):] if n.startswith("dispatch-") else None
            if route in ROUTES and route not in seen:
                seen[route] = len(rows)
                if route == ACT1_ROUTES[0] and len(rows) >= MIN_NESTED_OBSERVATIONS:
                    state["dispatched_trace_id"] = root["traceId"]
        if len(seen) == len(ROUTES):
            break
    for route in ROUTES:
        n = seen.get(route)
        check(n is not None and n >= MIN_NESTED_OBSERVATIONS,
              f"a recent live run dispatched to {route} with the handler subtree nested: "
              + (f"{n} observation(s)" if n is not None else "none") + f" (want {MIN_NESTED_OBSERVATIONS}+)",
              required=route in ACT1_ROUTES,
              hint=f"run {RUN_CMD}; if that question fell back, is its handler up?" if n is None
              else "only the router's spans landed: the handler joins via trace_context; same Langfuse project?")


def judge_checks(lf: Langfuse, now: datetime) -> None:
    """Scores no act depends on: they only warn, in either mode."""
    print("Extras")
    since = {"fromTimestamp": iso(now - timedelta(days=HISTORY_DAYS)), "toTimestamp": iso(now + timedelta(minutes=5))}
    rows = lf.cursor("/api/public/v3/scores", dict(since, name="route-plausibility"), cap=1)
    check(bool(rows), "'route-plausibility' judge scores in the last 14 days", required=False,
          hint=f"it samples 25% of NEW route-query rows: run {RUN_CMD}")


def misroute_checks(lf: Langfuse, now: datetime, full: bool) -> None:
    since = {"fromTimestamp": iso(now - timedelta(days=HISTORY_DAYS)), "toTimestamp": iso(now + timedelta(minutes=5))}
    rows = lf.cursor("/api/public/v3/scores", dict(since, name="routing_correct"), cap=1)
    check(bool(rows), "'routing_correct' scores in the last 14 days (the misroute-rate widget)", required=full,
          hint=f"seeded with the history; run {SEED_CMD}")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str], full: bool = True) -> Dict[str, Any]:
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
    if not check(bool(expected_project) and bool(proj) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in the root .env, or fix langfuse.project in demo.yaml"):
        return state

    now = datetime.now(timezone.utc)
    sections = [
        lambda: prompt_checks(lf, full),
        lambda: live_run_checks(lf, state, now),
        lambda: dataset_checks(lf, state, full),
        lambda: history_checks(lf, now, full),
        lambda: misroute_checks(lf, now, full),
        lambda: dashboard_checks(lf, state, full),
        lambda: experiment_checks(lf, state, now, full),
        lambda: judge_checks(lf, now),
    ]
    for i, section in enumerate(sections):
        try:
            section()
        except ApiError as e:  # a failed read is a failed check, never a skipped one
            check(False, f"Langfuse read failed: {e}", required=i < 2 or full)
        except Exception as e:  # noqa: BLE001 - an unexpected response shape fails this section only
            check(False, f"Langfuse response not understood: {type(e).__name__}: {e}", required=i < 2 or full)
    return state


def run_checks(args: argparse.Namespace, full: bool) -> None:
    """Config first, then services and Langfuse. Returns early when the config is unusable."""
    print("Config")
    manifest_path = DEMO_DIR / "demo.yaml"
    try:
        manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    except (OSError, UnicodeDecodeError) as e:
        check(False, f"{manifest_path.name} could not be read: {type(e).__name__}: {e}")
        return
    path = env_file(manifest_value(manifest, "", "profile"))
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    try:
        loaded, problem = load_env(path), ""
    except (OSError, UnicodeDecodeError) as e:   # shell LANGFUSE_* are already dropped
        loaded, problem = False, f" ({type(e).__name__}: {e})"
    check(loaded, f"env loaded from {shown}; shell-exported LANGFUSE_* ignored" if loaded
          else f"env {shown} could not be loaded{problem}" if problem else f"env {shown} not found",
          hint="cp .env.example .env and fill it in" if path.parent == DEMO_DIR else "run ./setup.sh at the repo root")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not check(loaded and bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set"):
        return
    problem = base_url_problem(base)
    if not check(problem is None, f"LANGFUSE_BASE_URL {base!r} is an http(s) URL" + (f": {problem}" if problem else ""),
                 hint=f"fix LANGFUSE_BASE_URL in {shown}, e.g. http://localhost:3001"):
        return
    service_checks()
    lf = Langfuse(base, pk, sk)
    state = langfuse_checks(lf, expected, full)
    if args.links and state.get("project_id"):
        print("\nLinks")
        for label, url in act_links(lf.base, state):
            print(f"  {label:<36} {url}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    ap.add_argument("--short", action="store_true",
                    help="check only the short path (Opening, Acts 1-2, Close); Acts 3-5 assets only warn")
    args = ap.parse_args(argv)
    del RESULTS[:]
    full = not args.short

    print("query-router readiness: " + ("full script (Acts 1-5)" if full else "SHORT PATH ONLY (Opening, Acts 1-2, Close)"))
    try:
        run_checks(args, full)
    except Exception as e:  # noqa: BLE001 - any failure must reach the verdict; Ctrl-C (BaseException) still propagates
        check(False, f"verifier stopped early: {type(e).__name__}: {e}",
              hint="the lines above still count; fix this and re-run")

    line, code = summary()
    if not full:
        line = line.replace("READY (", "READY for the short path only (", 1)
    print(f"\n{line}")
    return code

if __name__ == "__main__":
    sys.exit(main())
