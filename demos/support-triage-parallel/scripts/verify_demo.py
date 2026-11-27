#!/usr/bin/env python3
"""Readiness check for support-triage-parallel: reads Langfuse back and says READY or NOT READY.

    python demos/support-triage-parallel/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check
    python demos/support-triage-parallel/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root. It reads the root `.env` (this is a `container` demo) and ignores
shell-exported LANGFUSE_* keys. An HTTP 200 proves little on its own (a trace can exist
before its observations land), so every check counts real rows. Standard library only, so
it runs in any Python 3.9+ without the demo's dependencies. No model calls; a handful of
Langfuse reads and one ping to the ClickHouse playground, done in seconds.

What it checks: the keys map to the `LLM Observability Demo` project; the ClickHouse
playground the voter executes against answers; the 7 prompts and 2 datasets that
`scripts/seed_all.py` seeds; and the latest `triage-support-ticket` run with every stage
observation and its 3 scores. Optional acts only warn: the pinned tie trace (Act 3), the
aggregator experiment runs (Act 6) and the 2 evaluator scores.

Observations: Observations API v2 first. The root compose pins a v3 Langfuse server
(3.221.1), where v2 returns 404, so on a 404 only it falls back to the v1 read
`GET /api/public/observations`. Experiments likewise fall back to the dataset-runs read.
Scores always come from Scores API v3, which the v3 server serves.
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

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with main.py and seed_all.py.
TRACE_NAME = "triage-support-ticket"            # root span and trace name, main.py:44-46
SEED_PROMPTS = [                                # what scripts/seed_prompts.py seeds, label production
    "support-triage-summary",
    "support-triage-sentiment",
    "support-triage-category",
    "support-triage-policy-guard",
    "support-triage-synthesis",
    "support-triage-tie-break-judge",
    "support-triage-sql-voter",                 # seeded as v1 baseline + v2 production
]
PROMPT_LABEL = "production"
VERSIONED_PROMPT = "support-triage-sql-voter"   # the Prompts tab shows its version history
EXPECTED_DATASETS = {                           # what scripts/seed_datasets.py seeds -> item count
    "support-triage/category-branch": 12,
    "support-triage/sql-voting": 10,
}
EXPERIMENT_DATASET = "support-triage/sql-voting"
EXPERIMENT_RUN_PREFIX = "aggregator-"           # scripts/run_experiment.py run names
STAGE_OBSERVATIONS = [                          # one run of main.py, triage_pipeline.py + sql_voting.py
    TRACE_NAME,
    "analyze-sections",
    "branch-summary",
    "branch-sentiment-urgency",
    "branch-category",
    "branch-policy-guard",
    "synthesize-triage-brief",
    "synthesis-llm",
    "vote-sql",
    "vote-candidate",
    "validate-candidates",
    "explain-candidate",
    "tally-votes",
]
TIE_OBSERVATION = "tie-break-judge"             # present only when the vote split (Act 3)
EXPECTED_SCORES = ["consensus_confidence", "sql_validity_rate", "policy_flagged"]
EVALUATOR_SCORES = ["correlated-vote-risk", "consensus_margin_ok"]  # managed judge, code evaluator
LOOKBACK_DAYS = 7                               # how far back to look for a run
EXPERIMENT_LOOKBACK_DAYS = 30
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
PLAYGROUND_DEFAULT = "sql-clickhouse.clickhouse.com"
SEED_CMD = "python demos/support-triage-parallel/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm support-triage-parallel python main.py"
TIE_CMD = RUN_CMD + " --ticket TCK-002"
EXPERIMENT_CMD = "python demos/support-triage-parallel/scripts/run_experiment.py --strategy all"
EVALUATOR_CMD = "./scripts/seed-support-triage-evaluators.sh && ./scripts/seed-code-evaluators.sh"
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
    """The few public-API calls this check needs: projects, prompts, datasets, Observations
    API v2 (v1 on a v3 server), Scores API v3 and experiments. No legacy /traces reads."""

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

    def pages(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = 10) -> List[dict]:
        """Follow page numbers (the v1 observations read, datasets, dataset items)."""
        out: List[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params, limit=100, page=page)) or {}
            out += d.get("data") or []
            if page >= ((d.get("meta") or {}).get("totalPages") or 1) or len(out) >= cap:
                break
        return out[:cap]


class Observations:
    """Observations API v2, falling back to the v1 read only when the server answers 404.

    The root compose pins a v3 Langfuse server, which serves no Observations v2. `version`
    records which read answered so the output can say so.
    """

    def __init__(self, lf: Langfuse) -> None:
        self.lf = lf
        self.version: Optional[str] = None

    def list(self, since: datetime, until: datetime, *, name: Optional[str] = None,
             trace_id: Optional[str] = None, cap: int = 1000) -> List[dict]:
        """Observations in a time window, by name or trace, each with name, traceId,
        startTime and a `root` flag."""
        window = {"fromStartTime": iso(since), "toStartTime": iso(until)}
        if self.version != "v1":
            try:
                rows = self.lf.cursor("/api/public/v2/observations", dict(
                    window, name=name, traceId=trace_id, fields="core,basic"), cap=cap)
                self.version = "v2"
                return [dict(r, root=bool(r.get("isRootObservation"))) for r in rows]
            except ApiError as e:
                if e.status != 404 or self.version == "v2":
                    raise
                self.version = "v1"
        rows = self.lf.pages("/api/public/observations", dict(window, name=name, traceId=trace_id), cap=cap)
        return [dict(r, root=not r.get("parentObservationId")) for r in rows]


def latest_roots(rows: List[dict], name: str) -> List[dict]:
    """Root observations called `name`, newest first. Sorted here: the v1 read's order is
    not part of its contract."""
    roots = [r for r in rows if r.get("root") and r.get("name") == name and r.get("startTime")]
    return sorted(roots, key=lambda r: parse_time(r["startTime"]), reverse=True)


def stage_gaps(observations: List[dict]) -> List[str]:
    """Stage observation names a full run has that this trace lacks, in pipeline order."""
    have = {o.get("name") for o in observations}
    return [n for n in STAGE_OBSERVATIONS if n not in have]


def run_names(rows: List[dict]) -> List[str]:
    """Names of experiment runs from either the experiments read or the dataset-runs read."""
    names = []
    for r in rows:
        for key in ("name", "runName", "datasetRunName"):
            if isinstance(r.get(key), str):
                names.append(r[key])
    return sorted(set(names))


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per act of the demo script, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = []
    if state.get("trace_id"):
        links.append(("Act 1-2 · the latest run", f"{p}/traces/{state['trace_id']}"))
    if state.get("tie_trace_id"):
        links.append(("Act 3-4 · the tie trace", f"{p}/traces/{state['tie_trace_id']}"))
    links.append(("Act 5 · dashboards", f"{p}/dashboards"))
    if state.get("experiment_dataset_id"):
        links.append(("Act 6 · aggregator runs", f"{p}/datasets/{state['experiment_dataset_id']}"))
    links.append(("Act 7 · traces (fault run)", f"{p}/traces"))
    links.append(("Show me the code · prompt", f"{p}/prompts/{urllib.parse.quote(VERSIONED_PROMPT, safe='')}"))
    return links


# --- Checks -------------------------------------------------------------------------------

def playground_check(host: str) -> None:
    """The voter validates and executes every SQL candidate against this playground."""
    print("Services")
    url = f"https://{host}/ping"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            check(r.status < 500, f"ClickHouse playground answers at {url} ({r.status})")
    except urllib.error.HTTPError as e:
        check(e.code < 500, f"ClickHouse playground answers at {url} ({e.code})")
    except (urllib.error.URLError, OSError) as e:
        check(False, f"ClickHouse playground not reachable at {url}: {type(e).__name__}",
              hint="every candidate fails closed without it: check outbound HTTPS")


def expected_prompt_texts() -> Dict[str, str]:
    """The production text scripts/seed_prompts.py seeds per prompt (stdlib-only import)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_prompts  # noqa: E402  (same folder; reads env only for its own HOST/keys)
    texts = {name: text for name, text, _config, _msg in seed_prompts.SINGLE_VERSION}
    texts[VERSIONED_PROMPT] = seed_prompts.SQL_VOTER_V2
    return texts


def seeded_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    print("Seeded artifacts")
    texts = expected_prompt_texts()
    for name in SEED_PROMPTS:
        try:
            p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=PROMPT_LABEL) or {}
        except ApiError as e:
            check(False, f"prompt {name} with label {PROMPT_LABEL}: {e}", hint=f"run {SEED_CMD}")
            continue
        check(True, f"prompt {name}: {PROMPT_LABEL} = v{p.get('version')}")
        if (p.get("prompt") or "").strip() != texts.get(name, "").strip():
            check(False, f"prompt {name}: {PROMPT_LABEL} text differs from scripts/seed_prompts.py",
                  required=False, hint=f"a rehearsal edit? {SEED_CMD} puts it back")
        if name == VERSIONED_PROMPT:
            check((p.get("version") or 0) >= 2, f"prompt {name} has version history "
                  f"({PROMPT_LABEL} is v{p.get('version')}, want v2+)", required=False,
                  hint="seed_prompts.py seeds v1 then v2 only on a fresh project")

    datasets = {d.get("name"): d for d in lf.pages("/api/public/v2/datasets", {})}
    for name, want in EXPECTED_DATASETS.items():
        if not check(name in datasets, f"dataset {name} exists", hint=f"run {SEED_CMD}"):
            continue
        if name == EXPERIMENT_DATASET:
            state["experiment_dataset_id"] = datasets[name].get("id")
        d = lf.get("/api/public/dataset-items", datasetName=name, limit=1, page=1) or {}
        n = (d.get("meta") or {}).get("totalItems", len(d.get("data") or []))
        check(n >= want, f"dataset {name}: {n} item(s) (want {want})", hint=f"run {SEED_CMD}")


def latest_run_checks(lf: Langfuse, obs: Observations, state: Dict[str, Any]) -> None:
    print("Latest run")
    now = datetime.now(timezone.utc)
    since, until = now - timedelta(days=LOOKBACK_DAYS), now + timedelta(minutes=5)
    roots = latest_roots(obs.list(since, until, name=TRACE_NAME), TRACE_NAME)
    if obs.version == "v1":
        print("  note  Observations v2 answered 404 (a v3 Langfuse server): read through v1")
    if not check(bool(roots), f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days", hint=f"run {RUN_CMD}"):
        return
    latest = roots[0]
    state["trace_id"] = trace_id = latest["traceId"]
    started = parse_time(latest["startTime"])
    rows = obs.list(started - timedelta(hours=1), until, trace_id=trace_id)
    gaps = stage_gaps(rows)
    check(not gaps, f"latest run has {len(rows)} observation(s) covering all {len(STAGE_OBSERVATIONS)} stages"
          + (f"; missing {', '.join(gaps)}" if gaps else ""),
          hint="observations may still be landing: wait 30 s and re-run")
    names = sorted({s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})})
    missing = [n for n in EXPECTED_SCORES if n not in names]
    check(not missing, f"latest run's scores: {', '.join(names) or 'none'}"
          + (f"; missing {', '.join(missing)}" if missing else ""),
          hint="scores land after the trace: wait and re-run")
    no_eval = [n for n in EVALUATOR_SCORES if n not in names]
    check(not no_eval, "evaluator scores on the latest run" + (f": missing {', '.join(no_eval)}" if no_eval else ""),
          required=False, hint=f"both score NEW traces only: {EVALUATOR_CMD}, then run again")
    age_h = (now - started).total_seconds() / 3600
    check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
          required=False, hint=f"run {RUN_CMD} the day before")

    # Act 3: a demo run whose vote split, so the tie-break judge fired. Experiment traces
    # also have tie-break judges, so keep only the ones under a triage-support-ticket root.
    demo_traces = {r["traceId"] for r in roots}
    ties = [t for t in obs.list(since, until, name=TIE_OBSERVATION)
            if t.get("traceId") in demo_traces and t.get("startTime")]
    ties.sort(key=lambda t: parse_time(t["startTime"]), reverse=True)
    if check(bool(ties), f"a tie trace for Act 3 ({TIE_OBSERVATION} fired) in the last {LOOKBACK_DAYS} days",
             required=False, hint=f"run {TIE_CMD} until the step log shows tie_break=True"):
        state["tie_trace_id"] = ties[0]["traceId"]


def experiment_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    """Act 6: the aggregator runs on the sql-voting dataset. Optional."""
    print("Act 6 · experiment")
    since = datetime.now(timezone.utc) - timedelta(days=EXPERIMENT_LOOKBACK_DAYS)
    try:
        if not state.get("experiment_dataset_id"):
            raise ApiError(404, "dataset not seeded")
        rows = (lf.get("/api/public/experiments", datasetId=state["experiment_dataset_id"],
                       fromStartTime=iso(since), limit=100) or {}).get("data") or []
    except ApiError as e:
        if e.status != 404:
            raise
        try:
            rows = lf.pages(f"/api/public/datasets/{urllib.parse.quote(EXPERIMENT_DATASET, safe='')}/runs", {})
        except ApiError as e2:
            if e2.status != 404:
                raise
            rows = []
    runs = [n for n in run_names(rows) if n.startswith(EXPERIMENT_RUN_PREFIX)]
    check(bool(runs), f"aggregator runs on {EXPERIMENT_DATASET}: {', '.join(runs) or 'none'}",
          required=False, hint=f"{EXPERIMENT_CMD}  (calls models: 3 strategies x 10 items x 5 samples)")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or wrong LANGFUSE_BASE_URL; network error: is the langfuse profile up?")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in the root .env, or fix langfuse.project in demo.yaml"):
        return state
    try:
        seeded_checks(lf, state)
        latest_run_checks(lf, Observations(lf), state)
        experiment_checks(lf, state)
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
    print("support-triage-parallel readiness")
    print("Config")
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="run ./setup.sh at the repo root")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    check(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set (every branch, sample and judge)",
          hint="add it to the root .env, or ANTHROPIC_API_KEY=sk-... ./setup.sh")
    if check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set"):
        playground_check(os.environ.get("PUBLIC_CH_HOST") or PLAYGROUND_DEFAULT)
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
