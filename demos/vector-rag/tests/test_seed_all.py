"""seed_all.py offline: the root .env is the only source of keys, a missing or partial one
fails before the helper runs, the helper gets the right command, directory and URL, and
every helper or launch failure reaches the exit code. One test runs the real root helper
in-process behind a fake Prompts API. No network, no Langfuse, no model calls."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import urllib.error
import urllib.parse

import pytest

import seed_all as s
import verify_demo as v


@pytest.fixture(autouse=True)
def restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\n"
                    "LANGFUSE_BASE_URL=http://localhost:3001/\nANTHROPIC_MODEL=claude-sonnet-4-6\n")
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    return path


class Recorder:
    def __init__(self, code=0, exc=None):
        self.code, self.exc, self.calls = code, exc, []

    def __call__(self, cmd, cwd, env):
        self.calls.append((cmd, cwd, env))
        if self.exc:
            raise self.exc
        return self.code


def test_runs_the_root_helper_for_this_demos_prompt_only(env_file, capsys):
    rec = Recorder()
    assert s.main([], run=rec) == 0
    (cmd, cwd, env), = rec.calls
    assert cmd[1:] == [str(v.REPO_ROOT / "scripts" / "seed-app-prompts.py"), "--only", "vector-rag-generation"]
    assert cwd == str(v.REPO_ROOT)  # commands.seed runs from the repo root for a container demo
    assert env["LANGFUSE_BASE_URL"] == env["LANGFUSE_HOST"] == "http://localhost:3001"


def test_dry_run_is_passed_through(env_file):
    rec = Recorder()
    assert s.main(["--dry-run"], run=rec) == 0
    assert rec.calls[0][0][-1] == "--dry-run"


def test_hostile_shell_exports_never_reach_the_helper(env_file, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-another-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://elsewhere.example")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "shell-only")
    rec = Recorder()
    assert s.main([], run=rec) == 0
    env = rec.calls[0][2]
    assert env["LANGFUSE_PUBLIC_KEY"] == "pk-file"
    assert env["LANGFUSE_HOST"] == "http://localhost:3001"
    assert "LANGFUSE_TRACING_ENVIRONMENT" not in env


def test_missing_env_file_fails_without_running_the_helper(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "absent.env")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")
    rec = Recorder()
    assert s.main([], run=rec) == 1
    assert rec.calls == []
    assert "no env file" in capsys.readouterr().err


@pytest.mark.parametrize("text, missing", [
    ("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_BASE_URL=http://x\n", "LANGFUSE_SECRET_KEY"),
    ("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n", "LANGFUSE_BASE_URL"),
    ("LANGFUSE_PUBLIC_KEY=\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_HOST=http://x\n", "LANGFUSE_PUBLIC_KEY"),
])
def test_partial_env_fails_without_running_the_helper(tmp_path, monkeypatch, capsys, text, missing):
    path = tmp_path / ".env"
    path.write_text(text)
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")  # must not fill the gap
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://shell.example")
    rec = Recorder()
    assert s.main([], run=rec) == 1
    assert rec.calls == []
    assert missing in capsys.readouterr().err


def test_langfuse_host_alone_is_accepted_and_normalised(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_HOST=https://cloud.example/\n")
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    rec = Recorder()
    assert s.main([], run=rec) == 0
    assert rec.calls[0][2]["LANGFUSE_BASE_URL"] == "https://cloud.example"


def test_non_container_manifest_is_refused(env_file, monkeypatch, capsys):
    monkeypatch.setattr(v, "manifest_value", lambda text, section, key: "standalone")
    rec = Recorder()
    assert s.main([], run=rec) == 1
    assert rec.calls == []


def test_helper_failure_is_the_exit_code(env_file, capsys):
    assert s.main([], run=Recorder(code=1)) == 1
    assert "exited 1: not seeded" in capsys.readouterr().err


def test_helper_that_cannot_start_is_a_failure(env_file, capsys):
    assert s.main([], run=Recorder(exc=FileNotFoundError("no python"))) == 1
    assert "could not run" in capsys.readouterr().err


def test_seeds_exactly_what_verify_checks():
    assert s.PROMPT_NAMES == list(v.EXPECTED_PROMPTS) == ["vector-rag-generation"]


# --- The real helper, in-process, behind a fake Prompts API ---------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_end_to_end_with_the_real_helper_creates_then_leaves_unchanged(env_file, capsys):
    spec = importlib.util.spec_from_file_location("seed_app_prompts_e2e", s.HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    store = {}   # name -> served version
    posts = []

    def opener(req, timeout=None):
        url = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(url.query))
        assert f"{url.scheme}://{url.netloc}" == "http://localhost:3001"
        if req.get_method() == "POST":
            body = json.loads(req.data)
            posts.append(body["name"])
            store[body["name"]] = dict(body, version=len(posts))
            return _Resp(json.dumps(store[body["name"]]).encode())
        if url.path == "/api/public/v2/prompts":
            assert set(q) == {"name", "limit"}
            return _Resp(json.dumps({"data": [], "meta": {}}).encode())
        name = urllib.parse.unquote(url.path.rsplit("/", 1)[1])
        assert set(q) == {"label"}
        if name not in store:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
        return _Resp(json.dumps(store[name]).encode())

    def run(cmd, cwd, env):
        return helper.main(cmd[2:], environ=env, opener=opener)

    assert s.main([], run=run) == 0
    assert posts == ["vector-rag-generation"]  # the text-to-sql prompts are not touched
    assert "1 created" in capsys.readouterr().out
    assert s.main([], run=run) == 0
    assert posts == ["vector-rag-generation"]  # an unchanged run writes nothing
    assert "1 unchanged" in capsys.readouterr().out
