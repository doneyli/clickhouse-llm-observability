#!/usr/bin/env python3
"""Readiness check for brand-promo-multi-agent: reads Langfuse back and says READY or NOT READY.

    uv run python scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    uv run python scripts/verify_demo.py --links    # also print the link each act opens

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows. Standard library only, so it runs in any Python 3.9+ without
the demo's dependencies. No model calls; about twenty Langfuse reads, done in seconds.

What it checks, act by act (DEMO_SCRIPT.md):
  Act 1  synthetic fleet history (`<agent>_run` traces from generate_history.py) and the
         three persona dashboards, which are created by hand from seed_dashboards.py's checklist
  Act 2  a seeded `promo_planner_run` trace to open as the full multi-agent tree
  Act 3  a recent live `PromoPlanner` run with its observations
  Act 4  the 12 production prompts, 20 score configs and the 75-item golden dataset
  Act 5  experiment runs on the dataset (warn only)
  Act 6  the `PromoPlanner Human Review` queue with items (warn only)

Uses Observations API v2, Scores API v3, the experiments API and the unstable dashboards
API, which Langfuse Cloud serves. A self-hosted v3 server answers the v2 observation reads
with 404; see the README's Known limits.
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

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with the seeders named below.
TRACE_NAME = "PromoPlanner"                     # live runs: langfuse_trace_name from scripts/run_live_demo.py
HERO_HISTORY_TRACE = "promo_planner_run"        # seeded hero traces: src/synthetic/trace_generator.py
FLEET_AGENTS = ["CustomerCareBot", "SupplyChainPlanner", "ShelfImageAnalyzer",
                "InternalKBSearch", "FinanceCloseBot"]  # agent_fleet.other_agents; traces are `<name lower>_run`
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
DASHBOARDS = ["Executive - Agent Fleet", "Ops - Agent Health",
              "Engineer - PromoPlanner Deep Dive"]  # scripts/seed_dashboards.py DASHBOARDS
EXPECTED_SCORES: List[str] = []                 # online judges sample 10% of runs, so no score is required per run
MIN_OBSERVATIONS = 3                            # root chain, classify_intent and its generation, at the least
MIN_GENERATIONS = 3                             # a full plan run: orchestrator calls plus the crews' own LLM calls
LOOKBACK_DAYS = 7                               # how far back to look for a live run
HISTORY_DAYS = 30                               # synthetic_history.days_back in demo.config.example.yaml
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
EXPERIMENTS_SINCE = "2026-01-01T00:00:00Z"      # the experiments API needs a start-time lower bound
SERVICES: List[Tuple[str, str]] = []            # no local services: the demo is a CLI against Langfuse
SEED_CMD = "uv run scripts/seed_all.py"
HISTORY_CMD = "uv run scripts/generate_history.py"
RUN_CMD = "uv run scripts/run_live_demo.py play-all"
EXPERIMENT_CMD = "uv run python scripts/run_experiment.py --run-name rehearsal --sample 10 --evaluators deterministic"
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


# --- Demo-specific reads -------------------------------------------------------------------

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
    """The newest observation of a trace with this name in the window, as a list of 0 or 1."""
    return (lf.get("/api/public/v2/observations", filter=obs_filter(trace_name=trace_name, root=root),
                   fromStartTime=iso(since), toStartTime=iso(now + timedelta(minutes=5)),
                   fields="core,basic", limit=1) or {}).get("data") or []


def paged(lf: Langfuse, path: str, params: Optional[Dict[str, Any]] = None, max_pages: int = 20) -> List[dict]:
    """Follow page/totalPages (score configs, annotation queues and their items, dataset items)."""
    out: List[dict] = []
    for page in range(1, max_pages + 1):
        d = lf.get(path, **dict(params or {}, limit=100, page=page)) or {}
        out += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
    return out


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

    have = {c.get("name") for c in paged(lf, "/api/public/score-configs")}
    missing = [n for n in EXPECTED_SCORE_CONFIGS if n not in have]
    check(not missing, f"{len(EXPECTED_SCORE_CONFIGS) - len(missing)}/{len(EXPECTED_SCORE_CONFIGS)} score configs"
          + (f"; missing {', '.join(missing)}" if missing else ""), hint=f"run {SEED_CMD}")

    try:
        ds = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(DATASET_NAME, safe='')}") or {}
    except ApiError as e:
        if e.status != 404:
            raise
        check(False, f"dataset {DATASET_NAME} exists", hint=f"run {SEED_CMD}")
        return
    state["dataset_id"] = ds.get("id")
    meta = (lf.get("/api/public/dataset-items", datasetName=DATASET_NAME, limit=1) or {}).get("meta") or {}
    n = int(meta.get("totalItems") or 0)
    check(n >= DATASET_MIN_ITEMS, f"dataset {DATASET_NAME}: {n} items (want {DATASET_MIN_ITEMS}+)",
          hint=f"run {SEED_CMD}")


def history_checks(lf: Langfuse, now: datetime, state: Dict[str, Any]) -> None:
    """Acts 1 and 2: the synthetic fleet history and dashboards over it."""
    print("Fleet history and dashboards")
    since = now - timedelta(days=HISTORY_DAYS)
    hero = newest(lf, HERO_HISTORY_TRACE, since, now, root=False)
    if hero:
        state["history_trace_id"] = hero[0].get("traceId")
    check(bool(hero), f"seeded '{HERO_HISTORY_TRACE}' traces in the last {HISTORY_DAYS} days",
          hint=f"run {HISTORY_CMD} (50k traces, several minutes, no model calls); history ages out after {HISTORY_DAYS} days")
    found = [a for a in FLEET_AGENTS if newest(lf, f"{a.lower()}_run", since, now, root=False)]
    check(len(found) == len(FLEET_AGENTS), f"{len(found)}/{len(FLEET_AGENTS)} fleet agents in the seeded history",
          hint=f"run {HISTORY_CMD}")
    try:
        names = {d.get("name") for d in paged(lf, "/api/public/unstable/dashboards")}
    except ApiError as e:
        check(False, f"dashboards readable: {e}", required=False, hint="check the three persona dashboards in the UI")
        return
    missing = [d for d in DASHBOARDS if d not in names]
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


def optional_act_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    """Acts 4 to 6 extras that only warn: online judges, experiment runs, the review queue."""
    print("Evaluation extras (warn only)")
    try:
        rules = (lf.get("/api/public/v2/evaluation-rules", limit=50) or {}).get("data") or []
        on = [r for r in rules if r.get("enabled")]
        check(bool(on), f"{len(on)} enabled online evaluator rule(s)", required=False,
              hint="create the three judges from the checklist uv run scripts/seed_evaluators.py prints")
    except ApiError as e:
        check(False, f"evaluator rules readable: {e}", required=False)
    if state.get("dataset_id"):
        try:
            runs = lf.cursor("/api/public/experiments", {"datasetId": state["dataset_id"],
                                                          "fromStartTime": EXPERIMENTS_SINCE, "fields": "core"})
            check(bool(runs), f"{len(runs)} experiment run(s) on {DATASET_NAME}", required=False,
                  hint=f"run {EXPERIMENT_CMD} (~30 s, no judge cost)")
        except ApiError as e:
            check(False, f"experiment runs readable: {e}", required=False)
    try:
        queue = next((q for q in paged(lf, "/api/public/annotation-queues") if q.get("name") == QUEUE_NAME), None)
        if not check(bool(queue), f"annotation queue '{QUEUE_NAME}' exists", required=False,
                     hint="run uv run scripts/seed_annotation_queue.py, which creates it"):
            return
        state["queue_id"] = queue["id"]
        items = paged(lf, f"/api/public/annotation-queues/{queue['id']}/items")
        check(bool(items), f"annotation queue has {len(items)} item(s)", required=False,
              hint=f"run {EXPERIMENT_CMD} --queue-failures (routes low scorers in)")
    except ApiError as e:
        check(False, f"annotation queues readable: {e}", required=False)


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
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
        history_checks(lf, now, state)
        live_run_checks(lf, now, state)
        optional_act_checks(lf, state)
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}",
              hint="a 404 on /api/public/v2/observations means a self-hosted v3 server: see Known limits"
              if e.status == 404 else "")
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
    expected = expected_project(manifest)
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST set"):
        service_checks()
        lf = Langfuse(base, pk, sk)
        state = langfuse_checks(lf, expected)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<34} {url}")

    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
