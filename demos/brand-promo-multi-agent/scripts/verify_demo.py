#!/usr/bin/env python3
"""Readiness check for brand-promo-multi-agent: reads Langfuse back and says READY or NOT READY.

    uv run python scripts/verify_demo.py               # the full script: every act; exit 1 when a required check fails
    uv run python scripts/verify_demo.py --links       # also print the link each act opens
    uv run python scripts/verify_demo.py --short-path  # Acts 1-3 only: skips the Act 4-6 evidence checks

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows. Standard library only, so it runs in any Python 3.9+ without
the demo's dependencies. No model calls; about twenty Langfuse reads, done in seconds.

What it checks, act by act (DEMO_SCRIPT.md):
  Act 1  synthetic fleet history (one `<agent>_run` per fleet agent in the resolved
         demo.config.yaml, within its `synthetic_history.days_back`) and the three persona
         dashboards, which are created by hand from seed_dashboards.py's checklist
  Act 2  a seeded `promo_planner_run` trace to open as the full multi-agent tree
  Act 3  a recent live `PromoPlanner` run with its observations
  Act 4  the 12 production prompts, 20 score configs, 75+ ACTIVE golden-dataset items, and
         the three online judges enabled
  Act 5  a `baseline`-labelled experiment run on the dataset to compare the candidate with
  Act 6  the `PromoPlanner Human Review` queue with PENDING items

Every check is required for the full script. `--short-path` (Acts 1-3, the 10-12 minute
path) skips the Act 4 judges and the Act 5 and 6 evidence, and says so in its verdict.

Uses Observations API v2, Scores API v3, the experiments API and the unstable dashboards
API, which Langfuse Cloud serves. A self-hosted v3 server answers the v2 observation reads
with 404; see the README's Known limits.
"""
from __future__ import annotations

import argparse
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

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with the seeders named below.
TRACE_NAME = "PromoPlanner"                     # live runs: langfuse_trace_name from scripts/run_live_demo.py
HERO_HISTORY_TRACE = "promo_planner_run"        # seeded hero traces: src/synthetic/trace_generator.py
# The fleet and the history window come from the same config the generator reads:
# demo.config.yaml, else demo.config.example.yaml (src/config.py load_config).
EXPECTED_PROMPTS = {name: "production" for name in [  # scripts/seed_prompts.py PROMPT_REGISTRY
    "promo-planner/orchestrator/system",
    "promo-planner/orchestrator/classify-intent",
    "promo-planner/orchestrator/out-of-scope",
    "promo-planner/orchestrator/compose-brief",
    "promo-planner/research/research-task",
    "promo-planner/strategy/strategy-task",
    "promo-planner/compliance/brand-guidelines",
    "promo-planner/compliance/regulatory-check",
    "promo-planner/compliance/aggregate",
    "promo-planner/judge/tool-call-correctness",
    "promo-planner/judge/response-factuality",
    "promo-planner/judge/compliance-adherence",
]}
EXPECTED_SCORE_CONFIGS = [                      # scripts/setup_score_configs.py SCORE_CONFIGS
    "intent_classification_accuracy", "tool_call_match", "compliance_status_match", "brief_contains",
    "sku_validity", "brief_length_sanity", "tool_call_correctness", "response_factuality",
    "compliance_adherence", "brief_quality", "avg_intent_classification_accuracy", "avg_tool_call_match",
    "avg_compliance_status_match", "avg_brief_contains", "avg_sku_validity", "avg_tool_call_correctness",
    "avg_response_factuality", "avg_compliance_adherence", "certification_gate", "human_brief_review",
]
DATASET_NAME = "promo-planner-golden-v1"        # scripts/seed_dataset.py, scripts/run_experiment.py
DATASET_MIN_ITEMS = 75                          # 25 hand-authored + 50 slot-generated
QUEUE_NAME = "PromoPlanner Human Review"        # scripts/seed_annotation_queue.py, run_experiment.py
JUDGE_RULES = ["tool-call-correctness", "response-factuality",
               "compliance-adherence"]          # scripts/seed_evaluators.py EVALUATORS, created in the UI
BASELINE_LABEL = "baseline"                     # run_experiment.py's default --label, stored in run metadata
DASHBOARDS = ["Executive - Agent Fleet", "Ops - Agent Health",
              "Engineer - PromoPlanner Deep Dive"]  # scripts/seed_dashboards.py DASHBOARDS
EXPECTED_SCORES: List[str] = []                 # online judges sample 10% of runs, so no score is required per run
MIN_OBSERVATIONS = 3                            # root chain, classify_intent and its generation, at the least
MIN_GENERATIONS = 3                             # a full plan run: orchestrator calls plus the crews' own LLM calls
LOOKBACK_DAYS = 7                               # how far back to look for a live run
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
EXPERIMENTS_SINCE = "2026-01-01T00:00:00Z"      # the experiments API needs a start-time lower bound
SERVICES: List[Tuple[str, str]] = []            # no local services: the demo is a CLI against Langfuse
SEED_CMD = "uv run scripts/seed_all.py"
HISTORY_CMD = "uv run scripts/generate_history.py"
QUEUE_CMD = "uv run scripts/seed_annotation_queue.py"
RUN_CMD = "uv run scripts/run_live_demo.py play-all"
EXPERIMENT_CMD = "uv run python scripts/run_experiment.py --run-name rehearsal --sample 10 --evaluators deterministic"
TIMEOUT_S = 10
MAX_PAGES = 20

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
        try:
            # Building the request is inside the boundary: a malformed URL raises ValueError
            # here (or http.client.InvalidURL, also a ValueError, on open), not an HTTP error.
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or e.reason) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        except ValueError as e:
            raise ApiError(None, f"invalid request URL {url}: {e}") from None
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
        cur, seen = None, set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=100, **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:  # a server that repeats a cursor would otherwise loop to max_pages
                raise ApiError(None, f"{path}: the server repeated a cursor")
            seen.add(cur)
        raise ApiError(None, f"{path}: more than {max_pages} pages")


def base_url_problem(base: str) -> Optional[str]:
    """Why LANGFUSE_BASE_URL cannot be used for requests, or None. Checked before any read."""
    try:
        parts = urllib.parse.urlsplit(base)
        host, _ = parts.hostname, parts.port  # .port raises on an out-of-range port
    except ValueError as e:  # Python 3.11+ rejects an invalid bracketed host here
        return f"{base!r} is not a valid URL: {e}"
    if parts.scheme not in ("http", "https"):
        return f"{base!r} must start with http:// or https://"
    if not host:
        return f"{base!r} has no host"
    if "[" in parts.netloc:
        try:
            ipaddress.ip_address(host)  # Python 3.9 and 3.10 do not check bracketed hosts
        except ValueError:
            return f"{base!r} has an invalid IPv6 host [{host}]"
    return None


def read_local(path: Path) -> Tuple[str, Optional[str]]:
    """(text, error) of a local file: ("", None) when it is absent, the error when unreadable."""
    if not path.is_file():
        return "", None
    try:
        return path.read_text(), None
    except (OSError, UnicodeDecodeError) as e:
        return "", f"{type(e).__name__}: {e}"


# --- Demo-specific reads -------------------------------------------------------------------

def demo_config_path() -> Path:
    """The config the app and generator load: demo.config.yaml, else the committed example."""
    own = DEMO_DIR / "demo.config.yaml"
    return own if own.is_file() else DEMO_DIR / "demo.config.example.yaml"


def fleet_agents(config_text: str) -> List[str]:
    """`agent_fleet.other_agents[].name` from demo.config.yaml, without PyYAML.

    Reads the block form the config uses (`- name: "X"` items under `other_agents:`).
    tests/test_verify_demo.py checks the result against src.config.load_config().
    """
    names: List[str] = []
    in_fleet = in_others = False
    others_indent = 0
    for raw in config_text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_fleet, in_others = line.startswith("agent_fleet:"), False
            continue
        if not in_fleet:
            continue
        if re.match(r"\s*other_agents:\s*$", line):
            in_others, others_indent = True, indent
            continue
        if in_others and indent <= others_indent and not line.lstrip().startswith("-"):
            in_others = False
        m = re.match(r"\s*-\s*name:\s*(.+)$", line)
        if in_others and m:
            names.append(_unquote(m.group(1).strip()))
    return names


def history_trace_names(agents: List[str]) -> List[str]:
    """The trace names generate_history.py gives fleet agents: `<name lower>_run`."""
    return [f"{a.lower()}_run" for a in agents]


def expected_project(manifest: str, environ: Optional[MutableMapping[str, str]] = None) -> Optional[str]:
    """The project the keys must map to: DEMO_LANGFUSE_PROJECT, else `langfuse.project_name`
    in demo.config.yaml (a re-themed overlay may rename the project), else demo.yaml."""
    env = os.environ if environ is None else environ
    if env.get("DEMO_LANGFUSE_PROJECT"):
        return env["DEMO_LANGFUSE_PROJECT"]
    overlay = DEMO_DIR / "demo.config.yaml"
    if overlay.is_file():
        name = manifest_value(overlay.read_text(), "langfuse", "project_name")
        if name:
            return name
    return manifest_value(manifest, "langfuse", "project")


def obs_filter(*, trace_name: Optional[str] = None, root: bool = False) -> str:
    """Observations v2 `filter` JSON. traceName is not a query parameter of /v2/observations;
    passed as one it is silently ignored, so it goes in the filter."""
    f: List[Dict[str, Any]] = []
    if trace_name:
        f.append({"type": "string", "column": "traceName", "operator": "=", "value": trace_name})
    if root:
        f.append({"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True})
    return json.dumps(f)


def newest(lf: Langfuse, trace_name: str, since: datetime, now: datetime, *, root: bool) -> List[dict]:
    """The newest observation of a trace with this name in the window, as a list of 0 or 1.
    `basic` carries the observation name; `core` the trace id and start time."""
    return (lf.get("/api/public/v2/observations", filter=obs_filter(trace_name=trace_name, root=root),
                   fromStartTime=iso(since), toStartTime=iso(now + timedelta(minutes=5)),
                   fields="core,basic", limit=1) or {}).get("data") or []


def paged(lf: Langfuse, path: str, params: Optional[Dict[str, Any]] = None, max_pages: int = MAX_PAGES) -> List[dict]:
    """Follow page/totalPages (score configs, dashboards, queues and their items, dataset items)."""
    out: List[dict] = []
    for page in range(1, max_pages + 1):
        d = lf.get(path, **dict(params or {}, limit=100, page=page)) or {}
        out += d.get("data") or []
        if page >= int((d.get("meta") or {}).get("totalPages") or 1):
            return out
    raise ApiError(None, f"{path}: more than {max_pages} pages")


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """DEMO-SPECIFIC: one (label, url) per act of the demo script, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = [("Act 1 · persona dashboards", f"{p}/dashboards"), ("Act 1 · traces", f"{p}/traces")]
    if state.get("history_trace_id"):
        links.append(("Act 2 · a seeded multi-agent run", f"{p}/traces/{state['history_trace_id']}"))
    if state.get("trace_id"):
        links.append(("Act 3 · the latest live run", f"{p}/traces/{state['trace_id']}"))
    if state.get("dataset_id"):
        links.append(("Act 4 · the golden dataset", f"{p}/datasets/{state['dataset_id']}"))
        links.append(("Act 5 · dataset runs", f"{p}/datasets/{state['dataset_id']}"))
    if state.get("queue_id"):
        links.append(("Act 6 · the review queue", f"{p}/annotation-queues/{state['queue_id']}"))
    return links


# --- Checks -------------------------------------------------------------------------------

def service_checks() -> None:
    if not SERVICES:
        return
    print("Services")
    for name, url in SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                check(r.status < 500, f"{name} up at {url} ({r.status})")
        except urllib.error.HTTPError as e:
            check(e.code < 500, f"{name} up at {url} ({e.code})")
        except (urllib.error.URLError, OSError) as e:
            check(False, f"{name} not reachable at {url}: {type(e).__name__}", hint="start the demo's services")


def seeded_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    """Act 4's artifacts, from seed_all.py: prompts, score configs, the golden dataset."""
    print("Seeded artifacts")
    missing_prompts = []
    for name, label in EXPECTED_PROMPTS.items():
        try:
            lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label)
        except ApiError as e:
            if e.status != 404:
                raise
            missing_prompts.append(name)
    check(not missing_prompts, f"{len(EXPECTED_PROMPTS) - len(missing_prompts)}/{len(EXPECTED_PROMPTS)} "
          f"prompts under promo-planner/ with label production"
          + (f"; missing {', '.join(missing_prompts)}" if missing_prompts else ""), hint=f"run {SEED_CMD}")

    configs = paged(lf, "/api/public/score-configs")
    have = {c.get("name") for c in configs if not c.get("isArchived")}
    missing = [n for n in EXPECTED_SCORE_CONFIGS if n not in have]
    check(not missing, f"{len(EXPECTED_SCORE_CONFIGS) - len(missing)}/{len(EXPECTED_SCORE_CONFIGS)} score configs"
          + (f"; missing or archived {', '.join(missing)}" if missing else ""), hint=f"run {SEED_CMD}")

    try:
        ds = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(DATASET_NAME, safe='')}") or {}
    except ApiError as e:
        if e.status != 404:
            raise
        check(False, f"dataset {DATASET_NAME} exists", hint=f"run {SEED_CMD}")
        return
    state["dataset_id"] = ds.get("id")
    items = paged(lf, "/api/public/dataset-items", {"datasetName": DATASET_NAME})
    active = sum(1 for it in items if it.get("status") == "ACTIVE")
    check(active >= DATASET_MIN_ITEMS,
          f"dataset {DATASET_NAME}: {active} ACTIVE item(s) of {len(items)} (want {DATASET_MIN_ITEMS}+)",
          hint=f"run {SEED_CMD}; it restores archived items it owns")


def history_checks(lf: Langfuse, now: datetime, state: Dict[str, Any], agents: List[str], days: int) -> None:
    """Acts 1 and 2: the synthetic fleet history and dashboards over it."""
    print("Fleet history and dashboards")
    since = now - timedelta(days=days)
    hero = newest(lf, HERO_HISTORY_TRACE, since, now, root=False)
    if hero:
        state["history_trace_id"] = hero[0].get("traceId")
    check(bool(hero), f"seeded '{HERO_HISTORY_TRACE}' traces in the last {days} days",
          hint=f"run {HISTORY_CMD} (50k traces, several minutes, no model calls); history ages out after {days} days")
    names = history_trace_names(agents)
    missing = [n for n in names if not newest(lf, n, since, now, root=False)]
    check(bool(names) and not missing, f"{len(names) - len(missing)}/{len(names)} fleet agents in the seeded history"
          + (f"; missing {', '.join(missing)}" if missing else ""),
          hint=f"run {HISTORY_CMD} after changing agent_fleet in demo.config.yaml")
    try:
        dash_names = {d.get("name") for d in paged(lf, "/api/public/unstable/dashboards")}
    except ApiError as e:
        check(False, f"dashboards readable: {e}", hint="the three persona dashboards cannot be confirmed")
        return
    missing = [d for d in DASHBOARDS if d not in dash_names]
    check(not missing, f"{len(DASHBOARDS) - len(missing)}/{len(DASHBOARDS)} persona dashboards"
          + (f"; missing {', '.join(repr(m) for m in missing)}" if missing else ""),
          hint="create them in the UI from the checklist uv run scripts/seed_dashboards.py prints, named exactly")


def live_run_checks(lf: Langfuse, now: datetime, state: Dict[str, Any]) -> None:
    """Act 3: a recent live PromoPlanner run with its observations."""
    print("Latest live run")
    roots = newest(lf, TRACE_NAME, now - timedelta(days=LOOKBACK_DAYS), now, root=True)
    if not check(bool(roots), f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days", hint=f"run {RUN_CMD}"):
        return
    latest = roots[0]
    state["trace_id"] = trace_id = latest["traceId"]
    started = parse_time(latest["startTime"])
    obs = lf.cursor("/api/public/v2/observations", {
        "traceId": trace_id, "fields": "core",
        "fromStartTime": iso(started - timedelta(hours=1)), "toStartTime": iso(now + timedelta(minutes=5))})
    check(len(obs) >= MIN_OBSERVATIONS, f"latest run has {len(obs)} observation(s) (want {MIN_OBSERVATIONS}+)",
          hint="observations may still be landing: wait 30 s and re-run")
    gens = sum(1 for o in obs if o.get("type") == "GENERATION")
    check(gens >= MIN_GENERATIONS, f"latest run has {gens} generation(s) (want {MIN_GENERATIONS}+ for a full plan run)",
          required=False, hint="an out-of-scope query has fewer; run play-all so the last run is a full plan")
    if EXPECTED_SCORES:
        names = sorted({s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})})
        missing = [n for n in EXPECTED_SCORES if n not in names]
        check(not missing, f"latest run's scores: {', '.join(names) or 'none'}"
              + (f"; missing {', '.join(missing)}" if missing else ""), hint="scores land after the trace: wait and re-run")
    age_h = (now - started).total_seconds() / 3600
    check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
          required=False, hint=f"run {RUN_CMD} the day before")


def full_script_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    """Acts 4 to 6 evidence the full script needs: online judges, a baseline run, the queue."""
    print("Acts 4-6")
    try:
        rules = lf.cursor("/api/public/v2/evaluation-rules", {})
        enabled = {r.get("name") for r in rules if r.get("enabled")}
        missing = [n for n in JUDGE_RULES if n not in enabled]
        check(not missing, f"{len(JUDGE_RULES) - len(missing)}/{len(JUDGE_RULES)} online judges enabled"
              + (f"; missing or disabled {', '.join(missing)}" if missing else ""),
              hint="create them in the UI from the checklist uv run scripts/seed_evaluators.py prints")
    except ApiError as e:
        check(False, f"evaluation rules readable: {e}", hint="the online judges cannot be confirmed")

    if state.get("dataset_id"):
        try:
            runs = lf.cursor("/api/public/experiments", {"datasetId": state["dataset_id"],
                                                          "fromStartTime": EXPERIMENTS_SINCE, "fields": "core,metadata"})
            baseline = [r for r in runs if (r.get("metadata") or {}).get("label") == BASELINE_LABEL]
            check(bool(baseline), f"{len(baseline)} '{BASELINE_LABEL}' experiment run(s) of {len(runs)} "
                  f"on {DATASET_NAME}", hint=f"run {EXPERIMENT_CMD} (~30 s by estimate, no judge cost)")
        except ApiError as e:
            check(False, f"experiment runs readable: {e}")
    else:
        check(False, f"a '{BASELINE_LABEL}' experiment run needs the dataset", hint=f"run {SEED_CMD}")

    try:
        queue = next((q for q in paged(lf, "/api/public/annotation-queues") if q.get("name") == QUEUE_NAME), None)
        if not check(bool(queue), f"annotation queue '{QUEUE_NAME}' exists", hint=f"run {QUEUE_CMD}, which creates it"):
            return
        state["queue_id"] = queue["id"]
        pending = paged(lf, f"/api/public/annotation-queues/{queue['id']}/items", {"status": "PENDING"})
        check(bool(pending), f"annotation queue has {len(pending)} PENDING item(s)",
              hint=f"run {QUEUE_CMD}, or {EXPERIMENT_CMD} --queue-failures")
    except ApiError as e:
        check(False, f"annotation queues readable: {e}")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str], agents: List[str], days: int,
                    short_path: bool = False) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or wrong LANGFUSE_HOST region; network error: is Langfuse up?")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in .env, or set DEMO_LANGFUSE_PROJECT"):
        return state

    now = datetime.now(timezone.utc)
    try:
        seeded_checks(lf, state)
        history_checks(lf, now, state, agents, days)
        live_run_checks(lf, now, state)
        if short_path:
            print("Acts 4-6\n  SKIP  online judges, baseline experiment run, review queue (--short-path)")
        else:
            full_script_checks(lf, state)
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}",
              hint="a 404 on /api/public/v2/observations means a self-hosted v3 server: see Known limits"
              if e.status == 404 else "")
    except (ValueError, KeyError, TypeError, AttributeError) as e:  # a response in an unexpected shape
        check(False, f"Langfuse response not understood: {type(e).__name__}: {e}",
              hint="the server may be a different version than this check expects")
    return state


def run_checks(args: argparse.Namespace) -> None:
    """Every check, in order. main() owns the failure boundary around it."""
    print("Config")
    manifest, err = read_local(DEMO_DIR / "demo.yaml")
    manifest_ok = not err or check(False, f"demo.yaml unreadable: {err}")
    path = env_file(manifest_value(manifest, "", "profile"))
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    try:
        loaded, detail = load_env(path), ""
    except (OSError, UnicodeDecodeError) as e:  # load_env has already dropped the shell's LANGFUSE_*
        loaded, detail = False, f": unreadable, {type(e).__name__}: {e}"
    check(loaded, f"env loaded from {shown}{detail}; shell-exported LANGFUSE_* ignored",
          hint="cp .env.example .env and fill it in" if path.parent == DEMO_DIR else "run ./setup.sh at the repo root")
    cfg_path = demo_config_path()
    cfg_text, err = read_local(cfg_path)
    agents = fleet_agents(cfg_text)
    days_raw = manifest_value(cfg_text, "synthetic_history", "days_back") or ""
    days = int(days_raw) if days_raw.isdigit() and int(days_raw) > 0 else 0
    config_ok = check(not err and bool(agents) and days > 0,
                      f"{cfg_path.name}: unreadable, {err}" if err else
                      f"{cfg_path.name}: {len(agents)} fleet agent(s), history window {days or '?'} days",
                      hint="agent_fleet.other_agents and synthetic_history.days_back must be set")
    try:
        expected = expected_project(manifest)
    except (OSError, UnicodeDecodeError) as e:
        expected = None
        check(False, f"project name unreadable from demo.config.yaml: {type(e).__name__}: {e}")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    keys_ok = check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST set")
    url_ok = False
    if base:
        problem = base_url_problem(base)
        url_ok = check(problem is None, f"LANGFUSE_HOST {base} is an http(s) URL" if problem is None
                       else f"LANGFUSE_HOST unusable: {problem}",
                       hint="fix it in .env, for example https://us.cloud.langfuse.com")
    if keys_ok and url_ok and config_ok and manifest_ok:  # no API use on a configuration that failed
        service_checks()
        lf = Langfuse(base, pk, sk)
        state = langfuse_checks(lf, expected, agents, days, short_path=args.short_path)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<34} {url}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    ap.add_argument("--short-path", action="store_true",
                    help="readiness for Acts 1-3 only; skips the Act 4 judges and the Act 5-6 evidence")
    args = ap.parse_args(argv)  # outside the boundary: --help and usage errors exit as usual
    del RESULTS[:]
    print(f"{TRACE_NAME} readiness" + (" (short path: Acts 1-3)" if args.short_path else " (full script)"))
    try:
        run_checks(args)
    except Exception as e:  # not BaseException: Ctrl-C still stops the run
        check(False, f"verification stopped by an unexpected error: {type(e).__name__}: {e}",
              hint="the checks above still stand; fix the cause and re-run")

    line, code = summary()
    if args.short_path:
        line += " for the short path (Acts 1-3)"
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
