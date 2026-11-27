"""score_delegations.py against the request-level fake: runs found and assembled from
observations (never the trace endpoints), one delegation_quality score per worker on that
worker's observation, failures reach the exit code. A fake judge; no model calls."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import score_delegations as sdl  # noqa: E402
import verify_demo as v  # noqa: E402
from fake_langfuse import FakeLangfuse  # noqa: E402

NOW = datetime(2099, 1, 1, 0, 30, tzinfo=timezone.utc)


class Judge:
    def __init__(self):
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("R", (), {"content": '{"score": 0.75, "reason": "fine"}'})()


def lf():
    return v.Langfuse("https://lf.example", "pk", "sk")


@pytest.mark.parametrize("v3_server", [False, True])
def test_assemble_reads_symptom_plan_and_workers_from_observations(monkeypatch, v3_server):
    fake = FakeLangfuse(v3_server=v3_server).install(monkeypatch)
    fake.add_run("t1", ["slow_queries", "query_errors"], rounds=2, symptom="slow dashboard")
    obs = lf().observations({"traceId": "t1", "fromStartTime": "2098-01-01T00:00:00Z",
                             "toStartTime": "2100-01-01T00:00:00Z"}, fields="core,basic,io,metadata")
    run = sdl.assemble(obs)
    assert run["symptom"] == "slow dashboard"
    assert [t["analysis_type"] for t in run["plan"]["tasks"]] == ["slow_queries", "query_errors"]
    assert sorted(w["analysis_type"] for w in run["workers"]) == ["query_errors", "slow_queries"]
    assert all(isinstance(w["output"], dict) for w in run["workers"])  # v2 JSON strings parsed


def test_score_trace_posts_one_score_per_worker_on_its_observation(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.add_run("t1", ["slow_queries", "query_errors"], symptom="slow dashboard")
    judge = Judge()
    assert sdl.score_trace(lf(), judge, "t1", now=NOW) == 2
    posted = [b for m, p, _, b in fake.calls if m == "POST"]
    workers = {o["id"] for o in fake.observations if o["name"] == "worker"}
    assert {b["observationId"] for b in posted} == workers
    assert all(b["name"] == "delegation_quality" and b["value"] == 0.75 and b["traceId"] == "t1" for b in posted)
    assert "slow dashboard" in judge.prompts[0] and "SIBLING analysis_types dispatched alongside it: query_errors" \
        in judge.prompts[0]
    assert not any("/traces" in c[1] for c in fake.calls)


def test_a_run_without_workers_is_an_error(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.add_run("t1", [])
    with pytest.raises(v.ApiError, match="no worker observations"):
        sdl.score_trace(lf(), Judge(), "t1", now=NOW)


def test_recent_runs_are_newest_first(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.add_run("old", ["a"], start="2098-12-31T00:00:00.000Z")
    fake.add_run("new", ["a"], start="2099-01-01T00:00:00.000Z")
    assert sdl.recent_runs(lf(), 5, now=NOW) == ["new", "old"]


def test_main_fails_when_no_run_exists(monkeypatch, capsys):
    FakeLangfuse().install(monkeypatch)
    monkeypatch.setattr(sdl, "prepare_env", lambda: (True, "test env"))
    monkeypatch.setattr(sdl, "client_from_env", lf)
    assert sdl.main([]) == 1
    assert "No investigate-cluster-symptom runs found" in capsys.readouterr().out


def test_main_fails_on_a_read_error_and_on_a_missing_env(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.fail[("GET", "/api/public/v2/observations")] = v.ApiError(500, "boom")
    monkeypatch.setattr(sdl, "prepare_env", lambda: (True, "test env"))
    monkeypatch.setattr(sdl, "client_from_env", lf)
    assert sdl.main([]) == 1
    monkeypatch.setattr(sdl, "prepare_env", lambda: (False, "missing"))
    monkeypatch.setattr(sdl, "client_from_env", lambda: pytest.fail("no client without an env"))
    assert sdl.main([]) == 1
