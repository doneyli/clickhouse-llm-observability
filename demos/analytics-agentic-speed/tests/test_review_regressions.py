"""End-to-end agent boundaries from PR #122's review, with no network or model spend."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from explorer import agent as A, llm as L, prompts as P
from explorer.ch_client import ClickHouseError, QueryResult, QueryStats
from explorer.config import ClickHouseSettings, RunConfig, UnknownModelPriceError
from explorer.evaluators import answer_correct, answer_key_match
from explorer.models import Lever, Plan, Question, RoundRecord, ScenarioOutcome


class Span:
    trace_id = "1" * 32

    def __init__(self):
        self.updates = []
        self.scores = []

    def update(self, **kw):
        self.updates.append(kw)

    def score(self, **kw):
        self.scores.append(kw)

    score_trace = score


class LF:
    def __init__(self):
        self.label_version = 1
        self.fetches = []
        self.spans = []

    def get_prompt(self, name, **kw):
        self.fetches.append((name, kw))
        version = kw.get("version", self.label_version)
        return SimpleNamespace(version=version, is_fallback=False, compile=lambda **vars: f"{name} v{version}")

    @contextmanager
    def start_as_current_observation(self, **kw):
        span = Span()
        self.spans.append((kw, span))
        yield span

    def get_trace_url(self, **kw):
        return None


SQL = ("SELECT avg(ArrDelay <= 15 + {buffer:UInt16}) AS metric, count() AS support "
       "FROM flights.ontime WHERE Origin = {origin:String} AND Year = 2019")
PLAN = {"sql_template": SQL,
        "levers": [{"name": "origin", "ch_type": "String", "values": ["LAX"]},
                   {"name": "buffer", "ch_type": "UInt16", "values": ["9", "10"]}],
        "metric_name": "on_time", "objective": "maximize"}
DECISION = {"decision": "answer", "summary": "LAX needs 9 minutes",
            "best_scenario": [{"name": "origin", "value": "LAX"}, {"name": "buffer", "value": "9"}],
            "answer_values": [{"name": "origin", "value": "LAX"}, {"name": "buffer_minutes", "value": "9"}],
            "metric_value": 0.8507}


@pytest.mark.parametrize("verify_fails", [False, True])
async def test_agent_keeps_pinned_pair_and_rejects_failed_verification(monkeypatch, verify_fails):
    monkeypatch.setitem(DECISION, "summary", "Every airport crosses 85% with at most 29 minutes.")
    lf = LF()
    versions = {name: lf.get_prompt(name, label="staging", cache_ttl_seconds=0).version for name in P.PROMPTS}
    lf.label_version = 2  # labels move after the gate's pre-flight, before any task starts
    used = []

    class LLM:
        def __init__(self, *args):
            self.calls, self.cost_usd, self.cost_known, self.client = 0, 0.0, True, self

        async def structured(self, lf, *, name, prompt, **kw):
            self.calls += 1
            self.cost_usd += 0.05
            used.append((name, prompt.version))
            return PLAN if name == "plan" else DECISION

        async def close(self):
            pass

    class CH:
        settings = ClickHouseSettings("fake.test", "user", "unused")
        allowed_tables = ["flights.ontime"]
        calls = 0

        async def query(self, sql, params, **kw):
            self.calls += 1
            if self.calls == 3 and verify_fails:
                raise ClickHouseError("verification failed")
            return QueryResult(["metric", "support"], ["Float64", "UInt64"], [[0.8507, 1000]],
                               QueryStats(1, 2, 1000, 8000, 1, f"q-{self.calls}"))

    async def profile(*args):
        return "schema", "profile"

    monkeypatch.setattr(A, "LLM", LLM)
    monkeypatch.setattr(A, "profile", profile)
    cfg = RunConfig(prompt_label="staging", prompt_versions=versions)
    events = []
    result = await A.run_question(Question("q-padding", "padding?", ["origin", "buffer_minutes"]),
                                  cfg, ch=CH(), lf=lf, on_progress=events.append)
    assert used == [("plan", 1), ("analyse", 1)]
    assert all(kw == {"version": 1} for _, kw in lf.fetches[2:])  # no label or fallback in pinned tasks
    expected = {"origin": "LAX", "buffer_minutes": 9}
    output = {"answer_values": result.answer_values, "error": result.error, "stop_reason": result.stop_reason}
    assert result.answer_values == expected  # the error occurs after the model answers
    assert result.stop_reason == ("error" if verify_fails else "answered")
    assert answer_correct(input={}, output=output, expected_output=expected).value == (0 if verify_fails else 1)
    assert answer_key_match(input={}, output=output, expected_output=expected).value == (0 if verify_fails else 1)
    assert "Every airport" not in result.summary
    root_output = lf.spans[0][1].updates[-1]["output"]
    assert root_output["summary"] == result.summary == events[-1]["result"]["summary"]
    assert root_output["summary_evidence"] == result.summary_evidence
    assert result.summary_evidence["metric_verified"] is not verify_fails
    if not verify_fails:
        assert result.recommendation_grounded is True


@pytest.mark.parametrize("versions", [{P.PLANNER: 1}, {P.PLANNER: 0, P.ANALYST: 1}])
def test_partial_or_invalid_prompt_pins_fail_closed(versions):
    with pytest.raises(ValueError, match="BOTH"):
        P.get_prompt(LF(), P.PLANNER, label="staging", versions=versions)


def test_pinned_prompt_fetch_cannot_fall_back():
    class BrokenLF:
        def get_prompt(self, name, **kw):
            assert kw == {"version": 1}
            raise RuntimeError("version unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        P.get_prompt(BrokenLF(), P.PLANNER, label="staging", versions={n: 1 for n in P.PROMPTS})


def test_live_prompt_lookup_retains_local_fallback():
    lf = LF()
    P.get_prompt(lf, P.PLANNER, label="production", versions={})
    assert lf.fetches == [(P.PLANNER, {"label": "production", "fallback": P.PLANNER_TEXT})]


def test_unpriced_model_is_rejected_before_client_construction(monkeypatch):
    def unexpected_client():
        pytest.fail("a client was opened before pricing was checked")

    monkeypatch.setattr(L.anthropic, "AsyncAnthropic", unexpected_client)
    with pytest.raises(UnknownModelPriceError, match="No pricing"):
        L.LLM("unpriced-model", "medium")
    with pytest.raises(UnknownModelPriceError):
        L.cost_usd("unpriced-model", SimpleNamespace(input_tokens=1000, output_tokens=1000))


def test_known_model_cost_includes_cache_tokens():
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100,
                            cache_read_input_tokens=2000, cache_creation_input_tokens=500)
    assert L.cost_usd("claude-opus-5-5", usage) == {"input": 0.0069, "output": 0.002, "total": 0.0089}
    assert L.cost_usd("claude-fable-5-2", SimpleNamespace(input_tokens=1000000, output_tokens=1000000))["total"] == 48


async def test_unpriced_served_fallback_records_usage_and_unknown_cost():
    async def respond(**kw):
        return SimpleNamespace(model="unpriced-fallback", usage=SimpleNamespace(input_tokens=100, output_tokens=50))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=respond)))
    llm, lf = L.LLM("claude-opus-5-5", "medium", client=client), LF()
    with pytest.raises(UnknownModelPriceError):
        await llm.structured(lf, name="plan", system="s", user="u", schema={})
    assert llm.cost_known is False
    assert lf.spans[0][1].updates[-1]["metadata"]["pricing_missing"] is True
    assert lf.spans[0][1].updates[-1]["usage_details"] == {"input": 100, "output": 50}


async def test_unpriced_served_model_never_becomes_a_free_run(monkeypatch):
    async def respond(**kw):
        return SimpleNamespace(model="unpriced-fallback", usage=SimpleNamespace(input_tokens=100, output_tokens=50))

    closed = []

    async def close():
        closed.append(True)

    async def profile(*args):
        return "schema", "profile"

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=respond)), close=close)
    monkeypatch.setattr(L.anthropic, "AsyncAnthropic", lambda: client)
    monkeypatch.setattr(A, "profile", profile)
    ch = SimpleNamespace(settings=ClickHouseSettings("fake.test", "reader", "unused"))
    lf, events = LF(), []
    result = await A.run_question(Question("q-padding", "padding?"), RunConfig(), ch=ch, lf=lf,
                                  on_progress=events.append)
    assert result.stop_reason == "error" and result.error.startswith("UnknownModelPriceError:")
    assert result.cost_usd is None and closed == [True]
    assert events[-1]["result"]["cost_usd"] is None
    assert "run_cost_usd" not in {s["name"] for s in lf.spans[0][1].scores}


@pytest.mark.parametrize("bottom,expected_rows", [(0, 8), (5, 13)])
def test_round_history_keeps_only_requested_rows(bottom, expected_rows):
    plan = Plan(SQL, [Lever("buffer", "UInt16", list(range(300)))], "metric", "maximize")
    outcomes = [ScenarioOutcome({"buffer": i}, float(i), 1000, {}, 1, 2, 1000) for i in range(300)]
    rec = RoundRecord(1, plan, outcomes, 10)
    table = A._results_table(rec, top=8, bottom=bottom)
    data = [line for line in table.splitlines() if line and line[0].isdigit() and "\t" in line]
    assert len(data) == expected_rows
    assert ("Worst" in table) == (bottom > 0)


def test_round_history_does_not_duplicate_top_rows_in_small_tail():
    plan = Plan(SQL, [Lever("buffer", "UInt16", list(range(10)))], "metric", "maximize")
    rec = RoundRecord(1, plan, [ScenarioOutcome({"buffer": i}, float(i), 1, {}, 1, 1, 1) for i in range(10)], 10)
    table = A._results_table(rec, top=8, bottom=5)
    data = [line for line in table.splitlines() if line and line[0].isdigit() and "\t" in line]
    assert len(data) == len(set(data)) == 10
