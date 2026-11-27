#!/usr/bin/env python3
"""Readiness check for cluster-health-investigator: reads Langfuse back and says READY or NOT READY.

    python demos/cluster-health-investigator/scripts/verify_demo.py           # one PASS / FAIL / WARN line per check
    python demos/cluster-health-investigator/scripts/verify_demo.py --links   # also print the link each act opens

Run it from the repo root. It reads the root `.env` (a `container` demo) and ignores
shell-exported LANGFUSE_* keys.

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows. Standard library only, so it runs in any Python 3.9+ without
the demo's dependencies. No model calls; a handful of Langfuse reads and one `docker
inspect`, done in seconds.

Observations: the stack's self-hosted Langfuse server (3.x) answers Observations API v2
with 404, so `Langfuse.observations()` tries v2 first and falls back to the v1 list
endpoint only on a 404. Scores use Scores API v3, which the 3.x server serves.

Required checks cover the acts every run needs (Acts 1 and 2). Acts 3 to 6 need runs you
make on purpose (a re-plan trace, an overplan fault, the experiment, the delegation
scores), so their checks only warn.
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
TRACE_NAME = "investigate-cluster-symptom"      # trace name graph.py sets with propagate_attributes
FINAL_OBSERVATION = "synthesize-diagnosis"      # last observation of every run; finds the latest run
WORKER_OBSERVATION = "worker"                   # one per planned task
ORCHESTRATOR_OBSERVATION = "orchestrator"       # one per planning round; two means a re-plan
EXPECTED_PROMPTS = {                            # (prompt, label) pairs seed_prompts.py sets
    ("cluster-health-planner", "production"),
    ("cluster-health-planner", "candidate-scoped-decomposition"),
    ("cluster-health-planner", "fault-overplan"),
    ("cluster-health-worker", "production"),
    ("cluster-health-synthesizer", "production"),
}
EXPECTED_DATASETS = {                           # dataset -> golden item count seed_datasets.py writes
    "cluster-health/plan-quality": 8,
    "cluster-health/worker-quality": 10,
}
EXPERIMENT_DATASET = "cluster-health/plan-quality"
EXPERIMENT_RUNS = ["planner-production", "planner-candidate-scoped-decomposition"]  # run_experiment.py
EXPECTED_SCORES = ["worker_count", "plan_execution_complete", "coverage_sufficient"]  # every run, from graph.py
JUDGE_SCORES = ["diagnosis-coverage", "plan-scaling"]  # managed judges seed_evaluators.sh creates
DELEGATION_SCORE = "delegation_quality"         # scripts/score_delegations.py, Act 6
# orchestrator + plan-analyses + one worker (worker, run-system-query, interpret-findings)
# + replan-gate + synthesize-diagnosis. The LangGraph callback spans come on top.
MIN_OBSERVATIONS = 7
TARGET_CONTAINER = "langfuse-clickhouse"        # the ClickHouse the agent investigates
LOOKBACK_DAYS = 7                               # how far back to look for a run
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
SERVICES: List[Tuple[str, str]] = []            # Langfuse itself is checked by the key check
COMPOSE = "docker compose --profile langfuse --profile demo run --rm cluster-health"
SEED_CMD = f"{COMPOSE} python scripts/seed_all.py"
RUN_CMD = f"{COMPOSE} python main.py"
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

    def pages(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = 20) -> List[dict]:
        """Follow `meta.page` / `meta.totalPages` (dataset items, and the v1 observations list)."""
        out: List[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params, limit=100, page=page)) or {}
            rows = d.get("data") or []
            out += rows
            total = (d.get("meta") or {}).get("totalPages") or 0
            if not rows or page >= total or len(out) >= cap:
                break
        return out[:cap]

    obs_api: Optional[str] = None  # "v2" or "v1", decided by the first observations read

    def observations(self, params: Dict[str, Any], cap: int = 1000) -> List[dict]:
        """Observations by name or traceId, time-bounded, on whichever API the server serves.

        Observations API v2 first. The stack's self-hosted 3.x server answers it with 404
        ("only available in a Langfuse v4 write mode"), and only then does this fall back to
        the v1 list endpoint, the read `CLAUDE.md` names for a 3.x server. Any other error is
        raised, so a 401 or a 500 is never mistaken for "use v1".
        """
        if self.obs_api != "v1":
            try:
                rows = self.cursor("/api/public/v2/observations", dict(params, fields="core,basic"), cap=cap)
                self.obs_api = "v2"
                return rows
            except ApiError as e:
                if e.status != 404 or self.obs_api == "v2":
                    raise
                self.obs_api = "v1"
        return self.pages("/api/public/observations", params, cap=cap)


# --- Run-shape logic (pure; tests/test_verify_demo.py covers it) ---------------------------

def newest(rows: Iterable[dict]) -> Optional[dict]:
    """The row with the latest startTime; either API's ordering is not relied on."""
    best, best_t = None, None
    for r in rows:
        try:
            t = parse_time(r.get("startTime") or "")
        except ValueError:
            continue
        if best_t is None or t > best_t:
            best, best_t = r, t
    return best


def score_trace_id(score: dict) -> Optional[str]:
    """The trace a Scores v3 row belongs to: `subject.traceId`, or `subject.id` for a
    trace-level score. A flat `traceId` is accepted too, for older shapes."""
    subject = score.get("subject") or {}
    if subject.get("traceId"):
        return subject["traceId"]
    if subject.get("kind") == "trace" and subject.get("id"):
        return subject["id"]
    return score.get("traceId")


def replan_traces(orchestrators: Iterable[dict]) -> List[str]:
    """Trace ids with two or more `orchestrator` observations: a second planning round ran."""
    counts: Dict[str, int] = {}
    for o in orchestrators:
        if o.get("traceId"):
            counts[o["traceId"]] = counts.get(o["traceId"], 0) + 1
    return sorted(t for t, n in counts.items() if n >= 2)


def fanout_extremes(worker_counts: Iterable[dict]) -> Tuple[Optional[Tuple[str, float]], Optional[Tuple[str, float]]]:
    """(narrowest, broadest) run as (trace id, worker_count) from `worker_count` scores.
    Act 2 opens the two side by side."""
    pairs = [(score_trace_id(s), s.get("value")) for s in worker_counts]
    pairs = [(t, float(v)) for t, v in pairs if t and isinstance(v, (int, float))]
    if not pairs:
        return None, None
    return min(pairs, key=lambda p: p[1]), max(pairs, key=lambda p: p[1])


def run_names(rows: Iterable[dict]) -> List[str]:
    """Names from an experiments or dataset-runs listing."""
    return sorted({r.get("name") for r in rows if r.get("name")})


def missing_runs(names: Iterable[str], expected: Iterable[str]) -> List[str]:
    """Expected run names with no listed run that equals them or starts with them."""
    names = list(names)
    return [e for e in expected if not any(n == e or n.startswith(e) for n in names)]


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """DEMO-SPECIFIC: one (label, url) per act of the demo script, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = []
    if state.get("trace_id"):
        links.append(("Act 1 · the latest run", f"{p}/traces/{state['trace_id']}"))
    if state.get("narrow_trace"):
        links.append(("Act 2 · narrow fan-out", f"{p}/traces/{state['narrow_trace']}"))
    if state.get("broad_trace"):
        links.append(("Act 2 · broad fan-out", f"{p}/traces/{state['broad_trace']}"))
    if state.get("replan_trace"):
        links.append(("Act 3 · the re-plan run", f"{p}/traces/{state['replan_trace']}"))
    links.append(("Act 4 · dashboards", f"{p}/dashboards"))
    if state.get("dataset_id"):
        links.append(("Act 5 · plan-quality dataset", f"{p}/datasets/{state['dataset_id']}"))
    links.append(("Act 6 · evaluators", f"{p}/evals"))
    return links


# --- Checks -------------------------------------------------------------------------------

def container_health(name: str) -> Tuple[bool, str]:
    """(running, status) for a Docker container, from `docker inspect`."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", name],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"docker not usable: {type(e).__name__}"
    if out.returncode != 0:
        return False, (out.stderr.strip().splitlines() or ["not found"])[-1][:120]
    status = out.stdout.strip()
    return status.startswith("running") and "unhealthy" not in status, status


def service_checks() -> None:
    print("Services")
    ok, status = container_health(TARGET_CONTAINER)
    check(ok, f"{TARGET_CONTAINER} (the investigation target) is {status}",
          hint="docker compose --profile langfuse up -d")
    for name, url in SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                check(r.status < 500, f"{name} up at {url} ({r.status})")
        except urllib.error.HTTPError as e:
            check(e.code < 500, f"{name} up at {url} ({e.code})")
        except (urllib.error.URLError, OSError) as e:
            check(False, f"{name} not reachable at {url}: {type(e).__name__}", hint="start the demo's services")


def seeded_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    print("Seeded artifacts")
    for name, label in sorted(EXPECTED_PROMPTS):
        try:
            p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label) or {}
            check(True, f"prompt {name}: {label} = v{p.get('version')}")
        except ApiError as e:
            check(False, f"prompt {name} with label {label}: {e}", hint=f"run {SEED_CMD}")
    for name, want in EXPECTED_DATASETS.items():
        try:
            d = lf.get(f"/api/public/v2/datasets/{urllib.parse.quote(name, safe='')}") or {}
        except ApiError as e:
            check(False, f"dataset {name}: {e}", hint=f"run {SEED_CMD}")
            continue
        if name == EXPERIMENT_DATASET:
            state["dataset_id"] = d.get("id")
        n = len(lf.pages("/api/public/dataset-items", {"datasetName": name}))
        check(n >= want, f"dataset {name}: {n} item(s) (want {want}+)", hint=f"run {SEED_CMD}")


def latest_run_checks(lf: Langfuse, state: Dict[str, Any], now: datetime) -> bool:
    print("Latest run")
    window = {"fromStartTime": iso(now - timedelta(days=LOOKBACK_DAYS)), "toStartTime": iso(now + timedelta(minutes=5))}
    latest = newest(lf.observations(dict(window, name=FINAL_OBSERVATION), cap=100))
    if not check(bool(latest), f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days "
                 f"(found by its '{FINAL_OBSERVATION}' observation)", hint=f"run {RUN_CMD}"):
        return False
    state["trace_id"] = trace_id = latest["traceId"]
    started = parse_time(latest["startTime"])
    obs = lf.observations({"traceId": trace_id, "fromStartTime": iso(started - timedelta(hours=1)),
                           "toStartTime": iso(now + timedelta(minutes=5))})
    workers = sum(1 for o in obs if o.get("name") == WORKER_OBSERVATION)
    check(len(obs) >= MIN_OBSERVATIONS and workers >= 1,
          f"latest run has {len(obs)} observation(s), {workers} worker(s) (want {MIN_OBSERVATIONS}+, 1+)",
          hint="observations may still be landing: wait 30 s and re-run")
    scores = lf.cursor("/api/public/v3/scores", {"traceId": trace_id, "fields": "subject"})
    names = sorted({s.get("name") for s in scores if s.get("name")})
    missing = [n for n in EXPECTED_SCORES if n not in names]
    check(not missing, f"latest run's scores: {', '.join(names) or 'none'}"
          + (f"; missing {', '.join(missing)}" if missing else ""), hint="scores land after the trace: wait and re-run")
    wc = next((s.get("value") for s in scores if s.get("name") == "worker_count"), None)
    if wc is not None:
        check(int(wc) == workers, f"worker_count score {int(wc)} matches {workers} worker observation(s)",
              hint="observations may still be landing: wait 30 s and re-run")
    judged = [n for n in JUDGE_SCORES if n in names]
    check(len(judged) == len(JUDGE_SCORES), f"managed judge scores on the latest run: {', '.join(judged) or 'none'}",
          required=False, hint="seed them from the host (bash demos/cluster-health-investigator/scripts/"
                               "seed_evaluators.sh), then make a new run; judges score only runs made after "
                               "seeding, a minute or so after the trace")
    age_h = (now - started).total_seconds() / 3600
    check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
          required=False, hint=f"run {RUN_CMD} the day before")
    return True


def act_checks(lf: Langfuse, state: Dict[str, Any], now: datetime) -> None:
    """Optional acts: each needs a run made on purpose, so each only warns."""
    print("Acts")
    since = iso(now - timedelta(days=LOOKBACK_DAYS))
    counts = lf.cursor("/api/public/v3/scores", {"name": "worker_count", "fromTimestamp": since, "fields": "subject"},
                       cap=500)
    narrow, broad = fanout_extremes(counts)
    if narrow and broad:
        state["narrow_trace"], state["broad_trace"] = narrow[0], broad[0]
    check(bool(narrow and broad and broad[1] > narrow[1]),
          "Act 2: runs with different fan-out"
          + (f" (narrowest {narrow[1]:g}, broadest {broad[1]:g} workers)" if narrow and broad else ""),
          required=False, hint="pre-run a narrow and a broad symptom from DEMO_SYMPTOMS.md")

    window = {"fromStartTime": since, "toStartTime": iso(now + timedelta(minutes=5))}
    replans = replan_traces(lf.observations(dict(window, name=ORCHESTRATOR_OBSERVATION)))
    if replans:
        state["replan_trace"] = replans[0]
    check(bool(replans), f"Act 3: {len(replans)} run(s) with a second planning round", required=False,
          hint="run symptom #3 in DEMO_SYMPTOMS.md a few times; the re-plan is not guaranteed")

    cap = int(os.environ.get("MAX_WORKERS_TOTAL") or 8)
    capped = sum(1 for s in counts if isinstance(s.get("value"), (int, float)) and s["value"] >= cap)
    check(capped > 0, f"Act 4: {capped} run(s) at the {cap}-worker cap (the overplan fault)", required=False,
          hint=f"{COMPOSE} python main.py --fault overplan")

    if state.get("dataset_id"):
        try:
            rows = lf.cursor("/api/public/experiments", {"datasetId": state["dataset_id"], "fromStartTime": since})
        except ApiError as e:
            if e.status != 404:  # a 3.x server has no Experiments API; it lists dataset runs instead
                raise
            rows = lf.pages(f"/api/public/datasets/{urllib.parse.quote(EXPERIMENT_DATASET, safe='')}/runs", {})
        gone = missing_runs(run_names(rows), EXPERIMENT_RUNS)
        check(not gone, "Act 5: planner experiment runs " + ", ".join(EXPERIMENT_RUNS)
              + (f"; missing {', '.join(gone)}" if gone else ""), required=False,
              hint=f"{COMPOSE} python scripts/run_experiment.py (model calls: 8 items x 2 labels)")

    delegations = lf.cursor("/api/public/v3/scores", {"name": DELEGATION_SCORE, "fromTimestamp": since}, cap=100)
    check(bool(delegations), f"Act 6: {len(delegations)} {DELEGATION_SCORE} score(s) in the last {LOOKBACK_DAYS} days",
          required=False, hint=f"{COMPOSE} python scripts/score_delegations.py (one judge call per worker)")


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
                 hint="use this project's keys in .env, or fix langfuse.project in demo.yaml"):
        return state

    try:
        now = datetime.now(timezone.utc)
        seeded_checks(lf, state)
        if latest_run_checks(lf, state, now):
            act_checks(lf, state, now)
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
    print("cluster-health-investigator readiness")
    print("Config")
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="cp .env.example .env and fill it in" if path.parent == DEMO_DIR else "run ./setup.sh at the repo root")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set",
             hint="run ./setup.sh at the repo root"):
        service_checks()
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
