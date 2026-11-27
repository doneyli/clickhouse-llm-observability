#!/usr/bin/env python3
"""Seed the demo's Langfuse project: score configs, prompts, one dashboard.

    .venv/bin/python scripts/seed_langfuse.py             # create / reconcile everything
    .venv/bin/python scripts/seed_langfuse.py --dry-run   # validate every widget query, change nothing
    .venv/bin/python scripts/seed_langfuse.py --only scores prompts
    .venv/bin/python scripts/seed_langfuse.py --delete-dashboard

Idempotent, so re-running is a no-op:

- **Score configs** are matched by name. Missing ones are created; a drifted description or
  range is PATCHed. Configs cannot be deleted, so nothing is ever created twice.
- **Prompts** (``explorer.prompts.PROMPTS``) get a new version, labelled ``production``,
  only when the text differs from the prompt's **production** version. Comparing with
  `latest` would be wrong: a candidate label (e.g. `candidate-coarse`, see
  `scripts/seed_candidate_prompt.py`) is usually the newest version.
- **Dashboard** "Analytics at Agentic Speed" is matched by name; widgets by name
  (PATCHed when their definition drifted); the layout is rebuilt only when it differs.
  Every widget query is run against the Metrics API v2 first, and a widget the API rejects
  is reported and skipped, never aborting the rest.

What a widget cannot show (checked live 2026-11-26): ``experimentName`` is not a widget
dimension, and no view groups by metadata. So Experiment A is split by **trace version**:
the experiment runner sets ``version="breadth-<max_scenarios_per_round>"`` with
``propagate_attributes``. Concurrency levels are split by **session id**
(``scripts/run_concurrency.py`` gives each level its own session).

Keys come from the demo's own ``.env`` (``explorer.config.load_env`` overrides the shell),
and the script refuses to write unless they map to the ``analytics-agentic-speed`` project.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote

DEMO_DIR = Path(__file__).resolve().parent.parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

EXPECTED_PROJECT = "analytics-agentic-speed"
U = "/api/public/unstable"

# ---------------------------------------------------------------- score configs
# The fixed contract with explorer/agent.py and the experiment evaluators. The agent does
# not pass a configId, so these describe the scores in the UI; they do not gate ingestion.

STOP_REASONS = ["answered", "max_rounds", "budget", "error"]


def _num(name: str, description: str, *, lo: Optional[float] = 0, hi: Optional[float] = None) -> dict:
    cfg: dict[str, Any] = {"name": name, "dataType": "NUMERIC", "description": description}
    if lo is not None:
        cfg["minValue"] = lo
    if hi is not None:
        cfg["maxValue"] = hi
    return cfg


SCORE_CONFIGS: list[dict[str, Any]] = [
    # observation level, on every clickhouse.query tool span
    _num("db_server_ms", "ClickHouse's own time for one query (statistics.elapsed), in ms. The gap "
         "between this and the span's duration is network, not database."),
    # round level, on each `round` chain
    _num("round_scenarios", "Scenarios this round swept: the grid size after the per-round cap."),
    _num("round_db_server_ms_total", "Sum of db_server_ms over this round's queries: the total "
         "database work, in ms."),
    _num("round_db_p95_ms", "p95 of db_server_ms within this round, in ms."),
    _num("round_sweep_wall_ms", "Wall-clock time of this round's sweep, in ms. Far below "
         "round_db_server_ms_total means the queries ran concurrently."),
    # trace level
    _num("scenarios_evaluated", "Scenarios the agent tested in the whole run, across all rounds."),
    _num("db_server_ms_total", "Sum of ClickHouse server time over every query in the run, in ms."),
    _num("db_p95_ms", "p95 of db_server_ms across every query in the run, in ms."),
    _num("db_wait_ms_total", "Wall-clock time the run spent waiting on ClickHouse (profile, lookups, "
         "sweeps, verify), in ms. Concurrent queries overlap, so this is below db_server_ms_total."),
    _num("db_time_share", "Share of the run's wall time spent waiting on ClickHouse "
         "(db_wait_ms_total / run wall time). 0.05 = 5%. The punchline: the model, not the "
         "database, sets the run's latency.", hi=1),
    _num("scenarios_per_minute", "Throughput: scenarios evaluated per minute of run wall time."),
    _num("llm_calls", "Model calls in the run. Stays at 3-6 however wide the sweep gets."),
    _num("rounds", "Sweep rounds (compile, sweep, analyse) before the agent stopped."),
    _num("run_cost_usd", "LLM spend for the run, in USD. Database time is not in this number."),
    {"name": "stop_reason", "dataType": "CATEGORICAL",
     "description": "Why the run ended: answered, a cap (max_rounds, budget), or error. A run that "
                    "hits a cap says so here instead of hiding it.",
     "categories": [{"label": s, "value": i} for i, s in enumerate(STOP_REASONS)]},
    {"name": "recommendation_grounded", "dataType": "BOOLEAN",
     "description": "Deterministic metric-replay check: the named scenario, the reported metric and a "
                    "repeat query agree within six-significant-figure rounding. This does not assess "
                    "answer correctness, global optimality, or free-form prose."},
    # experiment item evaluators
    {"name": "answer_correct", "dataType": "BOOLEAN",
     "description": "Experiment evaluator: the answer matches the reference SQL's result on every "
                    "answer key."},
    _num("answer_key_match", "Experiment evaluator: fraction of answer keys that match the reference "
         "SQL's result, 0-1.", hi=1),
]

SCORE_TYPES: dict[str, str] = {c["name"]: c["dataType"] for c in SCORE_CONFIGS}
_DRIFT_KEYS = ("description", "minValue", "maxValue", "categories")


def plan_score_configs(existing: Iterable[dict]) -> tuple[list[dict], list[tuple[str, dict]], list[str]]:
    """(to_create, to_patch [(id, body)], conflicts) for the wanted configs vs what exists.

    A name that exists with a different data type is a conflict: it cannot be changed or
    deleted, so it is reported, never duplicated.
    """
    by_name = {c["name"]: c for c in existing if not c.get("isArchived")}
    create, patch, conflicts = [], [], []
    for want in SCORE_CONFIGS:
        have = by_name.get(want["name"])
        if have is None:
            create.append(want)
            continue
        if have.get("dataType") != want["dataType"]:
            conflicts.append(f"{want['name']}: exists as {have.get('dataType')}, contract says {want['dataType']}")
            continue
        body = {k: want[k] for k in _DRIFT_KEYS if k in want and _norm(have.get(k)) != _norm(want[k])}
        if body:
            patch.append((have["id"], body))
    return create, patch, conflicts


def _norm(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


# ---------------------------------------------------------------- prompts

def plan_prompts(wanted: dict[str, Any], current: Callable[[str], Optional[dict]]) -> list[tuple[str, str, Any]]:
    """[(name, action, detail)] — action is 'create' (detail = text) or 'unchanged' (detail = version).

    ``current(name)`` returns the version labelled production (or None when there is none).
    """
    out = []
    for name, text in wanted.items():
        cur = current(name)
        if cur is not None and cur.get("prompt") == text:
            out.append((name, "unchanged", cur.get("version")))
        else:
            out.append((name, "create", text))
    return out


def load_prompts() -> Optional[dict[str, Any]]:
    """``explorer.prompts.PROMPTS``, imported lazily; None when the module isn't there yet."""
    try:
        from explorer.prompts import PROMPTS  # noqa: PLC0415
    except ModuleNotFoundError as e:
        if e.name in ("explorer.prompts", "explorer"):
            return None
        raise
    return dict(PROMPTS)


# ---------------------------------------------------------------- dashboard

DASHBOARD = {
    "name": "Analytics at Agentic Speed",
    "description": (
        "The LLM writes the plan once; ClickHouse runs it hundreds of times. Database time per "
        "query, the database's share of each run, scenarios per run against LLM calls per run, "
        "the concurrency act, and Experiment A (accuracy by sweep breadth)."
    ),
}

VIEWS = {"observations", "scores-numeric", "scores-boolean", "scores-categorical"}
CHART_TYPES = {"LINE_TIME_SERIES", "AREA_TIME_SERIES", "BAR_TIME_SERIES", "HORIZONTAL_BAR",
               "VERTICAL_BAR", "PIE", "NUMBER", "HISTOGRAM", "PIVOT_TABLE"}
AGGS = {"sum", "avg", "count", "max", "min", "p50", "p75", "p90", "p95", "p99", "histogram", "uniq"}
# The Metrics API refuses these as dimensions without row_limit + a desc orderBy.
HIGH_CARDINALITY = {"traceId", "sessionId", "userId", "id", "observationId", "experimentName",
                    "experimentId"}
# Accepted by the Metrics API, rejected by the widget builder (re-checked 2026-11-26).
NOT_WIDGET_DIMENSIONS = {"experimentName", "experimentId", "datasetRunId", "experimentDatasetId"}

LLM_OBS = ["plan", "analyse"]  # the agent's own generations; their anthropic.* children are not counted
DB_OBS = ["profile", "lookups", "sweep", "verify"]  # chains whose wall time is waiting on ClickHouse
ROOT = "analytics-agentic-speed"
LEGACY_ROOT = "scenario-explorer"  # the demo's name until 2026-11-26; its traces still count as runs
OBSERVATION_NAMES = {ROOT, LEGACY_ROOT, "profile", "lookups", "plan", "round", "compile", "sweep",
                     "clickhouse.query", "analyse", "verify"}


def score_is(*names: str) -> dict:
    return {"column": "name", "operator": "any of", "value": list(names), "type": "stringOptions"}


def obs_named(*names: str) -> dict:
    return {"column": "name", "operator": "any of", "value": list(names), "type": "stringOptions"}


CONCURRENCY_RUNS = {"column": "tags", "operator": "any of", "value": ["concurrency"], "type": "arrayOptions"}
# Experiment A by trace version, not environment: the evaluator scores (answer_correct,
# answer_key_match) land in environment "default", so an environment filter drops them.
EXPERIMENT_A_RUNS = {"column": "traceVersion", "operator": "any of", "value": ["breadth-10", "breadth-300"],
                     "type": "stringOptions"}

COUNT = [{"measure": "count", "agg": "count"}]
AVG = [{"measure": "value", "agg": "avg"}]


def _w(name: str, description: str, view: str, chart: str, metrics: list[dict], filters: list[dict],
       w: int, h: int, dimensions: Optional[list[dict]] = None, **chart_config: Any) -> dict:
    return {"name": name, "description": description, "view": view, "chartType": chart,
            "dimensions": dimensions or [], "metrics": metrics, "filters": filters,
            "chartConfig": {"type": chart, **chart_config}, "w": w, "h": h}


WIDGETS: list[dict[str, Any]] = [
    # -- row: the headline numbers
    _w("Runs", "Agent runs in the window: one agent root observation each.",
       "observations", "NUMBER", COUNT, [obs_named(ROOT, LEGACY_ROOT)], 3, 4),
    _w("DB share of run wall time", "Average db_time_share: the fraction of a run spent waiting on "
       "ClickHouse. 0.05 = 5%. The rest of the run is the model thinking.",
       "scores-numeric", "NUMBER", AVG, [score_is("db_time_share")], 3, 4),
    _w("Scenarios per run", "Average scenarios_evaluated: what-ifs one question gets tested against.",
       "scores-numeric", "NUMBER", AVG, [score_is("scenarios_evaluated")], 3, 4),
    _w("Scenarios per minute", "Average scenarios_per_minute: run throughput, wall clock.",
       "scores-numeric", "NUMBER", AVG, [score_is("scenarios_per_minute")], 3, 4),
    # -- row: db_server_ms distribution
    _w("ClickHouse time per query, p50 (ms)", "Median db_server_ms: ClickHouse's own elapsed time "
       "for one scenario query.", "scores-numeric", "NUMBER",
       [{"measure": "value", "agg": "p50"}], [score_is("db_server_ms")], 3, 4),
    _w("ClickHouse time per query, p95 (ms)", "p95 of db_server_ms across every query in the window.",
       "scores-numeric", "NUMBER", [{"measure": "value", "agg": "p95"}], [score_is("db_server_ms")], 3, 4),
    _w("ClickHouse time per query, p99 (ms)", "p99 of db_server_ms: the slow tail, cold reads included.",
       "scores-numeric", "NUMBER", [{"measure": "value", "agg": "p99"}], [score_is("db_server_ms")], 3, 4),
    _w("Queries run", "One db_server_ms score per clickhouse.query span: queries ClickHouse answered.",
       "scores-numeric", "NUMBER", COUNT, [score_is("db_server_ms")], 3, 4),
    # -- row: what the model costs
    _w("LLM calls per run", "Average llm_calls. Compare with scenarios per run: the sweep gets wider, "
       "this number does not.", "scores-numeric", "NUMBER", AVG, [score_is("llm_calls")], 4, 4),
    _w("LLM cost per run (USD)", "Average run_cost_usd: model spend per question.",
       "scores-numeric", "NUMBER", AVG, [score_is("run_cost_usd")], 4, 4),
    _w("Recommendation grounded", "Share of runs whose recommended number reproduces when its SQL is "
       "re-run (1.0 = every run).", "scores-boolean", "NUMBER", AVG,
       [score_is("recommendation_grounded")], 4, 4),
    # -- DB time vs LLM time
    _w("ClickHouse time per query — distribution", "Histogram of db_server_ms over every query. The "
       "spike is warm reads; the tail is cold ones.", "scores-numeric", "HISTOGRAM",
       [{"measure": "value", "agg": "histogram"}], [score_is("db_server_ms")], 6, 6, bins=20),
    _w("Run wall time — LLM vs ClickHouse, per run", "Per run (trace id), summed wall time in ms. "
       "GENERATION = the model (plan, analyse). CHAIN = waiting on ClickHouse (profile, lookups, "
       "sweeps, verify), with concurrent queries counted once.", "observations", "PIVOT_TABLE",
       [{"measure": "latency", "agg": "sum"}], [obs_named(*LLM_OBS, *DB_OBS)], 6, 6,
       dimensions=[{"field": "traceId"}, {"field": "type"}], row_limit=20),
    _w("LLM vs ClickHouse wall time over time", "Summed wall time (ms) by kind. GENERATION = the model; "
       "CHAIN = waiting on ClickHouse. The CHAIN slice stays thin however many scenarios run.",
       "observations", "BAR_TIME_SERIES", [{"measure": "latency", "agg": "sum"}],
       [obs_named(*LLM_OBS, *DB_OBS)], 6, 6, dimensions=[{"field": "type"}]),
    # -- scenarios vs LLM calls
    _w("LLM calls vs scenarios per run", "Average scenarios_evaluated and llm_calls per run over time: "
       "the rising line is ClickHouse's work, the flat one the model's.", "scores-numeric",
       "LINE_TIME_SERIES", AVG, [score_is("scenarios_evaluated", "llm_calls")], 6, 6,
       dimensions=[{"field": "name"}]),
    _w("Sweep breadth vs LLM calls — range across runs", "Min, average and max per run. Scenarios vary "
       "by orders of magnitude; LLM calls and rounds barely move.", "scores-numeric", "PIVOT_TABLE",
       [{"measure": "value", "agg": "min"}, {"measure": "value", "agg": "avg"},
        {"measure": "value", "agg": "max"}, {"measure": "count", "agg": "count"}],
       [score_is("scenarios_evaluated", "llm_calls", "rounds")], 6, 6, dimensions=[{"field": "name"}]),
    _w("How runs ended", "stop_reason per run: answered, or a cap (max_rounds, budget), or error. "
       "Runs that hit a cap are counted here, not hidden.", "scores-categorical", "PIE", COUNT,
       [score_is("stop_reason")], 6, 6, dimensions=[{"field": "stringValue"}]),
    # -- concurrency act (SPEC §7)
    _w("Concurrency — ClickHouse time per query by level", "One row per concurrency level "
       "(session concurrency-agents-<N>-<run>): p50/p95/p99 of db_server_ms and the query count. "
       "Flat p95 while the count climbs is the many-agents-one-database story.",
       "scores-numeric", "PIVOT_TABLE",
       [{"measure": "value", "agg": "p50"}, {"measure": "value", "agg": "p95"},
        {"measure": "value", "agg": "p99"}, {"measure": "count", "agg": "count"}],
       [score_is("db_server_ms"), CONCURRENCY_RUNS], 6, 6,
       dimensions=[{"field": "sessionId"}], row_limit=50),
    # -- Experiment A (SPEC §6): split by trace version breadth-<N>
    _w("Experiment A — accuracy by sweep breadth", "Share of dataset items answered correctly "
       "(answer_correct) per trace version, which the experiment runner sets to "
       "breadth-<max_scenarios_per_round>. Item-level detail: the Experiments compare view.",
       "scores-boolean", "HORIZONTAL_BAR", AVG, [score_is("answer_correct"), EXPERIMENT_A_RUNS], 6, 6,
       dimensions=[{"field": "traceVersion"}]),
    _w("Experiment A — what breadth costs", "Average per run, by trace version (breadth-<N>): key "
       "match, scenarios, LLM calls, DB server time and LLM cost. Breadth is a knob you can afford to "
       "turn up.", "scores-numeric", "PIVOT_TABLE", AVG,
       [score_is("answer_key_match", "scenarios_evaluated", "llm_calls", "db_server_ms_total",
                 "run_cost_usd"), EXPERIMENT_A_RUNS], 6, 6,
       dimensions=[{"field": "traceVersion"}, {"field": "name"}]),
]

WIDGET_FIELDS = ("description", "view", "chartType", "dimensions", "metrics", "filters", "chartConfig")


def widget_body(w: dict) -> dict:
    """The POST/PATCH body for /dashboard-widgets (layout keys stripped)."""
    return {"name": w["name"], **{k: w[k] for k in WIDGET_FIELDS}}


def is_time_series(chart: str) -> bool:
    return chart.endswith("_TIME_SERIES")


def metrics_query(w: dict, now: Optional[datetime] = None, days: int = 30) -> dict:
    """The Metrics API v2 query equivalent to a widget, used to validate it before publishing."""
    now = now or datetime.now(timezone.utc)
    q: dict[str, Any] = {
        "view": w["view"],
        "metrics": [{"measure": m["measure"], "aggregation": m["agg"]} for m in w["metrics"]],
        "dimensions": w["dimensions"],
        "filters": w["filters"],
        "fromTimestamp": _iso(now - timedelta(days=days)),
        "toTimestamp": _iso(now),
    }
    config: dict[str, Any] = {}
    if any(d["field"] in HIGH_CARDINALITY for d in w["dimensions"]):
        m0 = w["metrics"][0]
        q["orderBy"] = [{"field": f"{m0['agg']}_{m0['measure']}", "direction": "desc"}]
        config["row_limit"] = w["chartConfig"].get("row_limit", 100)
    if w["chartType"] == "HISTOGRAM":
        config["bins"] = w["chartConfig"].get("bins", 10)
    if config:
        q["config"] = config
    if is_time_series(w["chartType"]):
        q["timeDimension"] = {"granularity": "day"}
    return q


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def layout(widgets: list[dict], cols: int = 12) -> list[tuple[str, int, int, int, int]]:
    """Flow the widgets left to right into a ``cols``-wide grid: [(name, x, y, width, height)]."""
    out, x, y, row_h = [], 0, 0, 0
    for w in widgets:
        if x + w["w"] > cols:
            x, y, row_h = 0, y + row_h, 0
        out.append((w["name"], x, y, w["w"], w["h"]))
        x += w["w"]
        row_h = max(row_h, w["h"])
    return out


def subset(want: Any, have: Any) -> bool:
    """True when ``have`` holds everything in ``want`` (the server may add defaults)."""
    if isinstance(want, dict):
        return isinstance(have, dict) and all(k in have and subset(v, have[k]) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(have, list) and len(want) == len(have) and all(subset(a, b) for a, b in zip(want, have))
    return _norm(want) == _norm(have)


def widget_drifted(w: dict, stored: dict) -> bool:
    body = widget_body(w)
    return not all(subset(body[k], stored.get(k)) for k in WIDGET_FIELDS)


def placement_tuple(p: dict) -> tuple[str, int, int, int, int]:
    return (p.get("widgetId"), p.get("x"), p.get("y"), p.get("width", p.get("x_size")),
            p.get("height", p.get("y_size")))


# ---------------------------------------------------------------- REST client

class LangfuseAPIError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, text: str):
        super().__init__(f"{method} {path} -> {status}: {text[:500]}")
        self.status = status


class API:
    def __init__(self, base_url: str, public_key: str, secret_key: str, timeout: float = 60.0):
        import httpx  # noqa: PLC0415

        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, auth=(public_key, secret_key), timeout=timeout)

    @classmethod
    def from_env(cls) -> "API":
        import os  # noqa: PLC0415

        from explorer.config import load_env  # noqa: PLC0415

        load_env()
        missing = [k for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.environ.get(k)]
        if missing:
            sys.exit(f"missing from {DEMO_DIR / '.env'}: {', '.join(missing)}")
        base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or "https://us.cloud.langfuse.com"
        return cls(base, os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])

    def __call__(self, method: str, path: str, body: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict:
        r = self._http.request(method, path, json=body, params=params)
        if r.status_code >= 400:
            raise LangfuseAPIError(method, path, r.status_code, r.text)
        return r.json() if r.content else {}

    def paginate(self, path: str, params: Optional[dict] = None) -> list[dict]:
        """Page-numbered lists (score configs, dashboards, widgets), read to the end."""
        out, page = [], 1
        while True:
            d = self("GET", path, params={"limit": 100, "page": page, **(params or {})})
            items = d.get("data") or []
            out += items
            if not items or page >= ((d.get("meta") or {}).get("totalPages") or 1):
                return out
            page += 1

    def metrics(self, query: dict) -> list[dict]:
        return self("GET", "/api/public/v2/metrics", params={"query": json.dumps(query)}).get("data") or []


def verify_project(api: API, expected: str) -> dict:
    projects = api("GET", "/api/public/projects").get("data") or []
    if not projects:
        sys.exit("no project resolves for these keys")
    p = projects[0]
    print(f"project: {p['name']} ({p['id']}) on {api.base_url}")
    if p["name"] != expected:
        sys.exit(f"refusing to write: keys map to {p['name']!r}, expected {expected!r} "
                 f"(pass --project-name to override)")
    return p


# ---------------------------------------------------------------- seeding steps

def seed_score_configs(api: API, dry_run: bool) -> None:
    print("\n== score configs")
    existing = api.paginate("/api/public/score-configs")
    create, patch, conflicts = plan_score_configs(existing)
    have = {c["name"] for c in existing}
    for c in SCORE_CONFIGS:
        if c["name"] in have and all(c is not x for x in create):
            print(f"  = {c['name']} ({c['dataType']})")
    for c in create:
        if not dry_run:
            api("POST", "/api/public/score-configs", c)
        print(f"  {'would create' if dry_run else '+'} {c['name']} ({c['dataType']})")
    for cid, body in patch:
        if not dry_run:
            api("PATCH", f"/api/public/score-configs/{cid}", body)
        print(f"  {'would update' if dry_run else '~'} {cid}: {', '.join(body)}")
    for msg in conflicts:
        print(f"  ! conflict (left as is): {msg}")
    if not dry_run:
        landed = {c["name"]: c["dataType"] for c in api.paginate("/api/public/score-configs")}
        ok = sum(landed.get(n) == t for n, t in SCORE_TYPES.items())
        print(f"  read back: {ok}/{len(SCORE_TYPES)} contract configs present with the right type")


def seed_prompts(api: API, dry_run: bool) -> None:
    print("\n== prompts")
    wanted = load_prompts()
    if wanted is None:
        print("  skipped: explorer/prompts.py does not exist yet (re-run once it does)")
        return

    def production(name: str) -> Optional[dict]:
        try:
            return api("GET", f"/api/public/v2/prompts/{quote(name, safe='')}", params={"label": "production"})
        except LangfuseAPIError as e:
            if e.status == 404:
                return None
            raise

    for name, action, detail in plan_prompts(wanted, production):
        if action == "unchanged":
            print(f"  = {name} (production v{detail} matches)")
            continue
        if dry_run:
            print(f"  would create {name} -> production")
            continue
        body = {"name": name, "type": "text" if isinstance(detail, str) else "chat", "prompt": detail,
                "labels": ["production"], "tags": ["analytics-agentic-speed"],
                "commitMessage": "seed_langfuse.py"}
        created = api("POST", "/api/public/v2/prompts", body)
        print(f"  + {name} v{created.get('version')} -> production")
    if not dry_run:
        for name in wanted:
            p = api("GET", f"/api/public/v2/prompts/{quote(name, safe='')}", params={"label": "production"})
            print(f"  read back: {name} production = v{p.get('version')}, labels {p.get('labels')}")


def seed_dashboard(api: API, dry_run: bool) -> Optional[str]:
    print(f"\n== dashboard: {DASHBOARD['name']}")
    existing_widgets = {w["name"]: w for w in api.paginate(f"{U}/dashboard-widgets")}
    accepted: list[tuple[str, dict]] = []  # (widget id, spec)
    for w in WIDGETS:
        try:
            rows = api.metrics(metrics_query(w))
        except LangfuseAPIError as e:
            print(f"  x {w['name']}: metrics query rejected: {e}")
            continue
        note = f"{len(rows)} row(s)"
        if dry_run:
            print(f"  ok {w['name']} ({note})")
            continue
        prior = existing_widgets.get(w["name"])
        try:
            if prior is None:
                wid = api("POST", f"{U}/dashboard-widgets", widget_body(w))["id"]
                print(f"  + {w['name']} ({note})")
            else:
                wid = prior["id"]
                if widget_drifted(w, prior):
                    api("PATCH", f"{U}/dashboard-widgets/{wid}", widget_body(w))
                    print(f"  ~ {w['name']} (definition updated; {note})")
                else:
                    print(f"  = {w['name']} ({note})")
        except LangfuseAPIError as e:  # one bad widget must not abort the dashboard
            print(f"  x {w['name']}: widget rejected: {e}")
            continue
        accepted.append((wid, w))
    if dry_run:
        return None

    dash = next((d for d in api.paginate(f"{U}/dashboards") if d["name"] == DASHBOARD["name"]), None)
    if dash is None:
        dash = api("POST", f"{U}/dashboards", DASHBOARD)
        print(f"  + dashboard {dash['id']}")
    elif (dash.get("description") or "") != DASHBOARD["description"]:
        api("PATCH", f"{U}/dashboards/{dash['id']}", DASHBOARD)
        print(f"  ~ dashboard {dash['id']} (description updated)")
    dash_id = dash["id"]

    ids = {w["name"]: wid for wid, w in accepted}
    want = {(ids[n], x, y, ww, hh) for n, x, y, ww, hh in layout([w for _, w in accepted])}
    placed = (api("GET", f"{U}/dashboards/{dash_id}").get("definition") or {}).get("widgets") or []
    if {placement_tuple(p) for p in placed} == want:
        print(f"  = layout ({len(placed)} placements)")
    else:
        for p in placed:  # rebuild, so a re-run always lands the same layout
            api("DELETE", f"{U}/dashboards/{dash_id}/placements/{p['id']}")
        for wid, x, y, ww, hh in sorted(want, key=lambda t: (t[2], t[1])):
            api("POST", f"{U}/dashboards/{dash_id}/placements",
                {"type": "widget", "widgetId": wid, "x": x, "y": y, "width": ww, "height": hh})
        print(f"  ~ layout rebuilt ({len(want)} placements)")

    placed = (api("GET", f"{U}/dashboards/{dash_id}").get("definition") or {}).get("widgets") or []
    names = {wid: w["name"] for wid, w in accepted}
    print(f"  read back: dashboard {dash_id} holds {len(placed)} widget(s)")
    for p in sorted(placed, key=lambda p: (p.get("y", 0), p.get("x", 0))):
        print(f"    - {names.get(p.get('widgetId'), p.get('widgetId'))}")
    return dash_id


def delete_dashboard(api: API) -> None:
    for d in api.paginate(f"{U}/dashboards"):
        if d["name"] == DASHBOARD["name"]:
            api("DELETE", f"{U}/dashboards/{d['id']}")
            print(f"deleted dashboard {d['id']}")
    names = {w["name"] for w in WIDGETS}
    for w in api.paginate(f"{U}/dashboard-widgets"):
        if w["name"] in names:
            api("DELETE", f"{U}/dashboard-widgets/{w['id']}")
            print(f"deleted widget {w['name']}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="validate and report, change nothing")
    ap.add_argument("--only", nargs="+", choices=["scores", "prompts", "dashboard"],
                    default=["scores", "prompts", "dashboard"])
    ap.add_argument("--delete-dashboard", action="store_true", help="delete the dashboard and its widgets")
    ap.add_argument("--project-name", default=EXPECTED_PROJECT, help="project the keys must map to")
    args = ap.parse_args(argv)

    api = API.from_env()
    project = verify_project(api, args.project_name)
    if args.delete_dashboard:
        delete_dashboard(api)
        return 0
    if "scores" in args.only:
        seed_score_configs(api, args.dry_run)
    if "prompts" in args.only:
        seed_prompts(api, args.dry_run)
    if "dashboard" in args.only:
        dash_id = seed_dashboard(api, args.dry_run)
        if dash_id:
            print(f"\n  {api.base_url}/project/{project['id']}/dashboards/{dash_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
