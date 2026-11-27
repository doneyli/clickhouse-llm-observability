"""seed_all.py offline: where it runs, its exit code, and that verify_demo expects what the
seeders create. No Docker, no Langfuse, no model calls."""
from __future__ import annotations

import pytest

import prompts as P
import queries
import seed_all as s
import seed_dataset
import seed_monitors
import seed_prompts
import verify_demo as v


def test_in_container_follows_the_marker_files(tmp_path):
    marker = tmp_path / ".dockerenv"
    assert s.in_container((str(marker),)) is False
    marker.write_text("")
    assert s.in_container((str(marker),)) is True


def test_host_run_delegates_to_the_container(monkeypatch):
    calls = []
    monkeypatch.setattr(s, "in_container", lambda: False)
    monkeypatch.setattr(s.subprocess, "call", lambda cmd, cwd, env: calls.append((cmd, cwd, env)) or 7)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    assert s.main([]) == 7                                  # the container's exit code
    cmd, cwd, env = calls[0]
    assert cmd == s.CONTAINER_CMD and cwd == str(s.REPO_ROOT)
    assert "LANGFUSE_PUBLIC_KEY" not in env                 # shell keys never reach compose


def test_missing_docker_is_a_failure(monkeypatch):
    def no_docker(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(s, "in_container", lambda: False)
    monkeypatch.setattr(s.subprocess, "call", no_docker)
    assert s.main([]) == 1


def test_missing_keys_fail_instead_of_skipping(monkeypatch, capsys):
    monkeypatch.setattr(s, "in_container", lambda: True)
    import langfuse_config as lf
    monkeypatch.setattr(lf, "is_langfuse_enabled", lambda: False)
    assert s.main([]) == 1
    assert "nothing can be seeded" in capsys.readouterr().out


def test_a_failing_seeder_fails_the_run_but_the_rest_still_run(monkeypatch, capsys):
    import langfuse_config as lf
    ran = []
    monkeypatch.setattr(lf, "is_langfuse_enabled", lambda: True)

    def boom():
        ran.append("prompts")
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(seed_prompts, "main", boom)
    monkeypatch.setattr(seed_dataset, "main", lambda: ran.append("dataset") or 0)
    monkeypatch.setattr(seed_monitors, "main", lambda: ran.append("monitors") or 0)
    assert s.main(["--local"]) == 1
    assert ran == ["prompts", "dataset", "monitors"]
    assert "FAILED: prompts" in capsys.readouterr().out


@pytest.mark.parametrize("key", list(v.EXPECTED_PROMPTS))
def test_verify_expects_the_prompts_seed_creates(key):
    seeded = {
        (P.SYSTEM_PROMPT_NAME, P.V1_LABEL): [],
        (P.SYSTEM_PROMPT_NAME, P.PRODUCTION_LABEL): [P.V2_LABEL],
        (P.GOAL_PROMPT_NAME, P.PRODUCTION_LABEL): [],
    }
    assert set(v.EXPECTED_PROMPTS) == set(seeded)
    assert v.EXPECTED_PROMPTS[key] == seeded[key]


def test_verify_expects_the_dataset_and_monitors_seed_creates():
    assert v.DATASET_NAME == seed_dataset.DATASET_NAME
    assert v.DATASET_ITEMS == [f"qt-{q}" for q in ("q1", "q2", "q3")]
    assert set(("q1", "q2", "q3")) <= set(queries.CATALOG)
    assert v.MONITORS == [m["name"] for m in seed_monitors.MONITORS]


def test_verify_trace_name_matches_the_loop():
    import agent_loop
    assert v.TRACE_NAME == agent_loop.TRACE_NAME
