"""seed_all.py offline: the prompt reconcile decision against a fake Langfuse, the step
wrapper, and that verify expects what seed creates. No network, no Docker, no model calls."""
from __future__ import annotations

import pytest

import seed_all as s
import verify_demo as v

DEFS = s.prompt_definitions()
CONFIG = {"model": "claude-sonnet-4-6", "temperature": 0.3}


class FakeLangfuse:
    def __init__(self, served=None, exists=None, status=404):
        self.served, self.status, self.posts = served, status, []
        self.exists = served is not None if exists is None else exists

    def get(self, path, **params):
        if path == "/api/public/v2/prompts":  # list by name
            return {"data": [{"name": params["name"]}] if self.exists else []}
        if self.served is None:
            raise v.ApiError(self.status, "not found")
        return self.served

    def post(self, path, body):
        self.posts.append(body)


def served(**changes):
    row = {"type": "text", "prompt": DEFS.V2, "config": dict(CONFIG), "version": 2}
    row.update(changes)
    return row


def reconcile(lf, **kw):
    return s.reconcile_prompt(lf, DEFS.NAME, DEFS.V1, DEFS.V2, CONFIG, **kw)


def test_missing_prompt_gets_baseline_then_production():
    lf = FakeLangfuse()
    assert reconcile(lf) == "created"
    assert [p["labels"] for p in lf.posts] == [[], ["production"]]
    assert [p["prompt"] for p in lf.posts] == [DEFS.V1, DEFS.V2]


def test_identical_prompt_is_left_unchanged():
    lf = FakeLangfuse(served())
    assert reconcile(lf) == "unchanged"
    assert lf.posts == []


@pytest.mark.parametrize("change", [{"prompt": "edited in the UI"}, {"config": {"model": "other"}}])
def test_drift_adds_one_production_version(change):
    lf = FakeLangfuse(served(**change))
    assert reconcile(lf) == "updated"
    assert [p["labels"] for p in lf.posts] == [["production"]]


def test_existing_prompt_without_the_label_gets_no_second_baseline():
    lf = FakeLangfuse(served=None, exists=True)
    assert reconcile(lf) == "updated"
    assert [p["prompt"] for p in lf.posts] == [DEFS.V2]


def test_dry_run_writes_nothing():
    lf = FakeLangfuse(served(prompt="drifted"))
    assert reconcile(lf, dry_run=True) == "updated"
    assert lf.posts == []


def test_other_errors_propagate():
    with pytest.raises(v.ApiError):
        reconcile(FakeLangfuse(status=500))


def test_dry_run_step_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(s.subprocess, "run", None)  # any call would raise
    assert s.run_step(["./scripts/seed-agentic-rag-evaluators.sh"], dry_run=True) == "skipped"
    assert "would run" in capsys.readouterr().out


def test_step_result_follows_the_exit_code(monkeypatch):
    class Done:
        def __init__(self, code):
            self.returncode = code

    monkeypatch.setattr(s.subprocess, "run", lambda cmd, cwd: Done(0))
    assert s.run_step(["true"], dry_run=False) == "reconciled"
    monkeypatch.setattr(s.subprocess, "run", lambda cmd, cwd: Done(1))
    assert s.run_step(["false"], dry_run=False) == "failed"


def test_wrapped_seeders_exist():
    for cmd in (s.JUDGES_CMD, s.LIBRECHAT_CMD):
        assert (v.REPO_ROOT / cmd[0]).is_file()
    assert (v.DEMO_DIR / s.INGEST_CMD[-1]).is_file()
    judges = (v.REPO_ROOT / s.JUDGES_CMD[0]).read_text()
    for rule in v.JUDGE_RULES:  # verify reads back the ids the judge seeder upserts
        assert f'"{rule}"' in judges


def test_verify_expects_what_seed_creates(monkeypatch):
    monkeypatch.delenv("GEN_PROMPT_NAME", raising=False)
    assert {s.prompt_definitions().NAME: s.PRODUCTION} == v.EXPECTED_PROMPTS
    graph_src = (v.DEMO_DIR / "graph.py").read_text()
    assert '"agentic-rag-generation"' in graph_src and 'lf.trace_context("agentic-rag"' in graph_src
    for name in v.EXPECTED_OBSERVATIONS:
        assert f'"{name}"' in graph_src
