#!/usr/bin/env python3
"""Readiness check for support-triage-parallel: reads Langfuse back and says READY or NOT READY.

    python demos/support-triage-parallel/scripts/verify_demo.py            # the full script (Acts 1-7)
    python demos/support-triage-parallel/scripts/verify_demo.py --short    # the 8-minute short path (Acts 1-3)
    python demos/support-triage-parallel/scripts/verify_demo.py --links    # also print the link each act opens

Run it from the repo root. It reads the root `.env` (this is a `container` demo) and ignores
shell-exported LANGFUSE_* keys. An HTTP 200 proves little on its own (a trace can exist
before its observations land), so every check counts real rows. Standard library only, so
it runs in any Python 3.9+ without the demo's dependencies. No model calls; a handful of
Langfuse reads and one ping to the ClickHouse playground, done in seconds.

What it requires, for either path: the keys map to the `LLM Observability Demo` project;
the ClickHouse playground the voter executes against answers; the 7 prompts that
`scripts/seed_all.py` seeds carry `production`; both datasets hold every expected item
ACTIVE with the checked-in content (and, for sql-voting, a pinned signature); the latest
`triage-support-ticket` run has every stage observation and its 3 scores; and a demo run
whose vote split, so the tie-break judge fired (Act 3, pre-run in the pre-flight).

The full script also requires the three `aggregator-<strategy>` experiment runs (Act 6) and
the two evaluator scores (`correlated-vote-risk`, `consensus_margin_ok`) on a demo run; with
`--short` those two only warn. A stale run or a prompt edited since seeding always warns.

Reads: projects, prompts, datasets, dataset items, Observations API v2, Scores API v3 and
the experiments read. The root compose pins a v3 Langfuse server (3.221.1), where
Observations v2 and `/experiments` answer 404, so on a 404 only it falls back to the v1
`GET /api/public/observations` and `GET /api/public/datasets/{name}/runs`. No /traces reads.
"""
from __future__ import annotations

import argparse
import base64
import http.client
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
STRATEGIES = ["result-signature", "majority-exact", "judge-consensus"]  # run_experiment.py
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
TIE_STRATEGY = "result-signature"               # judge-consensus fires the judge on every run
EXPECTED_SCORES = ["consensus_confidence", "sql_validity_rate", "policy_flagged"]
EVALUATOR_SCORES = ["correlated-vote-risk", "consensus_margin_ok"]  # managed judge, code evaluator
LOOKBACK_DAYS = 7                               # how far back to look for a run
EXPERIMENT_LOOKBACK_DAYS = 30
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
PLAYGROUND_DEFAULT = "sql-clickhouse.clickhouse.com"
SEED_CMD = "python demos/support-triage-parallel/scripts/seed_all.py"
RUN_CMD = "docker compose --profile demo run --rm support-triage-parallel python main.py"
TIE_CMD = RUN_CMD + " --ticket TCK-002"
MAX_PAGES = 20
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
    env.update(parse_env(path.read_text(encoding="utf-8")))
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
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        try:  # building the request is inside the boundary: a malformed URL raises ValueError
            query = {k: (str(v).lower() if isinstance(v, bool) else v)
                     for k, v in (params or {}).items() if v is not None}
            url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or e.reason) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        except ValueError as e:  # urlsplit, Request, or http.client.InvalidURL
            raise ApiError(None, f"invalid request URL {self.base}{path}: {e}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def cursor(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = MAX_PAGES) -> List[dict]:
        """Follow `meta.cursor` (Observations v2, Scores v3, experiments) to the end, or to
        `cap` rows. A page may be empty while a cursor is still returned, so stop on a missing
        cursor, not on an empty page. A cursor that repeats, or more pages than `max_pages`,
        is an error, not a silently short result."""
        out: List[dict] = []
        cur, seen = None, set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=100, **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:
                raise ApiError(None, f"{path} returned the same cursor twice")
            seen.add(cur)
        raise ApiError(None, f"{path} did not finish within {max_pages} pages")

    def pages(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = MAX_PAGES) -> List[dict]:
        """Follow page numbers (v1 observations, datasets, dataset items, dataset runs) to the
        last page, or to `cap` rows. More pages than `max_pages` is an error."""
        out: List[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params, limit=100, page=page)) or {}
            out += d.get("data") or []
            if page >= ((d.get("meta") or {}).get("totalPages") or 1) or len(out) >= cap:
                return out[:cap]
        raise ApiError(None, f"{path} did not finish within {max_pages} pages")


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


def strategy_of_run(name: str) -> Optional[str]:
    """The strategy of a `run_experiment.py` comparison run, or None for anything else.

    Run names are `aggregator-<strategy>`, optionally `-<label>`; the N sweep's
    `aggregator-<strategy>-n<N>` runs are not the 5-sample comparison Act 6 shows.
    """
    for strategy in STRATEGIES:
        stem = f"aggregator-{strategy}"
        if name == stem or (name.startswith(stem + "-") and not re.match(r"n\d+(-|$)", name[len(stem) + 1:])):
            return strategy
    return None


def expected_items() -> Dict[str, Dict[str, Tuple[Any, Any]]]:
    """Dataset -> item id -> (input, expected output to match) from seed_datasets.py. For
    sql-voting only canonical_sql is fixed; the signature is pinned at seed time."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_datasets  # noqa: E402  (stdlib at import; the playground is used only when seeding)
    return {
        seed_datasets.CATEGORY_DATASET: {i: (inp, out) for i, inp, out in seed_datasets.category_items()},
        seed_datasets.SQL_VOTING_DATASET: {i: (inp, {"canonical_sql": sql})
                                           for i, inp, sql in seed_datasets.sql_voting_items()},
    }


def item_problem(row: Optional[dict], want_input: Any, want_output: Dict[str, Any], pinned: bool) -> str:
    """Why one dataset item is not a usable ACTIVE example, or "" when it is."""
    if row is None:
        return "missing"
    if str(row.get("status") or "ACTIVE").upper() != "ACTIVE":
        return str(row.get("status")).lower()
    got = row.get("expectedOutput") or {}
    if row.get("input") != want_input or any(got.get(k) != v for k, v in want_output.items()):
        return "content differs from seed_datasets.py"
    if pinned and not got.get("result_signature"):
        return "no pinned result signature"
    return ""


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

def base_url_problem(url: str) -> Optional[str]:
    """Why `url` cannot be the Langfuse base URL, or None. Checked before any request, so a
    typo in the env file is a FAIL with a reason instead of a traceback: urlsplit raises
    ValueError on a bad IPv6 literal (http://[::gg]) or port, on some Pythons only later."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018  (raises ValueError on a non-numeric or out-of-range port)
        if host and ":" in host:
            ipaddress.IPv6Address(host.split("%")[0])
    except ValueError as e:
        return f"{type(e).__name__}: {e}"
    if parts.scheme not in ("http", "https"):
        return f"scheme must be http or https, got '{parts.scheme or 'none'}'"
    if not host:
        return "no host"
    return None


def playground_check(host: str) -> None:
    """The voter validates and executes every SQL candidate against this playground."""
    print("Services")
    url = f"https://{host}/ping"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            check(r.status < 500, f"ClickHouse playground answers at {url} ({r.status})")
    except urllib.error.HTTPError as e:
        check(e.code < 500, f"ClickHouse playground answers at {url} ({e.code})")
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        check(False, f"ClickHouse playground not reachable at {url}: {type(e).__name__}",
              hint="every candidate fails closed without it: check PUBLIC_CH_HOST and outbound HTTPS")


def expected_prompts() -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """The production (text, config) scripts/seed_prompts.py seeds per prompt (stdlib-only)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_prompts  # noqa: E402  (same folder; reads env only for its own HOST/keys)
    defs = {name: (text, config) for name, text, config, _msg in seed_prompts.SINGLE_VERSION}
    defs[VERSIONED_PROMPT] = (seed_prompts.SQL_VOTER_V2, seed_prompts.VOTER_CONFIG)
    return defs


def expected_prompt_texts() -> Dict[str, str]:
    return {name: text for name, (text, _config) in expected_prompts().items()}


def seeded_checks(lf: Langfuse, state: Dict[str, Any]) -> None:
    print("Seeded artifacts")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed_prompts  # noqa: E402
    defs = expected_prompts()
    for name in SEED_PROMPTS:
        try:
            p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=PROMPT_LABEL) or {}
        except ApiError as e:
            check(False, f"prompt {name} with label {PROMPT_LABEL}: {e}", hint=f"run {SEED_CMD}")
            continue
        check(True, f"prompt {name}: {PROMPT_LABEL} = v{p.get('version')}")
        text, config = defs[name]
        if not seed_prompts.matches(p, text, config):
            check(False, f"prompt {name}: {PROMPT_LABEL} type, text or config differs from scripts/seed_prompts.py",
                  required=False, hint=f"a rehearsal edit? {SEED_CMD} puts it back")
        if name == VERSIONED_PROMPT:
            check((p.get("version") or 0) >= 2, f"prompt {name} has version history "
                  f"({PROMPT_LABEL} is v{p.get('version')}, want v2+)", required=False,
                  hint="seed_prompts.py seeds v1 then v2 only on a fresh project")

    datasets = {d.get("name"): d for d in lf.pages("/api/public/v2/datasets", {})}
    wanted = expected_items()
    for name, want in EXPECTED_DATASETS.items():
        if not check(name in datasets, f"dataset {name} exists", hint=f"run {SEED_CMD}"):
            continue
        if name == EXPERIMENT_DATASET:
            state["experiment_dataset_id"] = datasets[name].get("id")
        rows = {r.get("id"): r for r in lf.pages("/api/public/dataset-items", {"datasetName": name}, cap=10000)}
        problems = {}
        for item_id, (inp, out) in wanted[name].items():
            why = item_problem(rows.get(item_id), inp, out, pinned=name == EXPERIMENT_DATASET)
            if why:
                problems[item_id] = why
        usable = len(wanted[name]) - len(problems)
        detail = "; ".join(f"{i} {w}" for i, w in sorted(problems.items())[:4]) + (" ..." if len(problems) > 4 else "")
        check(not problems and usable >= want,
              f"dataset {name}: {usable}/{want} expected items ACTIVE and usable" + (f" ({detail})" if detail else ""),
              hint=f"run {SEED_CMD}; it re-activates archived items and re-pins signatures")


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
    age_h = (now - started).total_seconds() / 3600
    check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
          required=False, hint=f"run {RUN_CMD} the day before")

    state["demo_traces"] = demo_traces = {r["traceId"] for r in roots}
    tie_checks(lf, obs, state, demo_traces, since, until)


def is_split_vote(scores: List[dict]) -> bool:
    """A trace's consensus_confidence shows a split vote under result-signature voting: below
    1, with the strategy tally_votes writes into the comment (sql_voting.py:251-253). The
    rows must come from a Scores v3 read with `fields=details`, which carries `comment`."""
    for s in scores:
        if s.get("name") != "consensus_confidence":
            continue
        try:
            value = float(s.get("value"))
        except (TypeError, ValueError):
            return False
        return value < 1 and (s.get("comment") or "").endswith(f"strategy={TIE_STRATEGY}")
    return False


def tie_checks(lf: Langfuse, obs: "Observations", state: Dict[str, Any], demo_traces: set,
               since: datetime, until: datetime) -> None:
    """Act 3 (in the short path too): a demo run whose vote split, so the tie-break judge
    fired. Experiment traces also have tie-break judges, so only judges under a
    triage-support-ticket root count, and the run's consensus must show the split."""
    ties = [t for t in obs.list(since, until, name=TIE_OBSERVATION)
            if t.get("traceId") in demo_traces and t.get("startTime")]
    ties.sort(key=lambda t: parse_time(t["startTime"]), reverse=True)
    for t in ties[:10]:
        # `comment` is in the Scores v3 `details` group: without it the strategy is unreadable.
        scores = lf.cursor("/api/public/v3/scores",
                           {"traceId": t["traceId"], "name": "consensus_confidence", "fields": "details"})
        if is_split_vote(scores):
            state["tie_trace_id"] = t["traceId"]
            break
    found = bool(state.get("tie_trace_id"))
    check(found, f"Act 3 tie trace: a demo run where {TIE_OBSERVATION} broke a split {TIE_STRATEGY} vote "
          f"in the last {LOOKBACK_DAYS} days" + (f" ({len(ties)} judge run(s), none a split)" if ties and not found else ""),
          hint=f"run {TIE_CMD} until the step log shows tie_break=True")


def evaluator_checks(lf: Langfuse, state: Dict[str, Any], required: bool) -> None:
    """The two evaluators the Close and the objections cite, on any recent demo run."""
    print("Evaluators")
    since = iso(datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS))
    demo_traces = state.get("demo_traces") or set()
    for name in EVALUATOR_SCORES:
        rows = lf.cursor("/api/public/v3/scores", {"name": name, "fromTimestamp": since, "fields": "subject"})
        hits = [r for r in rows if (r.get("subject") or {}).get("traceId") in demo_traces]
        check(bool(hits), f"evaluator score {name} on {len(hits)} demo run(s) in the last {LOOKBACK_DAYS} days",
              required=required, hint=f"both score NEW traces only: {EVALUATOR_CMD}, then run again")


def experiment_runs(lf: Langfuse, dataset_id: str) -> Tuple[List[str], str]:
    """Run names on the sql-voting dataset, and which read answered. The experiments read
    first; the dataset-runs read only when it 404s (the pinned v3 server has no
    /experiments)."""
    since = datetime.now(timezone.utc) - timedelta(days=EXPERIMENT_LOOKBACK_DAYS)
    try:
        rows = lf.cursor("/api/public/experiments", {"datasetId": dataset_id, "fromStartTime": iso(since)})
        return [r["name"] for r in rows if isinstance(r.get("name"), str)], "experiments"
    except ApiError as e:
        if e.status != 404:
            raise
    rows = lf.pages(f"/api/public/datasets/{urllib.parse.quote(EXPERIMENT_DATASET, safe='')}/runs", {})
    return [r["name"] for r in rows if isinstance(r.get("name"), str)], "dataset runs"


def experiment_checks(lf: Langfuse, state: Dict[str, Any], required: bool) -> None:
    """Act 6: one comparison run per aggregation strategy on the sql-voting dataset."""
    print("Act 6 · experiment")
    if not state.get("experiment_dataset_id"):
        check(False, f"aggregator runs: dataset {EXPERIMENT_DATASET} is not seeded", required=required,
              hint=f"run {SEED_CMD}, then {EXPERIMENT_CMD}")
        return
    names, source = experiment_runs(lf, state["experiment_dataset_id"])
    have = {strategy_of_run(n) for n in names} - {None}
    missing = [s for s in STRATEGIES if s not in have]
    check(not missing, f"aggregator runs on {EXPERIMENT_DATASET} ({source} read): "
          f"{len(STRATEGIES) - len(missing)}/{len(STRATEGIES)} strategies"
          + (f"; missing {', '.join('aggregator-' + m for m in missing)}" if missing else ""),
          required=required, hint=f"{EXPERIMENT_CMD}  (calls models: 3 strategies x 10 items x 5 samples)")


def langfuse_checks(lf: Langfuse, expected_project: Optional[str], short: bool = False) -> Dict[str, Any]:
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
        evaluator_checks(lf, state, required=not short)
        experiment_checks(lf, state, required=not short)
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}")
    return state


def run_checks(short: bool, links: bool) -> None:
    """Config first (no request until the env file, keys and URL are valid), then the reads.
    A read failure is a FAIL inside langfuse_checks; anything else reaches main()."""
    print("Config")
    manifest_path = DEMO_DIR / "demo.yaml"
    try:
        manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    except (OSError, UnicodeDecodeError) as e:
        check(False, f"cannot read {manifest_path.name}: {type(e).__name__}: {e}", hint="restore it from git")
        return
    path = env_file(manifest_value(manifest, "", "profile"))
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    try:
        loaded = load_env(path)          # shell LANGFUSE_* are dropped before the file is read
    except (OSError, UnicodeDecodeError) as e:
        check(False, f"cannot read {shown}: {type(e).__name__}: {e}",
              hint="fix the file's permissions or encoding (UTF-8 text)")
        return
    check(loaded, f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="run ./setup.sh at the repo root")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    check(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set (every branch, sample and judge)",
          hint="add it to the root .env, or ANTHROPIC_API_KEY=sk-... ./setup.sh")
    if not check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set"):
        return
    problem = base_url_problem(base)
    if not check(problem is None, "LANGFUSE_BASE_URL is an http(s) URL" + (f": {base!r} ({problem})" if problem else ""),
                 hint="fix it in the env file, e.g. LANGFUSE_BASE_URL=http://localhost:3001"):
        return
    playground_check(os.environ.get("PUBLIC_CH_HOST") or PLAYGROUND_DEFAULT)
    lf = Langfuse(base, pk, sk)
    state = langfuse_checks(lf, expected, short=short)
    if links and state.get("project_id"):
        print("\nLinks")
        for label, url in act_links(lf.base, state):
            print(f"  {label:<28} {url}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    ap.add_argument("--short", action="store_true",
                    help="readiness for the short path (Opening, Acts 1-3, Close): Act 6 and the evaluators only warn")
    args = ap.parse_args(argv)
    del RESULTS[:]

    print("support-triage-parallel readiness: " + ("short path (Acts 1-3)" if args.short else "full script (Acts 1-7)"))
    try:
        run_checks(args.short, args.links)
    except Exception as e:  # noqa: BLE001  (any failure must still reach the counted verdict)
        check(False, f"verification stopped: {type(e).__name__}: {e}",
              hint="the results above stand; this is an unexpected response or a bug in this check")

    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
