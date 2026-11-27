"""seed_all.py offline: the env is settled before any step (host: root .env only; container:
compose env), every step's exit code reaches the final one, the host-only judge step is
explicit, and the manifest's own command line, from the repo root, fails cleanly without a
root .env. Steps are fake commands; nothing is seeded."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import seed_all as sa  # noqa: E402
import verify_demo as v  # noqa: E402

GOOD_ENV = "LANGFUSE_PUBLIC_KEY=pk-root\nLANGFUSE_SECRET_KEY=sk-root\nLANGFUSE_BASE_URL=http://localhost:3001\n"
HOSTILE = {"LANGFUSE_PUBLIC_KEY": "pk-other", "LANGFUSE_SECRET_KEY": "sk-other",
           "LANGFUSE_HOST": "https://cloud.example", "PATH": os.environ.get("PATH", "")}


class Runner:
    """Records each step; returns the exit code scripted for its file name."""

    def __init__(self, codes=None, raise_for=()):
        self.codes, self.raise_for, self.calls = codes or {}, raise_for, []

    def __call__(self, argv, cwd, env):
        name = os.path.basename(argv[-1])
        self.calls.append((name, env))
        if name in self.raise_for:
            raise OSError(f"cannot start {name}")
        return self.codes.get(name, 0)


@pytest.fixture
def host(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(GOOD_ENV)
    monkeypatch.setattr(v, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sa, "in_container", lambda env: False)
    return dict(HOSTILE)


def test_host_loads_the_root_env_and_runs_every_step_with_it(host):
    runner = Runner()
    assert sa.main([], environ=host, runner=runner) == 0
    assert [c[0] for c in runner.calls] == ["seed_prompts.py", "seed_datasets.py", "seed_evaluators.sh"]
    for _, env in runner.calls:
        assert env["LANGFUSE_PUBLIC_KEY"] == "pk-root" and env["LANGFUSE_HOST"] == "http://localhost:3001"


def test_missing_root_env_fails_before_any_step(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sa, "in_container", lambda env: False)
    runner = Runner()
    environ = dict(HOSTILE)
    assert sa.main([], environ=environ, runner=runner) == 1
    assert runner.calls == [] and "not found" in capsys.readouterr().out
    assert "LANGFUSE_PUBLIC_KEY" not in environ  # the hostile key is gone, not used


def test_partial_root_env_fails_before_any_step(host, tmp_path):
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=pk-root\n")
    runner = Runner()
    assert sa.main([], environ=host, runner=runner) == 1 and runner.calls == []


@pytest.mark.parametrize("failing", ["seed_prompts.py", "seed_datasets.py", "seed_evaluators.sh"])
def test_a_failing_step_fails_the_seed_but_the_others_still_run(host, capsys, failing):
    runner = Runner(codes={failing: 3})
    assert sa.main([], environ=host, runner=runner) == 1
    assert len(runner.calls) == 3
    out = capsys.readouterr().out
    assert "FAILED (exit 3)" in out and "Seed INCOMPLETE" in out and "Seed complete" not in out


def test_a_step_that_cannot_start_fails_the_seed(host):
    assert sa.main([], environ=host, runner=Runner(raise_for={"seed_datasets.py"})) == 1


def test_with_traces_adds_the_live_run(host):
    runner = Runner()
    assert sa.main(["--with-traces"], environ=host, runner=runner) == 0
    assert runner.calls[-1][0] == "run_live_demo.py"


def test_container_cannot_seed_judges_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(sa, "in_container", lambda env: True)
    environ = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_HOST": "http://langfuse-web:3000"}
    runner = Runner()
    assert sa.main([], environ=environ, runner=runner) == 1
    assert [c[0] for c in runner.calls] == ["seed_prompts.py", "seed_datasets.py"]
    out = capsys.readouterr().out
    assert "not run here" in out and sa.HOST_SEED in out
    assert sa.main(["--skip-judges"], environ=environ, runner=Runner()) == 0
    assert "skipped  Seed managed judges (--skip-judges)" in capsys.readouterr().out


def test_the_manifest_command_from_the_repo_root(tmp_path):
    """`commands.seed` as written, from the repo root, with hostile shell keys: with no root
    .env it exits 1 before any request; with one it gets past the env check. `--help` is used
    in the second case so nothing is seeded."""
    env = dict(HOSTILE, LANGFUSE_BASE_URL="https://cloud.example")
    env.pop(v.CONTAINER_FLAG, None)
    if v.in_container(env) or (v.REPO_ROOT / ".env").is_file():
        cmd = [sys.executable, "demos/cluster-health-investigator/scripts/seed_all.py", "--help"]
        out = subprocess.run(cmd, cwd=v.REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0 and "--skip-judges" in out.stdout
        return
    cmd = [sys.executable, "demos/cluster-health-investigator/scripts/seed_all.py"]
    out = subprocess.run(cmd, cwd=v.REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "not found" in out.stdout and "Seed NOT run" in out.stdout
    assert "=== Seed prompts ===" not in out.stdout
