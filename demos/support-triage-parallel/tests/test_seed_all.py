"""seed_all.py offline: which env the seeders see on the host and in the container."""
from __future__ import annotations

import seed_all
import verify_demo


def test_host_run_uses_the_root_env_over_shell_keys(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\n"
                                   "LANGFUSE_BASE_URL=http://localhost:3001\n")
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    env = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "LANGFUSE_HOST": "https://shell.example", "PATH": "/bin"}
    assert seed_all.load_root_env(env) is True
    assert env["LANGFUSE_PUBLIC_KEY"] == "pk-file"
    assert env["LANGFUSE_HOST"] == "http://localhost:3001"   # seed_prompts.py reads LANGFUSE_HOST
    assert env["PATH"] == "/bin"


def test_root_env_host_is_kept_when_set(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LANGFUSE_BASE_URL=http://a:3001\nLANGFUSE_HOST=http://b:3001\n")
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    env = {}
    seed_all.load_root_env(env)
    assert env["LANGFUSE_HOST"] == "http://b:3001"


def test_container_run_keeps_the_compose_env(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)   # no .env: the demo folder is /app
    env = {"LANGFUSE_PUBLIC_KEY": "pk-compose", "LANGFUSE_HOST": "http://langfuse-web:3000"}
    assert seed_all.load_root_env(env) is False
    assert env == {"LANGFUSE_PUBLIC_KEY": "pk-compose", "LANGFUSE_HOST": "http://langfuse-web:3000"}
