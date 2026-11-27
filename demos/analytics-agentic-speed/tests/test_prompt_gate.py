"""The prompt gate's threshold logic, report and wiring: no network, no Langfuse, no ClickHouse."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from explorer import prompts as P
from explorer.evaluators import load_questions

DEMO = Path(__file__).resolve().parent.parent
REPO = DEMO.parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "analytics-agentic-speed-prompt-gate.yml"
INTEGRITY = REPO / ".github" / "workflows" / "demo-integrity.yml"


def _load_script(name: str):
    mod_name = f"scripts_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, DEMO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_script("prompt_gate")


@pytest.fixture(scope="module")
def thresholds(gate):
    return gate.load_thresholds()


HERO_WANT = {"origin": "LAX", "buffer_minutes": 9}


def _result(qid: str, want: dict, got: dict, *, cost=0.14, wall_ms=60_000.0, share=0.07, scen=400,
            calls=4, error=None):
    out = {"question_id": qid, "answer_values": got, "run_cost_usd": cost, "wall_ms": wall_ms,
           "db_time_share": share, "scenarios_evaluated": scen, "llm_calls": calls,
           "stop_reason": "error" if error else "answered", "error": error}
    item = SimpleNamespace(id=qid, input={"question_id": qid, "question": "q"}, expected_output=want)
    return SimpleNamespace(item=item, output=out, evaluations=[])


def _good_run():
    return [
        _result("q-padding", HERO_WANT, {"origin": "LAX", "buffer_minutes": 9}),
        _result("q-carrier-month", {"carrier": "WN", "month": 7}, {"carrier": "wn", "month": "7"}),
        _result("q-late-departure-block", {"origin": "ORD", "dep_time_block": "1900-1959"},
                {"origin": "ORD", "dep_time_block": "1900-1959"}),
    ]


def _readback(n):
    return {"run_items": n, "scored_items": n, "ok": True, "error": None, "langfuse_summary": None}


# ----------------------------------------------------------------------- thresholds.json
def test_thresholds_file_is_valid_and_matches_the_standard_config(gate, thresholds):
    rx = _load_script("run_experiment")
    assert thresholds["dataset"] == rx.DATASET_NAME
    # The bar was calibrated at Experiment A's wide config; the gate must run exactly that.
    assert thresholds["config"]["max_scenarios_per_round"] == rx.BREADTH["wide"] == 300
    assert thresholds["config"]["max_rounds"] == rx.EXPERIMENT_A_ROUNDS == 2
    ids = {q.id for q in load_questions()}
    assert thresholds["must_pass"] and set(thresholds["must_pass"]) <= ids
    assert "q-padding" in thresholds["must_pass"]  # the hero: the one-minute answer
    assert {"accuracy", "mean_run_cost_usd"} <= set(thresholds["hard"])


def test_baseline_passes_its_own_bar_with_headroom(gate, thresholds):
    """Policy bounds leave headroom from the historical baseline; this does not establish noise tolerance."""
    base = {k: v for k, v in thresholds["baseline"].items() if not k.startswith("_")}
    for kind in ("hard", "soft"):
        for name, rule in thresholds[kind].items():
            assert name in base, f"{name} has no baseline to justify its bound"
            assert gate.check(rule, base[name]) == "PASS", f"baseline fails {kind}.{name}"
            if "min" in rule:
                assert rule["min"] < base[name] or name == "errors"
            else:
                assert rule["max"] > base[name] or name == "errors"


def test_accuracy_floor_allows_one_miss_and_blocks_two(thresholds):
    floor = thresholds["hard"]["accuracy"]["min"]
    assert 11 / 15 >= floor > 10 / 15  # baseline 12/15: one more miss passes, two more block


@pytest.mark.parametrize("bad", [
    {"hard": {"accuracy": {"min": 0.7, "max": 0.9}}},
    {"hard": {"accuracy": {}}},
    {"hard": {"accuracy": {"floor": 0.7}}},
    {"hard": {"accuracy": {"min": "0.7"}}},
    {"hard": {"accuracy": {"min": True}}},
    {"must_pass": "q-padding"},
    {"config": {"max_scenarios_per_round": 0, "max_rounds": 2}},
])
def test_load_thresholds_rejects_malformed_rules(gate, tmp_path, thresholds, bad):
    t = json.loads(json.dumps(thresholds))
    t.update(bad)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(t))
    with pytest.raises(ValueError):
        gate.load_thresholds(p)


def test_load_thresholds_rejects_missing_sections(gate, tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"dataset": "x", "hard": {}, "soft": {}}))
    with pytest.raises(ValueError, match="config"):
        gate.load_thresholds(p)


# ----------------------------------------------------------------------- check()
@pytest.mark.parametrize("rule,value,want", [
    ({"min": 0.7}, 0.8, "PASS"),
    ({"min": 0.7}, 0.7, "PASS"),  # the bound itself passes
    ({"min": 0.7}, 0.6667, "FAIL"),
    ({"max": 0.22}, 0.22, "PASS"),
    ({"max": 0.22}, 0.2201, "FAIL"),
    ({"max": 0}, 0.0, "PASS"),
    ({"max": 0}, 1.0, "FAIL"),
    ({"min": 0.7}, None, "MISSING"),
    ({"max": 0.22}, float("nan"), "MISSING"),
])
def test_check(gate, rule, value, want):
    assert gate.check(rule, value) == want


# ----------------------------------------------------------------------- metrics
def test_gate_metrics_from_item_results(gate):
    m = gate.gate_metrics(_good_run(), _readback(3), ["q-padding"])
    assert m["items_evaluated"] == 3 and m["items_in_langfuse"] == 3
    assert m["accuracy"] == 1.0 and m["errors"] == 0
    assert m["mean_run_cost_usd"] == pytest.approx(0.14)
    assert m["mean_wall_s"] == pytest.approx(60.0)  # ms -> s
    assert m["mean_db_time_share"] == pytest.approx(0.07)
    assert m["mean_scenarios_evaluated"] == 400 and m["mean_llm_calls"] == 4
    assert m["must_pass:q-padding"] == 1.0


def test_errored_item_counts_as_wrong_and_as_an_error(gate):
    run = _good_run()
    run[1] = _result("q-carrier-month", {"carrier": "WN", "month": 7}, {}, error="TimeoutError: item exceeded 900s")
    m = gate.gate_metrics(run, _readback(3), ["q-padding"])
    assert m["accuracy"] == pytest.approx(2 / 3) and m["errors"] == 1


def test_gate_blocks_verification_failures_with_correct_leftover_answers(gate, thresholds):
    results = [_result("q-padding" if i == 0 else f"q-{i}", HERO_WANT, HERO_WANT,
                       error="ClickHouseError: verify failed") for i in range(15)]
    metrics = gate.gate_metrics(results, _readback(15), thresholds["must_pass"])
    rows, blocked = gate.evaluate(thresholds, metrics, 15)
    assert metrics["accuracy"] == 0 and metrics["errors"] == 15
    assert blocked and _rows_by_name(rows)["must_pass:q-padding"]["status"] == "FAIL"


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), -1, True])
def test_gate_cannot_average_away_one_unknown_cost(gate, thresholds, missing):
    results = _good_run()
    results[1].output["run_cost_usd"] = missing
    metrics = gate.gate_metrics(results, _readback(3), thresholds["must_pass"])
    rows, blocked = gate.evaluate(thresholds, metrics, 3)
    assert metrics["mean_run_cost_usd"] is None
    assert blocked and _rows_by_name(rows)["mean_run_cost_usd"]["status"] == "MISSING"


def test_one_minute_off_on_the_hero_fails_must_pass(gate):
    run = _good_run()
    run[0] = _result("q-padding", HERO_WANT, {"origin": "LAX", "buffer_minutes": 10})  # a 5-minute grid
    m = gate.gate_metrics(run, _readback(3), ["q-padding"])
    assert m["must_pass:q-padding"] == 0.0


def test_must_pass_question_not_in_the_run_is_missing(gate):
    m = gate.gate_metrics(_good_run()[1:], _readback(2), ["q-padding"])
    assert m["must_pass:q-padding"] is None


def test_no_readback_means_no_langfuse_evidence(gate):
    assert gate.gate_metrics(_good_run(), None, [])["items_in_langfuse"] is None


# ----------------------------------------------------------------------- evaluate()
def _rows_by_name(rows):
    return {r["name"]: r for r in rows}


def test_good_run_passes(gate, thresholds):
    rows, blocked = gate.evaluate(thresholds, gate.gate_metrics(_good_run(), _readback(3), thresholds["must_pass"]), 3)
    assert not blocked
    by = _rows_by_name(rows)
    # the gate's own rules come first, then must-pass, then the file's hard and soft rules
    assert [r["name"] for r in rows][:3] == ["items_evaluated", "items_in_langfuse", "must_pass:q-padding"]
    assert by["accuracy"]["baseline"] == 0.8 and by["mean_wall_s"]["kind"] == "soft"


def test_hero_wrong_blocks_even_when_accuracy_passes(gate, thresholds):
    run = _good_run() + [_result(f"q-x{i}", {"k": 1}, {"k": 1}) for i in range(7)]
    run[0] = _result("q-padding", HERO_WANT, {"origin": "ATL", "buffer_minutes": 10})
    m = gate.gate_metrics(run, _readback(len(run)), thresholds["must_pass"])
    assert m["accuracy"] == pytest.approx(0.9)
    rows, blocked = gate.evaluate(thresholds, m, len(run))
    assert blocked and _rows_by_name(rows)["must_pass:q-padding"]["status"] == "FAIL"


def test_cost_ceiling_blocks(gate, thresholds):
    run = [_result(r.item.id, r.item.expected_output, r.output["answer_values"], cost=0.30) for r in _good_run()]
    rows, blocked = gate.evaluate(thresholds, gate.gate_metrics(run, _readback(3), ["q-padding"]), 3)
    assert blocked and _rows_by_name(rows)["mean_run_cost_usd"]["status"] == "FAIL"


def test_soft_misses_warn_but_do_not_block(gate, thresholds):
    run = [_result(r.item.id, r.item.expected_output, r.output["answer_values"], wall_ms=200_000.0, share=0.4,
                   scen=70, calls=6) for r in _good_run()]
    rows, blocked = gate.evaluate(thresholds, gate.gate_metrics(run, _readback(3), ["q-padding"]), 3)
    assert not blocked
    by = _rows_by_name(rows)
    for name in ("mean_wall_s", "mean_db_time_share", "mean_scenarios_evaluated", "mean_llm_calls"):
        assert by[name]["status"] == "FAIL" and by[name]["kind"] == "soft"


def test_dropped_or_unread_items_block(gate, thresholds):
    m = gate.gate_metrics(_good_run(), {"scored_items": 2}, ["q-padding"])
    rows, blocked = gate.evaluate(thresholds, m, expected_items=3)
    assert blocked and _rows_by_name(rows)["items_in_langfuse"]["status"] == "FAIL"
    m = gate.gate_metrics(_good_run()[:2], _readback(2), ["q-padding"])
    rows, blocked = gate.evaluate(thresholds, m, expected_items=3)
    assert blocked and _rows_by_name(rows)["items_evaluated"]["status"] == "FAIL"


def test_missing_hard_metric_blocks(gate, thresholds):
    m = gate.gate_metrics(_good_run(), _readback(3), ["q-padding"])
    m["mean_run_cost_usd"] = None  # e.g. the agent stopped reporting cost
    rows, blocked = gate.evaluate(thresholds, m, 3)
    assert blocked and _rows_by_name(rows)["mean_run_cost_usd"]["status"] == "MISSING"


# ----------------------------------------------------------------------- report
def _render(gate, thresholds, run, *, n_dataset=15, url="https://lf.example/run"):
    m = gate.gate_metrics(run, _readback(len(run)), thresholds["must_pass"])
    rows, blocked = gate.evaluate(thresholds, m, len(run))
    md = gate.render(rows, blocked, label="candidate-coarse", versions={P.PLANNER: 2, P.ANALYST: 2},
                     model="claude-opus-5-5", cfg=thresholds["config"], dataset=thresholds["dataset"],
                     run_name="gate · candidate-coarse v2/v2 · 20261126T000000Z", run_url=url,
                     n_items=len(run), n_dataset=n_dataset,
                     questions=gate.per_question_rows(run, ("secret-host.example.cloud",)))
    return md, blocked


def test_render_blocked_report(gate, thresholds):
    run = _good_run()
    run[0] = _result("q-padding", HERO_WANT, {"origin": "LAX", "buffer_minutes": 10})
    md, blocked = _render(gate, thresholds, run)
    assert blocked
    assert md.startswith("## ❌ **GATE FAILED** — label `candidate-coarse` (planner v2, analyst v2)")
    assert "**Blocked on:** `must_pass:q-padding`, `accuracy`" in md
    assert "must not be promoted to `production`" in md
    assert "[Open the run in Langfuse](https://lf.example/run)" in md
    assert "| `q-padding` | ❌ | origin=LAX, buffer_minutes=10 | origin=LAX, buffer_minutes=9 |" in md
    assert "**Smoke run:** 3 of 15 questions" in md
    assert "| `accuracy` | 67% | ≥ 70% | 80% | hard | ❌ |" in md
    assert "| `mean_run_cost_usd` | $0.140 | ≤ $0.220 | $0.144 | hard | ✅ |" in md


def test_render_passed_report_full_run(gate, thresholds):
    run = _good_run()
    md, blocked = _render(gate, thresholds, run, n_dataset=3, url=None)
    assert not blocked and md.startswith("## ✅ **GATE PASSED**")
    assert "Smoke run" not in md and "Open the run" not in md
    assert "All configured hard thresholds met for this pair (planner v2, analyst v2)" in md
    assert "calibration limits" in md and "safe to move" not in md


def test_smoke_pass_never_recommends_promotion(gate, thresholds):
    md, blocked = _render(gate, thresholds, _good_run())
    assert not blocked and "not promotion evidence" in md
    assert "safe to move" not in md


def test_thresholds_mark_historical_calibration_as_provisional(thresholds):
    assert thresholds["calibration"]["status"] == "provisional"


def test_render_lists_soft_warnings(gate, thresholds):
    run = [_result(r.item.id, r.item.expected_output, r.output["answer_values"], wall_ms=200_000.0)
           for r in _good_run()]
    md, blocked = _render(gate, thresholds, run)
    assert not blocked and "Warnings (soft, not blocking): `mean_wall_s`" in md
    assert "| `mean_wall_s` | 200.0 s | ≤ 120.0 s | 66.3 s | soft (warn) | ⚠️ |" in md


def test_errors_never_leak_the_clickhouse_host(gate, thresholds):
    run = _good_run()
    run[1] = _result("q-carrier-month", {"carrier": "WN", "month": 7}, {},
                     error="ConnectError: secret-host.example.cloud:8443 refused")
    md, _ = _render(gate, thresholds, run)
    assert "secret-host" not in md and "<CH_HOST>:8443 refused" in md
    assert gate.scrub_text("a host.x b", ("host.x", "")) == "a <CH_HOST> b"


def test_abort_report_makes_no_claim(gate):
    md = gate.render_abort("prompt 'analytics-agentic-speed-analyst' has no label 'x'", label="x")
    assert md.startswith("## ⏹️ **GATE NOT EVALUATED** — label `x`") and "no claim" in md


def test_write_summary_appends_to_the_step_summary(gate, tmp_path, monkeypatch):
    p = tmp_path / "summary.md"
    p.write_text("### Prompt change\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(p))
    gate.write_summary("## verdict")
    assert p.read_text() == "### Prompt change\n## verdict\n\n"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY")
    gate.write_summary("ignored")  # no-op outside Actions
    assert "ignored" not in p.read_text()


def test_run_name_is_always_unique(gate):
    v = {P.PLANNER: 2, P.ANALYST: 1}
    assert gate.make_run_name("candidate-coarse", v, None, "S") == "gate · candidate-coarse v2/v1 · S"
    assert (gate.make_run_name("production", v, "ci · {label} {version} · #7.1", "S")
            == "ci · production v2/v1 · #7.1 · S")


# ----------------------------------------------------------------------- main(): pre-flight aborts
def test_preflight_abort_exits_1_and_writes_the_summary(gate, tmp_path, monkeypatch):
    p = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(p))

    def boom(a, t):
        raise gate.GateAbort("ClickHouse is not reachable as `scenario_agent`")

    monkeypatch.setattr(gate, "run_gate", boom)
    assert gate.main(["--prompt-label", "candidate-coarse", "--warn-only"]) == 1  # --warn-only never hides it
    assert "GATE NOT EVALUATED" in p.read_text()


@pytest.mark.parametrize("blocked,warn_only,code", [(True, False, 1), (True, True, 0), (False, False, 0)])
def test_exit_codes(gate, monkeypatch, blocked, warn_only, code):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(gate, "run_gate", lambda a, t: ("## verdict", blocked))
    argv = ["--prompt-label", "production"] + (["--warn-only"] if warn_only else [])
    assert gate.main(argv) == code


def test_prompt_versions_aborts_when_a_prompt_lacks_the_label(gate):
    class LF:
        def get_prompt(self, name, label=None, cache_ttl_seconds=None):
            if name == P.ANALYST:
                raise RuntimeError("NotFoundError")
            return SimpleNamespace(version=2)

    with pytest.raises(gate.GateAbort, match="BOTH prompts"):
        gate.prompt_versions(LF(), "candidate-coarse")


def test_gate_passes_resolved_pair_to_every_task(gate, thresholds, monkeypatch):
    from explorer import tracing

    class LF:
        current = 1
        fetches = []

        def get_prompt(self, name, **kw):
            self.fetches.append((name, kw))
            return SimpleNamespace(version=kw.get("version", self.current), is_fallback=False)

        def run_experiment(self, **kw):
            return SimpleNamespace(item_results=_good_run(), dataset_run_id="run", dataset_run_url="url")

        def flush(self):
            pass

    lf, captured = LF(), {}
    monkeypatch.setattr(gate, "load_env", lambda: None)
    monkeypatch.setenv("CH_HOST", "fake.test")
    monkeypatch.setenv("CH_USER", "reader")
    monkeypatch.setattr(tracing, "get_langfuse", lambda: lf)
    monkeypatch.setattr(gate, "check_project", lambda *a: "analytics-agentic-speed")

    def probe(settings):
        lf.current = 2  # a label move after resolution must not alter the task's pair
        return 231000000

    def task(**kw):
        captured.update(kw)
        return object()

    monkeypatch.setattr(gate, "check_clickhouse", probe)
    monkeypatch.setattr(gate, "load_items", lambda *a: ([r.item for r in _good_run()], 3))
    monkeypatch.setattr(gate.rx, "make_task", task)
    monkeypatch.setattr(gate.rx, "read_back", lambda *a, **kw: _readback(3))
    a = SimpleNamespace(prompt_label="staging", model=None, project_name="analytics-agentic-speed",
                        limit=None, run_name=None, item_timeout=1, concurrency=3, readback_wait=0)
    md, blocked = gate.run_gate(a, thresholds)
    assert not blocked and "planner v1, analyst v1" in md
    assert captured["cfg"].prompt_versions == {P.PLANNER: 1, P.ANALYST: 1}
    assert lf.current == 2
    assert sum("label" in kw for _, kw in lf.fetches) == 2  # exactly once per prompt


def test_unpriced_gate_model_aborts_before_any_service_call(gate, thresholds, monkeypatch):
    monkeypatch.setattr(gate, "load_env", lambda: None)
    with pytest.raises(gate.GateAbort, match="No pricing configured"):
        gate.run_gate(SimpleNamespace(prompt_label="staging", model="unpriced-model"), thresholds)


def test_check_project_refuses_another_project(gate):
    lf = SimpleNamespace(auth_check=lambda: True,
                         api=SimpleNamespace(projects=SimpleNamespace(
                             get=lambda: SimpleNamespace(data=[SimpleNamespace(name="real-estate")]))))
    with pytest.raises(gate.GateAbort, match="real-estate"):
        gate.check_project(lf, "analytics-agentic-speed")


# ----------------------------------------------------------------------- the candidate prompt
@pytest.fixture(scope="module")
def seeder():
    return _load_script("seed_candidate_prompt")


def test_candidate_is_coarse_and_compiles_like_production(seeder):
    texts = seeder.CANDIDATES["candidate-coarse"]["texts"]()
    assert set(texts) == {P.PLANNER, P.ANALYST}  # the gate needs the label on both
    for name, text in texts.items():
        assert text != P.PROMPTS[name]
        assert seeder.variables(text) == seeder.variables(P.PROMPTS[name])  # same {{vars}} as production
        assert "Grid policy" in text and "even when" in text  # a rule, not a hint the model overrides
        assert "5-minute increments" in text
    assert "prefer covering the space" not in texts[P.PLANNER]
    assert "steps of 5" in texts[P.PLANNER] and "never finer" in texts[P.PLANNER]
    assert "smallest multiple of 5" in texts[P.ANALYST]
    # production's two pointers to 1-minute steps are gone
    assert "1-minute buffers instead of 5" not in texts[P.ANALYST]
    assert "whole minute in the interval that matters" not in texts[P.ANALYST]
    # only the resolution changed: the rest of production's rules are intact
    assert "It must return EXACTLY ONE ROW" in texts[P.PLANNER]
    assert "When you answer:" in texts[P.ANALYST]


def test_candidate_never_gets_a_production_label(seeder):
    assert seeder.labels_for("candidate-coarse") == ["candidate-coarse"]
    for bad in ("production", "latest", "previous-production", "Has Space"):
        with pytest.raises(ValueError):
            seeder.labels_for(bad)


def test_candidate_edit_stops_when_production_changed(seeder):
    with pytest.raises(ValueError, match="anchor"):
        seeder.coarse_planner("a rewritten production prompt")
    with pytest.raises(ValueError, match="anchor"):
        seeder.edit("x x", "x", "y")  # an ambiguous anchor is refused too


# ----------------------------------------------------------------------- CI wiring
def _workflow():
    if not WORKFLOW.exists():
        pytest.skip("not in the monorepo checkout")
    wf = yaml.safe_load(WORKFLOW.read_text())
    return wf, wf.get("on", wf.get(True))  # YAML 1.1 reads the bare key `on` as True


def test_workflow_runs_the_gate_on_both_triggers(seeder):
    wf, on = _workflow()
    assert on["repository_dispatch"]["types"] == ["analytics-agentic-speed-prompt-update"]
    opts = on["workflow_dispatch"]["inputs"]["prompt_label"]["options"]
    assert "candidate-coarse" in opts and "production" in opts
    steps = wf["jobs"]["gate"]["steps"]
    run = "\n".join(s.get("run", "") for s in steps)
    assert "python scripts/prompt_gate.py --prompt-label" in run and "github.run_attempt" in run
    assert wf["jobs"]["gate"]["defaults"]["run"]["working-directory"] == "demos/analytics-agentic-speed"
    assert P.PLANNER in run and P.ANALYST in run
    deploy_if = wf["jobs"]["deploy"]["if"]
    assert "is_production == 'true'" in deploy_if and "skip == ''" in deploy_if


def test_workflow_secrets_are_documented_and_demo_prefixed():
    _workflow()
    text = WORKFLOW.read_text()
    secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    readme = (DEMO / "cicd" / "README.md").read_text()
    assert secrets, "the gate needs secrets"
    for s in secrets:
        assert s in readme, f"secret {s} is not documented in cicd/README.md"
        assert s.startswith("AGENTIC_SPEED_") or s == "ANTHROPIC_API_KEY"
    assert {"AGENTIC_SPEED_CH_HOST", "AGENTIC_SPEED_CH_USER", "AGENTIC_SPEED_CH_PASSWORD",
            "AGENTIC_SPEED_LANGFUSE_PUBLIC_KEY", "AGENTIC_SPEED_LANGFUSE_SECRET_KEY"} <= secrets
    assert "CH_ADMIN" not in text  # the gate runs as the read-only agent user


def test_demo_integrity_resolves_this_demos_requirements():
    if not INTEGRITY.exists():
        pytest.skip("not in the monorepo checkout")
    # The deps-resolve matrix is built from the demo manifests: every demo with a
    # demo.yaml and a requirements.txt is resolved. So this demo is covered when it
    # has both and the matrix still comes from the manifests job.
    jobs = yaml.safe_load(INTEGRITY.read_text())["jobs"]
    matrix = jobs["deps-resolve"]["strategy"]["matrix"]["demo"]
    assert "needs.manifests.outputs" in str(matrix)
    assert (DEMO / "demo.yaml").is_file() and (DEMO / "requirements.txt").is_file()
