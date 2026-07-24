"""Backstops trip at exact boundaries; kill sentinel is honored. LLM/DB-free."""

import time
import types

import pytest

import budget
import checkpoint


def make_state(tmp_path, *, turn=1, cost=0.0, t0=None, sigint=False, run_id="run-test"):
    # budget.check() reads .sigint/.run_id/.turn/.cost_usd/.t0 — a namespace suffices.
    return types.SimpleNamespace(
        run_id=run_id, turn=turn, cost_usd=cost,
        t0=t0 if t0 is not None else time.monotonic(), sigint=sigint,
    )


@pytest.fixture(autouse=True)
def _tmp_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("TUNER_CHECKPOINT_DIR", str(tmp_path))
    return tmp_path


def test_no_trip_within_limits(tmp_path):
    caps = budget.Caps(max_turns=15, max_budget_usd=0.75, watchdog_s=600)
    st = make_state(tmp_path, turn=15, cost=0.74)
    assert budget.check(st, caps) is None


def test_max_turns_boundary(tmp_path):
    caps = budget.Caps(max_turns=15, max_budget_usd=999, watchdog_s=999999)
    # Turn == max_turns is still allowed to run; the (max_turns+1)-th trips.
    assert budget.check(make_state(tmp_path, turn=15), caps) is None
    assert budget.check(make_state(tmp_path, turn=16), caps) == "error_max_turns"


def test_max_budget_boundary(tmp_path):
    caps = budget.Caps(max_turns=999, max_budget_usd=0.75, watchdog_s=999999)
    assert budget.check(make_state(tmp_path, cost=0.7499), caps) is None
    assert budget.check(make_state(tmp_path, cost=0.75), caps) == "error_max_budget_usd"
    assert budget.check(make_state(tmp_path, cost=2.14), caps) == "error_max_budget_usd"


def test_watchdog_boundary(tmp_path):
    caps = budget.Caps(max_turns=999, max_budget_usd=999, watchdog_s=600)
    assert budget.check(make_state(tmp_path, t0=time.monotonic()), caps) is None
    old = time.monotonic() - 601
    assert budget.check(make_state(tmp_path, t0=old), caps) == "error_watchdog"


def test_kill_sentinel_honored(tmp_path):
    caps = budget.Caps()
    st = make_state(tmp_path, run_id="run-killme")
    assert budget.check(st, caps) is None
    open(checkpoint.kill_path("run-killme"), "w").close()
    assert budget.check(st, caps) == "killed"


def test_sigint_flag_honored(tmp_path):
    caps = budget.Caps()
    st = make_state(tmp_path, sigint=True)
    assert budget.check(st, caps) == "killed"


def test_kill_precedence_over_caps(tmp_path):
    # Kill switch wins even when a cap is also exceeded.
    caps = budget.Caps(max_turns=1, max_budget_usd=0.01, watchdog_s=1)
    st = make_state(tmp_path, turn=99, cost=99, sigint=True)
    assert budget.check(st, caps) == "killed"


def test_cost_of_sonnet():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    # Sonnet: $3/M in + $15/M out = $18.
    assert budget.cost_of(usage, "claude-sonnet-4-6") == pytest.approx(18.0, rel=1e-6)


def test_cost_of_unknown_model_defaults_sonnet():
    usage = types.SimpleNamespace(input_tokens=1000, output_tokens=0,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0)
    assert budget.cost_of(usage, "some-future-model") == pytest.approx(0.003, rel=1e-6)
