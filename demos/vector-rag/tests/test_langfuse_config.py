"""langfuse_config.py: the on/off switch, prompt fetch and fallback, trace naming, and that a
failure in the caller's code is never swallowed. A fake `langfuse` module stands in for the
SDK, so the tests need neither the package nor a server."""
from __future__ import annotations

import importlib
import re
import sys
import types
from contextlib import contextmanager

import pytest

LANGFUSE_VARS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL")


class FakeClient:
    def __init__(self, prompt=None, fail=False):
        self.prompt, self.fail, self.calls = prompt, fail, []

    def get_prompt(self, name, label):
        self.calls.append((name, label))
        if self.fail:
            raise RuntimeError("404 prompt not found")
        return self.prompt


def load(monkeypatch, enabled: bool, client: FakeClient = None):
    """Import langfuse_config fresh: LANGFUSE_ENABLED is computed at import time."""
    for k in LANGFUSE_VARS:
        monkeypatch.delenv(k, raising=False)
    if enabled:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    propagated = []

    @contextmanager
    def propagate_attributes(**kw):
        propagated.append(kw)
        yield

    fake = types.ModuleType("langfuse")
    fake.get_client = lambda: client
    fake.propagate_attributes = propagate_attributes
    monkeypatch.setitem(sys.modules, "langfuse", fake)
    monkeypatch.delitem(sys.modules, "langfuse_config", raising=False)
    mod = importlib.import_module("langfuse_config")
    return mod, propagated


def test_without_keys_everything_is_a_no_op(monkeypatch):
    lc, propagated = load(monkeypatch, enabled=False)
    assert lc.is_langfuse_enabled() is False
    assert lc.get_langfuse_client() is None
    assert lc.get_managed_prompt("vector-rag-generation") is None  # -> inline fallback
    assert lc.get_langfuse_handler() is None                      # -> callbacks=None, untraced
    with lc.langfuse_trace():
        pass
    assert propagated == []
    lc.flush()  # must not raise


def test_one_key_alone_does_not_enable_tracing(monkeypatch):
    for k in LANGFUSE_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-only")
    monkeypatch.delitem(sys.modules, "langfuse_config", raising=False)
    assert importlib.import_module("langfuse_config").is_langfuse_enabled() is False


def test_managed_prompt_is_fetched_by_the_production_label(monkeypatch):
    prompt = object()
    client = FakeClient(prompt=prompt)
    lc, _ = load(monkeypatch, enabled=True, client=client)
    assert lc.get_managed_prompt("vector-rag-generation") is prompt
    assert client.calls == [("vector-rag-generation", "production")]


def test_unseeded_prompt_returns_none_so_the_app_falls_back(monkeypatch, capsys):
    lc, _ = load(monkeypatch, enabled=True, client=FakeClient(fail=True))
    assert lc.get_managed_prompt("vector-rag-generation") is None
    assert "using local fallback" in capsys.readouterr().out


def test_trace_is_named_vector_rag_and_tagged(monkeypatch):
    lc, propagated = load(monkeypatch, enabled=True, client=FakeClient())
    with lc.langfuse_trace():
        pass
    assert propagated == [{"trace_name": "vector-rag", "tags": ["vector-rag", "demo"]}]


def test_an_error_in_the_traced_block_reaches_the_caller(monkeypatch, capsys):
    """The context manager guards its own setup only. Swallowing the body's exception used to
    print it as a Langfuse failure and raise 'generator didn't stop after throw()'."""
    lc, _ = load(monkeypatch, enabled=True, client=FakeClient())
    with pytest.raises(ZeroDivisionError):
        with lc.langfuse_trace():
            1 / 0
    assert "unavailable" not in capsys.readouterr().out


def test_trace_setup_failure_still_runs_the_block(monkeypatch, capsys):
    lc, _ = load(monkeypatch, enabled=True, client=FakeClient())

    def broken(**kw):
        raise RuntimeError("otel not ready")

    monkeypatch.setattr(sys.modules["langfuse"], "propagate_attributes", broken)
    ran = []
    with lc.langfuse_trace():
        ran.append(True)
    assert ran == [True]
    assert "Langfuse trace context unavailable" in capsys.readouterr().out


def test_session_ids_are_prefixed_and_stable(monkeypatch):
    lc, _ = load(monkeypatch, enabled=False)
    sid = lc.get_session_id()
    assert re.fullmatch(r"vector-rag-[0-9a-f]{8}", sid)
    assert lc.get_session_id() == sid
    lc.set_session_id("custom")
    assert lc.get_session_id() == "custom"
