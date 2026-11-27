"""explorer.config.load_env: the demo's .env is the only source of LANGFUSE_*. No network."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from explorer import config

FULL = """\
LANGFUSE_PUBLIC_KEY=pk-lf-demo
LANGFUSE_SECRET_KEY=sk-lf-demo
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
CH_HOST=svc.example.clickhouse.cloud
"""


@pytest.fixture
def hostile(monkeypatch):
    for k in list(os.environ):
        if k.startswith("LANGFUSE_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-OTHER")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-OTHER")
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "other")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("EXPLORER_TARGET", raising=False)
    # load_env writes into os.environ; monkeypatch restores what it set, so record the rest
    for k in ("LANGFUSE_BASE_URL", "CH_HOST"):
        monkeypatch.setenv(k, "placeholder")
        monkeypatch.delenv(k)


def write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text)
    return p


def test_file_wins_and_every_inherited_langfuse_var_is_dropped(hostile, tmp_path):
    src = config.load_env(env_file=write(tmp_path, FULL))
    assert src.endswith(".env")
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-demo"
    assert os.environ["LANGFUSE_BASE_URL"] == "https://us.cloud.langfuse.com"
    assert "LANGFUSE_HOST" not in os.environ  # the shell's host can't steer the SDK
    assert "LANGFUSE_TRACING_ENVIRONMENT" not in os.environ


def test_partial_file_never_borrows_the_shell_secret(hostile, tmp_path):
    with pytest.raises(config.EnvError, match="LANGFUSE_SECRET_KEY"):
        config.load_env(env_file=write(tmp_path, FULL.replace("LANGFUSE_SECRET_KEY=sk-lf-demo\n", "")))
    assert os.environ.get("LANGFUSE_SECRET_KEY") != "sk-lf-OTHER"


def test_empty_values_count_as_missing(hostile, tmp_path):
    with pytest.raises(config.EnvError, match="LANGFUSE_PUBLIC_KEY, LANGFUSE_BASE_URL"):
        config.load_env(env_file=write(tmp_path, FULL.replace("pk-lf-demo", "").replace(
            "https://us.cloud.langfuse.com", "")))


def test_host_alone_fills_the_base_url(hostile, tmp_path):
    config.load_env(env_file=write(tmp_path, FULL.replace("LANGFUSE_BASE_URL", "LANGFUSE_HOST")))
    assert os.environ["LANGFUSE_BASE_URL"] == "https://us.cloud.langfuse.com"


def test_missing_file_fails_outside_ci(hostile, tmp_path):
    with pytest.raises(config.EnvError, match="no .env at"):
        config.load_env(env_file=tmp_path / ".env")
    assert issubclass(config.EnvError, SystemExit)  # a CLI exits 1 with the message, no traceback


def test_missing_file_drops_the_shell_keys_too(hostile, tmp_path):
    with pytest.raises(config.EnvError):
        config.load_env(env_file=tmp_path / ".env")
    assert not [k for k in os.environ if k.startswith("LANGFUSE_")]


@pytest.mark.parametrize("value", ["true", "", "false", "1"])
def test_seed_and_verify_mode_needs_the_file_even_on_github_actions(hostile, tmp_path, monkeypatch, value):
    monkeypatch.setenv("GITHUB_ACTIONS", value)
    with pytest.raises(config.EnvError, match="no .env at"):
        config.load_env(env_file=tmp_path / ".env")
    assert "LANGFUSE_PUBLIC_KEY" not in os.environ  # the runner's credentials are dropped, not used


def test_partial_file_on_github_actions_never_borrows_the_step_env(hostile, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(config.EnvError, match="LANGFUSE_SECRET_KEY"):
        config.load_env(env_file=write(tmp_path, FULL.replace("LANGFUSE_SECRET_KEY=sk-lf-demo\n", "")),
                        ci_step_env=True)
    assert "LANGFUSE_SECRET_KEY" not in os.environ


def test_ci_step_env_mode_uses_the_step_env_on_a_runner_with_no_file(hostile, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    assert config.load_env(env_file=tmp_path / ".env", ci_step_env=True) == "the GitHub Actions step env"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-OTHER"  # the workflow's secrets, by design


@pytest.mark.parametrize("value", ["", "false", "1"])
def test_ci_step_env_mode_identifies_the_runner_positively(hostile, tmp_path, monkeypatch, value):
    monkeypatch.setenv("GITHUB_ACTIONS", value)
    with pytest.raises(config.EnvError, match="no .env at"):
        config.load_env(env_file=tmp_path / ".env", ci_step_env=True)
    assert "LANGFUSE_PUBLIC_KEY" not in os.environ


def test_only_the_workflow_command_opts_in_to_the_step_env():
    import re
    demo = config.DEMO_DIR
    callers = {str(f.relative_to(demo)) for f in [*demo.glob("*.py"), *demo.glob("scripts/*.py"),
                                                  *demo.glob("webapp/*.py")]
               if re.search(r"load_env\([^)]*ci_step_env=True", f.read_text())}
    assert callers == {"scripts/prompt_gate.py"}
    workflow = (demo.parent.parent / ".github/workflows/analytics-agentic-speed-prompt-gate.yml").read_text()
    assert "scripts/prompt_gate.py" in workflow


@pytest.mark.parametrize("url", ["http://localhost:invalid", "localhost:3000", "ftp://x.example", "https://"])
def test_an_unusable_base_url_fails_before_any_client(hostile, tmp_path, url):
    with pytest.raises(config.EnvError, match="LANGFUSE_BASE_URL"):
        config.load_env(env_file=write(tmp_path, FULL.replace("https://us.cloud.langfuse.com", url)))


def unreadable(tmp_path, monkeypatch):
    """A .env that exists but can't be opened (mode 000 is readable as root, so fake the open)."""
    p = write(tmp_path, FULL)
    real_open = Path.open

    def deny(self, *a, **k):
        if self == p:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *a, **k)
    monkeypatch.setattr(Path, "open", deny)
    return p


def test_an_unreadable_file_is_an_env_error(hostile, tmp_path, monkeypatch):
    with pytest.raises(config.EnvError, match="cannot read .*PermissionError"):
        config.load_env(env_file=unreadable(tmp_path, monkeypatch))
    assert "LANGFUSE_PUBLIC_KEY" not in os.environ


def test_a_file_that_is_not_utf8_is_an_env_error(hostile, tmp_path):
    p = tmp_path / ".env"
    p.write_bytes(b"LANGFUSE_PUBLIC_KEY=\xff\xfe\n")
    with pytest.raises(config.EnvError, match="cannot read .*UnicodeDecodeError"):
        config.load_env(env_file=p)


def test_clickhouse_only_scripts_need_no_langfuse_keys(hostile, tmp_path):
    config.load_env(require_langfuse=False, env_file=write(tmp_path, "CH_HOST=svc\n"))
    assert os.environ["CH_HOST"] == "svc" and "LANGFUSE_PUBLIC_KEY" not in os.environ
