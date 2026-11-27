"""Evaluator logic and the experiment task wrapper. No network."""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langfuse import Evaluation
from langfuse.experiment import _run_evaluator, _run_task

from explorer.config import ClickHouseSettings, RunConfig
from explorer.evaluators import (
    ITEM_EVALUATORS,
    PASS_THROUGH_METRICS,
    RUN_EVALUATORS,
    answer_correct,
    answer_key_match,
    normalize,
    run_metrics,
    run_summary,
    summarize,
    values_match,
)
from explorer.models import RunResult

DEMO = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    mod_name = f"scripts_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, DEMO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # @dataclass resolves the module through sys.modules
    spec.loader.exec_module(mod)
    return mod


EXPECTED = {"origin": "LAX", "buffer_minutes": 9}


def out(answer, **extra):
    return {"answer_values": answer, **extra}


# ----------------------------------------------------------------------- normalize
@pytest.mark.parametrize("raw, want", [
    ("  lax ", "LAX"),
    ("LAX\x00\x00", "LAX"),
    ("1700-1759", "1700-1759"),
    ("9e", "9E"),           # carrier code, not a number
    ("1E5", "1E5"),         # no exponent parsing: stays a string
    ("09", 9),
    (" 12 ", 12),
    ("-3", -3),
    ("12.50", 12.5),
    (".5", 0.5),
    (9, 9),
    (9.0, 9.0),
    (None, None),
    (True, True),
])
def test_normalize(raw, want):
    got = normalize(raw)
    assert got == want and type(got) is type(want)


@pytest.mark.parametrize("expected, actual, ok", [
    (9, 9, True),
    (9, 9.0, True),
    (9, "9", True),
    (9, " 9 ", True),
    (9, 10, False),
    (9, 9.4, False),        # ints are exact
    (6, "June", False),
    (0.3621, 0.3640, True),  # floats: within 1% relative
    (0.3621, 0.3580, False),
    (0.3621, "0.3625", True),
    (24.23, 24.0, True),
    (24.23, 23.9, False),
    ("LAX", "lax", True),
    ("LAX", " LAX\x00", True),
    ("1700-1759", "1700-1759", True),
    ("1700-1759", "1700", False),
    ("LAX", None, False),
    (None, None, False),
    (1, True, False),        # booleans are not numbers
])
def test_values_match(expected, actual, ok):
    assert values_match(expected, actual) is ok


# ----------------------------------------------------------------------- item evaluators
def test_answer_correct_all_keys():
    ev = answer_correct(input={}, output=out({"origin": "lax", "buffer_minutes": "9"}), expected_output=EXPECTED,
                        metadata=None)
    assert isinstance(ev, Evaluation)
    assert (ev.name, ev.value, ev.data_type) == ("answer_correct", 1.0, "BOOLEAN")


def test_answer_correct_key_names_case_insensitive():
    ev = answer_correct(input={}, output=out({"Origin": "LAX", "BUFFER_MINUTES": 9}), expected_output=EXPECTED)
    assert ev.value == 1.0


def test_answer_correct_partial_is_wrong_and_says_which():
    ev = answer_correct(input={}, output=out({"origin": "LAX", "buffer_minutes": 10}), expected_output=EXPECTED)
    assert ev.value == 0.0
    assert "buffer_minutes" in ev.comment


def test_answer_correct_missing_key():
    ev = answer_correct(input={}, output=out({"origin": "LAX"}), expected_output=EXPECTED)
    assert ev.value == 0.0


@pytest.mark.parametrize("bad", [None, "LAX, 9", {}, {"answer_values": None}, {"answer_values": "LAX"}])
def test_answer_correct_malformed_output(bad):
    assert answer_correct(input={}, output=bad, expected_output=EXPECTED).value == 0.0
    assert answer_key_match(input={}, output=bad, expected_output=EXPECTED).value == 0.0


def test_error_output_scores_incorrect_with_reason():
    o = {"answer_values": {}, "error": "ClickHouseError: boom", "wall_ms": 1200.0}
    ev = answer_correct(input={}, output=o, expected_output=EXPECTED)
    assert ev.value == 0.0 and "boom" in ev.comment


@pytest.mark.parametrize("failure", [{"error": "ClickHouseError: verification failed"}, {"stop_reason": "error"}])
def test_error_after_answer_cannot_earn_credit(failure):
    o = out(EXPECTED, **failure)
    assert answer_correct(input={}, output=o, expected_output=EXPECTED).value == 0.0
    assert answer_key_match(input={}, output=o, expected_output=EXPECTED).value == 0.0
    assert summarize([_item_result(EXPECTED, o)])["accuracy"] == 0.0
    assert summarize([_item_result(EXPECTED, o)])["errors"] == 1


def test_no_expected_output_is_not_correct():
    assert answer_correct(input={}, output=out({"origin": "LAX"}), expected_output=None).value == 0.0


def test_answer_key_match_fraction():
    exp = {"origin": "ORD", "month": 6, "buffer_minutes": 51}
    ev = answer_key_match(input={}, output=out({"origin": "ORD", "month": 6, "buffer_minutes": 47}),
                          expected_output=exp)
    assert (ev.name, ev.data_type) == ("answer_key_match", "NUMERIC")
    assert ev.value == pytest.approx(2 / 3, abs=1e-3)


def test_run_metrics_pass_through_and_task_error():
    o = out({}, db_time_share=0.031, scenarios_evaluated=612, wall_ms=48000.5, run_cost_usd=0.21,
            llm_calls=5, db_server_ms_total=9100.0, summary="not a number")
    evs = {e.name: e for e in run_metrics(input={}, output=o, expected_output=EXPECTED)}
    # The agent's own trace scores are not re-emitted (no duplicate names in compare views).
    assert set(evs) == {"wall_ms", "task_error"}
    assert evs["wall_ms"].data_type == "NUMERIC" and evs["wall_ms"].value == 48000.5
    assert evs["task_error"].value == 0.0 and evs["task_error"].data_type == "BOOLEAN"
    assert not set(evs) & {"db_time_share", "scenarios_evaluated", "run_cost_usd", "llm_calls"}


def test_run_metrics_skips_missing_and_flags_error():
    evs = {e.name: e for e in run_metrics(input={}, output={"error": "Timeout", "wall_ms": 900000.0})}
    assert set(evs) == {"wall_ms", "task_error"}
    assert evs["task_error"].value == 1.0 and "Timeout" in evs["task_error"].comment
    assert {e.name for e in run_metrics(input={}, output=None)} == {"task_error"}


# ----------------------------------------------------------------------- SDK contract
@pytest.mark.parametrize("ev", ITEM_EVALUATORS)
def test_item_evaluators_match_sdk_signature(ev):
    # The SDK calls item evaluators with exactly these keywords (langfuse/_client/client.py).
    params = inspect.signature(ev).parameters
    for name in ("input", "output", "expected_output", "metadata"):
        assert name in params and params[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


async def test_item_evaluators_through_sdk_runner():
    kwargs = {"input": {"question_id": "q"}, "output": out({"origin": "LAX", "buffer_minutes": 9}, wall_ms=10.0),
              "expected_output": EXPECTED, "metadata": {"difficulty": "medium"}}
    names = []
    for ev in ITEM_EVALUATORS:
        res = await _run_evaluator(ev, _raise_on_error=True, **kwargs)
        assert res and all(isinstance(r, Evaluation) for r in res)
        names += [r.name for r in res]
    assert {"answer_correct", "answer_key_match", "wall_ms", "task_error"} <= set(names)


def _item_result(expected, output):
    return SimpleNamespace(item=SimpleNamespace(expected_output=expected), output=output, evaluations=[])


def test_summarize_and_run_summary():
    results = [
        _item_result(EXPECTED, out({"origin": "LAX", "buffer_minutes": 9}, db_time_share=0.02, wall_ms=1000.0,
                                   run_cost_usd=0.10, llm_calls=4, scenarios_evaluated=600)),
        _item_result({"carrier": "EV", "month": 6}, out({"carrier": "F9", "month": 6}, db_time_share=0.04,
                                                        wall_ms=3000.0, run_cost_usd=0.30)),
        _item_result(EXPECTED, {"answer_values": {}, "error": "boom", "wall_ms": 500.0}),
    ]
    s = summarize(results)
    assert s["items"] == 3 and s["errors"] == 1
    assert s["accuracy"] == pytest.approx(1 / 3)
    assert s["mean_db_time_share"] == pytest.approx(0.03)
    assert s["mean_wall_ms"] == pytest.approx(1500.0)
    assert s["mean_run_cost_usd"] == pytest.approx(0.20)

    evs = {e.name: e for e in run_summary(item_results=results)}
    assert set(evs) == {"accuracy", "mean_db_time_share", "mean_wall_ms", "mean_run_cost_usd"}
    assert evs["accuracy"].value == pytest.approx(0.3333, abs=1e-4)
    assert "1/3 correct" in evs["accuracy"].comment


async def test_run_evaluator_through_sdk_runner():
    results = [_item_result(EXPECTED, out({"origin": "LAX", "buffer_minutes": 9}, wall_ms=1.0))]
    for ev in RUN_EVALUATORS:
        res = await _run_evaluator(ev, _raise_on_error=True, item_results=results)
        assert [r.name for r in res][0] == "accuracy" and res[0].value == 1.0


def test_run_summary_empty_run():
    assert run_summary(item_results=[]) == []


# ----------------------------------------------------------------------- task wrapper
SETTINGS = ClickHouseSettings(host="localhost", user="u", password="", port=8123, secure=False)
ITEM = SimpleNamespace(id="q-padding", input={"question_id": "q-padding", "question": "How much padding?",
                                              "answer_keys": ["origin", "buffer_minutes"]},
                       expected_output=EXPECTED, metadata={})


def _fake_result(**kw):
    base = dict(trace_id="t" * 32, question_id="q-padding", stop_reason="answered", summary="LAX needs 9 min",
                answer_values={"origin": "LAX", "buffer_minutes": 9}, best_scenario={"origin": "LAX", "buffer": 9},
                metric_value=0.8507, rounds=2, scenarios_evaluated=610, llm_calls=5, db_server_ms_total=4200.0,
                db_wait_ms_total=5100.0, wall_ms=60000.0, cost_usd=0.25, recommendation_grounded=True)
    base.update(kw)
    return RunResult(**base)


def test_task_output_preserves_unknown_cost():
    rx = _load_script("run_experiment")
    assert rx.task_output(_fake_result(cost_usd=None), RunConfig())["run_cost_usd"] is None


async def test_task_wraps_a_successful_run():
    rx = _load_script("run_experiment")
    seen = {}

    async def fake_run(question, cfg, *, ch, lf, tags=None, session_id=None, metadata=None, version=None):
        seen.update(question=question, cfg=cfg, tags=tags, session_id=session_id, version=version)
        return _fake_result()

    cfg = RunConfig(max_scenarios_per_round=10)
    task = rx.make_task(cfg=cfg, ch_settings=SETTINGS, lf=None, variant_key="breadth-10",
                        run_name="breadth-10-x", run_fn=fake_run)
    o = await _run_task(task, ITEM)  # exactly how the SDK calls it
    assert o["answer_values"] == {"origin": "LAX", "buffer_minutes": 9} and o["error"] is None
    assert o["db_time_share"] == pytest.approx(5100 / 60000, abs=1e-6)
    assert o["max_scenarios_per_round"] == 10
    assert set(PASS_THROUGH_METRICS) <= set(o)
    # The agent sees the question only: no reference SQL, no expected answer.
    q = seen["question"]
    assert (q.id, q.answer_keys, q.reference_sql, q.expected) == ("q-padding", ["origin", "buffer_minutes"], None, None)
    assert seen["tags"] == ["experiment", "breadth-10"] and seen["session_id"] == "breadth-10-x"
    assert seen["version"] == "breadth-10"  # the dashboard's Experiment A widgets group by trace version
    assert answer_correct(input=ITEM.input, output=o, expected_output=EXPECTED).value == 1.0


async def test_task_never_raises():
    rx = _load_script("run_experiment")

    async def boom(*a, **k):
        raise RuntimeError("agent exploded")

    task = rx.make_task(cfg=RunConfig(), ch_settings=SETTINGS, lf=None, variant_key="breadth-300",
                        run_name="r", run_fn=boom)
    o = await _run_task(task, ITEM)
    assert o["error"] == "RuntimeError: agent exploded" and o["answer_values"] == {}
    assert o["question_id"] == "q-padding" and o["wall_ms"] >= 0
    assert answer_correct(input=ITEM.input, output=o, expected_output=EXPECTED).value == 0.0
    assert {e.name: e.value for e in run_metrics(input={}, output=o)}["task_error"] == 1.0


async def test_task_bad_item_and_timeout():
    rx = _load_script("run_experiment")
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(5)

    task = rx.make_task(cfg=RunConfig(), ch_settings=SETTINGS, lf=None, variant_key="v", run_name="r",
                        run_fn=slow, item_timeout_s=0.05)
    o = await _run_task(task, ITEM)
    assert o["error"].startswith("TimeoutError")
    o = await _run_task(task, SimpleNamespace(id="x", input=None))
    assert o["error"].startswith("ValueError")


def test_variants_and_compare_parsing():
    rx = _load_script("run_experiment")
    ns = SimpleNamespace(compare=None, breadth=None, max_rounds=None)
    vs = rx.build_variants(ns, RunConfig(max_rounds=4))
    # Experiment A holds rounds fixed (and small) for both breadths, whatever the base config says.
    assert [(v.key, v.cfg.max_scenarios_per_round, v.cfg.max_rounds) for v in vs] == [
        ("breadth-10", 10, rx.EXPERIMENT_A_ROUNDS), ("breadth-300", 300, rx.EXPERIMENT_A_ROUNDS)]
    assert rx.EXPERIMENT_A_ROUNDS == 2
    assert len({v.experiment for v in vs}) == 1
    ns = SimpleNamespace(compare=None, breadth=None, max_rounds=3)  # --max-rounds overrides both
    assert {v.cfg.max_rounds for v in rx.build_variants(ns, RunConfig())} == {3}

    ns = SimpleNamespace(compare=rx.parse_compare("models", "claude-opus-5-5,claude-sonnet-5-5"),
                         breadth=None, max_rounds=None)
    vs = rx.build_variants(ns, RunConfig())
    assert [(v.key, v.cfg.model, v.cfg.max_scenarios_per_round) for v in vs] == [
        ("model-claude-opus-5-5", "claude-opus-5-5", 300), ("model-claude-sonnet-5-5", "claude-sonnet-5-5", 300)]

    ns = SimpleNamespace(compare=rx.parse_compare("prompt-labels", "production,candidate"),
                         breadth="narrow", max_rounds=2)
    vs = rx.build_variants(ns, RunConfig())
    assert [(v.cfg.prompt_label, v.cfg.max_scenarios_per_round, v.cfg.max_rounds) for v in vs] == [
        ("production", 10, 2), ("candidate", 10, 2)]

    for bad in [("models", "a"), ("models", "a,a"), ("temperature", "a,b")]:
        with pytest.raises(SystemExit):
            rx.parse_compare(*bad)
    with pytest.raises(SystemExit):
        rx.build_variants(SimpleNamespace(compare=("models", ["a", "b"]), breadth="both", max_rounds=None),
                          RunConfig())


def test_run_name_stamp_format():
    rx = _load_script("run_experiment")
    stamp = rx.utc_stamp()
    assert len(stamp) == 16 and stamp.endswith("Z")


def test_experiment_items_trim_long_metadata():
    from langfuse.api import DatasetItem

    rx = _load_script("run_experiment")
    item = DatasetItem(id="q-padding", status="ACTIVE", input={"question": "x"}, expected_output=EXPECTED,
                       metadata={"difficulty": "medium", "margin": 0.1, "notes": "n" * 300,
                                 "reference_sql": "SELECT " + "x" * 400, "runner_up": {"origin": "ATL"}},
                       dataset_id="d1", dataset_name="ds", created_at="2026-11-26T00:00:00Z",
                       updated_at="2026-11-26T00:00:00Z", media_references=[])
    [trimmed] = rx.experiment_items([item])
    assert trimmed.metadata == {"difficulty": "medium", "margin": 0.1}
    assert (trimmed.id, trimmed.dataset_id, trimmed.expected_output) == ("q-padding", "d1", EXPECTED)
    assert len(item.metadata) == 5  # the original is untouched
