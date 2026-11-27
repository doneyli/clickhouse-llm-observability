"""No-network checks of what scripts/seed_langfuse.py and scripts/run_concurrency.py send.

The failure modes these guard against are all silent on the server side: a widget that
filters a BOOLEAN score in the numeric view renders empty, a typo in a score name renders
empty, a high-cardinality dimension without row_limit is refused only at publish time.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

DEMO_DIR = Path(__file__).resolve().parent.parent
for p in (DEMO_DIR, DEMO_DIR / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, DEMO_DIR / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


seed = _load("seed_langfuse")
conc = _load("run_concurrency")

# The fixed contract with explorer/agent.py and the experiment evaluators.
CONTRACT = {
    "db_server_ms": "NUMERIC",
    "round_scenarios": "NUMERIC", "round_db_server_ms_total": "NUMERIC",
    "round_db_p95_ms": "NUMERIC", "round_sweep_wall_ms": "NUMERIC",
    "scenarios_evaluated": "NUMERIC", "db_server_ms_total": "NUMERIC", "db_p95_ms": "NUMERIC",
    "db_wait_ms_total": "NUMERIC", "db_time_share": "NUMERIC", "scenarios_per_minute": "NUMERIC",
    "llm_calls": "NUMERIC", "rounds": "NUMERIC", "run_cost_usd": "NUMERIC",
    "stop_reason": "CATEGORICAL", "recommendation_grounded": "BOOLEAN",
    "answer_correct": "BOOLEAN", "answer_key_match": "NUMERIC",
}
VIEW_FOR_TYPE = {"NUMERIC": "scores-numeric", "BOOLEAN": "scores-boolean", "CATEGORICAL": "scores-categorical"}


# ---------------------------------------------------------------- score configs

def test_every_contract_score_has_exactly_one_config_of_the_right_type():
    names = [c["name"] for c in seed.SCORE_CONFIGS]
    assert len(names) == len(set(names))
    assert seed.SCORE_TYPES == CONTRACT


@pytest.mark.parametrize("cfg", seed.SCORE_CONFIGS, ids=lambda c: c["name"])
def test_score_config_payload_is_valid(cfg):
    assert len(cfg["name"]) <= 35 and re.fullmatch(r"[A-Za-z0-9_ .()\-]+", cfg["name"])
    assert cfg["description"] and len(cfg["description"]) > 20
    if cfg["dataType"] == "NUMERIC":
        assert "categories" not in cfg
        assert cfg.get("minValue", 0) == 0  # every numeric score here is a count, a time or a share
    elif cfg["dataType"] == "BOOLEAN":  # categories are generated server-side and cannot be passed
        assert not {"categories", "minValue", "maxValue"} & set(cfg)
    else:
        assert "minValue" not in cfg and "maxValue" not in cfg


def test_ranges_and_categories():
    by = {c["name"]: c for c in seed.SCORE_CONFIGS}
    for share in ("db_time_share", "answer_key_match"):
        assert (by[share]["minValue"], by[share]["maxValue"]) == (0, 1)
    assert [c["label"] for c in by["stop_reason"]["categories"]] == ["answered", "max_rounds", "budget", "error"]
    assert [c["value"] for c in by["stop_reason"]["categories"]] == [0, 1, 2, 3]


def test_plan_score_configs_creates_missing_patches_drift_and_never_duplicates():
    existing = [
        {"id": "a", "name": "db_server_ms", "dataType": "NUMERIC", "minValue": 0.0,
         "description": next(c for c in seed.SCORE_CONFIGS if c["name"] == "db_server_ms")["description"]},
        {"id": "b", "name": "llm_calls", "dataType": "NUMERIC", "minValue": 0, "description": "old"},
        {"id": "c", "name": "stop_reason", "dataType": "NUMERIC"},  # wrong type: report, don't touch
        {"id": "d", "name": "pass or fail", "dataType": "CATEGORICAL"},  # Langfuse default, not ours
    ]
    create, patch, conflicts = seed.plan_score_configs(existing)
    created = {c["name"] for c in create}
    assert created == set(CONTRACT) - {"db_server_ms", "llm_calls", "stop_reason"}
    assert patch == [("b", {"description": next(c["description"] for c in seed.SCORE_CONFIGS
                                                if c["name"] == "llm_calls")})]
    assert len(conflicts) == 1 and conflicts[0].startswith("stop_reason")


def test_plan_score_configs_is_a_no_op_once_everything_exists():
    existing = [{**c, "id": c["name"]} for c in seed.SCORE_CONFIGS]
    assert seed.plan_score_configs(existing) == ([], [], [])


# ---------------------------------------------------------------- prompts

def test_plan_prompts_creates_only_when_production_text_differs():
    store = {"same": {"prompt": "hello {{x}}", "version": 3}, "changed": {"prompt": "old", "version": 1}}
    plan = seed.plan_prompts({"same": "hello {{x}}", "changed": "new", "missing": "brand new"}, store.get)
    assert plan == [("same", "unchanged", 3), ("changed", "create", "new"), ("missing", "create", "brand new")]


def test_load_prompts_returns_the_agent_prompts_when_present():
    prompts = seed.load_prompts()
    if prompts is None:
        pytest.skip("explorer/prompts.py not written yet")
    assert prompts and all(isinstance(k, str) and v for k, v in prompts.items())


# ---------------------------------------------------------------- dashboard widgets

def _score_names(w):
    return [v for f in w["filters"] if f["column"] == "name" for v in f["value"]]


@pytest.mark.parametrize("w", seed.WIDGETS, ids=lambda w: w["name"])
def test_widget_payload_shape(w):
    body = seed.widget_body(w)
    assert set(body) == {"name", "description", "view", "chartType", "dimensions", "metrics", "filters",
                         "chartConfig"}
    assert w["view"] in seed.VIEWS and w["chartType"] in seed.CHART_TYPES
    assert body["chartConfig"]["type"] == w["chartType"]
    assert w["description"]
    for m in w["metrics"]:
        assert set(m) == {"measure", "agg"} and m["agg"] in seed.AGGS
    for f in w["filters"]:
        assert {"column", "operator", "value", "type"} <= set(f)
    dims = {d["field"] for d in w["dimensions"]}
    assert not dims & seed.NOT_WIDGET_DIMENSIONS
    assert "tags" not in dims  # tags as a dimension groups by the whole array, order-sensitively
    if dims & seed.HIGH_CARDINALITY:
        assert w["chartConfig"].get("row_limit")
    if w["chartType"] not in ("PIVOT_TABLE",):
        assert len(w["dimensions"]) <= 1 and (len(w["metrics"]) == 1)


@pytest.mark.parametrize("w", [w for w in seed.WIDGETS if w["view"].startswith("scores-")], ids=lambda w: w["name"])
def test_score_widgets_name_contract_scores_in_the_matching_view(w):
    names = _score_names(w)
    assert names, "an unfiltered score widget mixes every score on one axis"
    for n in names:
        assert n in CONTRACT, f"{n!r} is not a score the agent emits"
        assert VIEW_FOR_TYPE[CONTRACT[n]] == w["view"], f"{n} is {CONTRACT[n]}: it would render empty in {w['view']}"


@pytest.mark.parametrize("w", [w for w in seed.WIDGETS if w["view"] == "observations"], ids=lambda w: w["name"])
def test_observation_widgets_name_contract_observations(w):
    names = [v for f in w["filters"] if f["column"] == "name" for v in f["value"]]
    assert names and set(names) <= seed.OBSERVATION_NAMES


def test_llm_vs_db_split_counts_each_kind_once():
    # The model's own generations only (not their SDK children), and DB chains that do not
    # contain each other (round contains sweep, so round would double the sweep's time).
    assert seed.LLM_OBS == ["plan", "analyse"]
    assert "round" not in seed.DB_OBS and "clickhouse.query" not in seed.DB_OBS


def test_spec_widgets_are_all_there():
    names = " | ".join(w["name"] for w in seed.WIDGETS)
    for needle in ("LLM vs ClickHouse", "p50", "p95", "p99", "distribution", "Scenarios per run",
                   "Scenarios per minute", "LLM calls vs scenarios", "Concurrency", "Experiment A — accuracy"):
        assert needle in names
    assert len({w["name"] for w in seed.WIDGETS}) == len(seed.WIDGETS)


def test_metrics_query_matches_the_widget():
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    by = {w["name"]: w for w in seed.WIDGETS}
    q = seed.metrics_query(by["Run wall time — LLM vs ClickHouse, per run"], now)
    assert q["metrics"] == [{"measure": "latency", "aggregation": "sum"}]
    assert q["orderBy"] == [{"field": "sum_latency", "direction": "desc"}]
    assert q["config"]["row_limit"] == 20
    assert q["fromTimestamp"] == "2026-10-27T12:00:00Z" and q["toTimestamp"] == "2026-11-26T12:00:00Z"
    ts = seed.metrics_query(by["LLM calls vs scenarios per run"], now)
    assert ts["timeDimension"] == {"granularity": "day"} and "orderBy" not in ts
    hist = seed.metrics_query(by["ClickHouse time per query — distribution"], now)
    assert hist["config"] == {"bins": 20}
    tile = seed.metrics_query(by["Runs"], now)
    assert "config" not in tile and "timeDimension" not in tile


def test_layout_fills_a_12_column_grid_without_overlap():
    cells = set()
    for name, x, y, w, h in seed.layout(seed.WIDGETS):
        assert 0 <= x and x + w <= 12, name
        for cx in range(x, x + w):
            for cy in range(y, y + h):
                assert (cx, cy) not in cells, f"{name} overlaps"
                cells.add((cx, cy))


def test_widget_drift_ignores_server_defaults_but_sees_real_changes():
    w = seed.WIDGETS[0]
    stored = {**seed.widget_body(w), "id": "x", "chartConfig": {**w["chartConfig"], "show_value_labels": False}}
    assert not seed.widget_drifted(w, stored)
    stored["filters"] = [{**w["filters"][0], "value": ["something-else"]}]
    assert seed.widget_drifted(w, stored)


def test_placement_tuple_reads_both_size_spellings():
    assert seed.placement_tuple({"widgetId": "w", "x": 0, "y": 4, "x_size": 3, "y_size": 4}) == ("w", 0, 4, 3, 4)
    assert seed.placement_tuple({"widgetId": "w", "x": 0, "y": 4, "width": 3, "height": 4}) == ("w", 0, 4, 3, 4)


# ---------------------------------------------------------------- run_concurrency helpers

def test_parse_levels():
    assert conc.parse_levels("1,5, 10,25") == [1, 5, 10, 25]
    with pytest.raises(Exception):
        conc.parse_levels("0,5")


def test_questions_cycle_when_agents_outnumber_them():
    assert conc.assign_questions(["a", "b", "c"], 7) == ["a", "b", "c", "a", "b", "c", "a"]
    assert conc.assign_questions(["a", "b", "c"], 2) == ["a", "b"]


def test_questions_file_loads_into_questions():
    if not conc.QUESTIONS_FILE.exists():
        pytest.skip("data/questions.yaml not written yet")
    qs = conc.load_questions()
    assert qs and all(q.id and q.text for q in qs)
    assert len({q.id for q in qs}) == len(qs)


def test_tags_session_and_pool():
    assert conc.agent_tags(10) == ["concurrency", "agents-10"]  # run_question prepends analytics-agentic-speed
    assert conc.session_id_for(5, "20261126T1200Z") == "concurrency-agents-005-20261126T1200Z"
    assert conc.session_id_for(5, "s") < conc.session_id_for(25, "s")
    cfg = conc.RunConfig(concurrency=16)
    assert conc.pool_size([1, 5, 25], cfg) == 400


def test_percentile_matches_linear_interpolation():
    vals = [float(v) for v in range(1, 101)]
    assert conc.percentile(vals, 50) == pytest.approx(50.5)
    assert conc.percentile(vals, 95) == pytest.approx(95.05)
    assert conc.percentile([7.0], 99) == 7.0
    assert conc.percentile([], 50) is None


def test_estimate_bounds_the_spend():
    cfg = conc.RunConfig(max_rounds=2, budget_usd=3.0, model="claude-sonnet-5-5")
    est = conc.estimate([1, 5, 10, 25], cfg)
    assert est.runs == 41
    assert (est.llm_calls_low, est.llm_calls_high) == (82, 164)
    assert 0 < est.usd_typical < est.usd_cap == pytest.approx(123.0)


def test_summarize_counts_raised_and_errored_runs_as_failed():
    class R:
        def __init__(self, stop):
            self.stop_reason, self.scenarios_evaluated, self.llm_calls, self.cost_usd = stop, 100, 4, 0.5

    s = conc.summarize(3, [10.0, 20.0, 30.0], 1, 2.0, [R("answered"), R("error"), RuntimeError("x")])
    assert (s["runs_ok"], s["runs_failed"], s["queries"], s["query_errors"]) == (1, 2, 3, 1)
    assert s["p50"] == 20.0 and s["queries_per_s"] == 1.5 and s["scenarios"] == 200


def test_dry_run_prints_the_plan_and_spends_nothing(capsys, monkeypatch):
    monkeypatch.delenv("EXPLORER_TARGET", raising=False)
    monkeypatch.setattr(conc, "load_env", lambda: None)
    monkeypatch.setenv("CH_HOST", "example.clickhouse.cloud")
    assert conc.main(["--levels", "1,5", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "agents-5" in out and "estimate:" in out and "dry run" in out


def test_refuses_the_playground(monkeypatch):
    monkeypatch.setattr(conc, "load_env", lambda: None)
    monkeypatch.setenv("EXPLORER_TARGET", "playground")
    with pytest.raises(SystemExit):
        conc.main(["--dry-run"])


def test_without_yes_it_stops_before_running(capsys, monkeypatch):
    monkeypatch.delenv("EXPLORER_TARGET", raising=False)
    monkeypatch.setattr(conc, "load_env", lambda: None)
    monkeypatch.setenv("CH_HOST", "example.clickhouse.cloud")
    monkeypatch.setattr(conc, "load_questions", lambda path: [conc.Question(id="q1", text="t")])
    assert conc.main(["--levels", "2"]) == 2
    assert "--yes" in capsys.readouterr().out
