"""seed_prompts.py reconcile against the request-level fake: every owned (prompt, label) on its
own, no writes when unchanged, labels moved rather than versions added when a matching
version exists, config-only drift, other labels preserved, failures propagated. Offline."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, os.path.dirname(HERE))

import seed_prompts as sp  # noqa: E402
import verify_demo as v  # noqa: E402
from fake_langfuse import FakeLangfuse  # noqa: E402

CONFIG = {"model": "claude-sonnet-4-6", "temperature": 0.2}


@pytest.fixture
def fake(monkeypatch):
    return FakeLangfuse().install(monkeypatch)


def lf():
    return v.Langfuse("https://lf.example", "pk", "sk")


def labels(fake, name):
    return {lab: n for n, p in fake.prompts[name].items() for lab in p["labels"]}


def test_every_owned_label_is_one_verify_checks():
    owned = {(name, w["label"]) for name, wants in sp.desired().items() for w in wants}
    assert owned == v.EXPECTED_PROMPTS


def test_fresh_project_creates_each_label_in_the_original_version_order(fake):
    out = sp.reconcile(lf(), sp.desired(), CONFIG)
    assert set(out.values()) == {"created"}
    assert labels(fake, "cluster-health-planner") == {"fault-overplan": 1, "production": 2,
                                                      "candidate-scoped-decomposition": 3}
    p = fake.prompts["cluster-health-planner"][2]
    assert p["type"] == "text" and p["prompt"] == sp.PLANNER_FALLBACK and p["config"] == CONFIG


def test_unchanged_reseed_writes_nothing(fake):
    sp.reconcile(lf(), sp.desired(), CONFIG)
    fake.calls.clear()
    out = sp.reconcile(lf(), sp.desired(), CONFIG)
    assert set(out.values()) == {"unchanged"}
    assert fake.writes() == []


def test_a_missing_secondary_label_is_moved_back_without_a_new_version(fake):
    sp.reconcile(lf(), sp.desired(), CONFIG)
    fake.prompts["cluster-health-planner"][3]["labels"] = []  # someone removed the candidate label
    fake.calls.clear()
    out = sp.reconcile(lf(), sp.desired(), CONFIG)
    assert out[("cluster-health-planner", "candidate-scoped-decomposition")] == "updated"
    assert fake.writes() == [("PATCH", "/api/public/v2/prompts/cluster-health-planner/versions/3")]
    assert max(fake.prompts["cluster-health-planner"]) == 3


def test_changed_text_creates_a_version_and_keeps_other_labels(fake):
    fake.add_prompt("cluster-health-worker", "old worker text", ["production", "staging"], CONFIG)
    out = sp.reconcile(lf(), {"cluster-health-worker": sp.desired()["cluster-health-worker"]}, CONFIG)
    assert out == {("cluster-health-worker", "production"): "created"}
    assert labels(fake, "cluster-health-worker") == {"staging": 1, "production": 2}
    assert fake.prompts["cluster-health-worker"][2]["prompt"] == sp.WORKER_FALLBACK


def test_config_only_drift_is_repaired(fake):
    sp.reconcile(lf(), sp.desired(), CONFIG)
    fake.prompts["cluster-health-synthesizer"][1]["config"] = {"model": "other", "temperature": 0.9}
    out = sp.reconcile(lf(), sp.desired(), CONFIG)
    assert out[("cluster-health-synthesizer", "production")] == "created"
    assert fake.prompts["cluster-health-synthesizer"][2]["config"] == CONFIG
    assert [o for k, o in out.items() if k[0] != "cluster-health-synthesizer"] == ["unchanged"] * 4


def test_a_label_on_a_chat_prompt_is_not_accepted_as_matching():
    assert not sp.matches({"type": "chat", "prompt": "x", "config": {}}, "x", {})
    assert sp.matches({"type": "text", "prompt": "x", "config": None}, "x", {})


def test_dry_run_writes_nothing(fake):
    out = sp.reconcile(lf(), sp.desired(), CONFIG, dry_run=True)
    assert set(out.values()) == {"created"} and fake.writes() == []


def test_partial_failure_is_reported_and_the_rest_still_reconciled(fake, monkeypatch, capsys):
    fake.fail[("POST", "/api/public/v2/prompts")] = v.ApiError(500, "write failed")
    fake.add_prompt("cluster-health-worker", sp.WORKER_FALLBACK, ["production"], sp.prompt_config())
    monkeypatch.setattr(sp, "prepare_env", lambda: (True, "test env"))
    monkeypatch.setattr(sp, "client_from_env", lf)
    assert sp.main([]) == 1
    out = capsys.readouterr().out
    assert "unchanged cluster-health-worker [production]" in out
    assert "failed    cluster-health-planner [production] HTTP 500: write failed" in out
    assert "4 failed" in out


def test_total_read_failure_fails(fake, monkeypatch):
    fake.fail[("GET", "/api/public/v2/prompts")] = v.ApiError(None, "URLError: refused")
    monkeypatch.setattr(sp, "prepare_env", lambda: (True, "test env"))
    monkeypatch.setattr(sp, "client_from_env", lf)
    assert sp.main([]) == 1


def test_missing_env_fails_before_any_request(monkeypatch, capsys):
    monkeypatch.setattr(sp, "prepare_env", lambda: (False, "/x/.env not found"))
    monkeypatch.setattr(sp, "client_from_env", lambda: pytest.fail("no client without an env"))
    assert sp.main([]) == 1
    assert "Prompts NOT seeded" in capsys.readouterr().out
