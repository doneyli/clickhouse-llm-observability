"""Test setup for the text-to-sql gate tests.

Runs with NO external services — Langfuse is forced off (so gate spans / trace
tags no-op) and the heavy LangChain deps are stubbed when absent, so `sql_pipeline`
imports and its gate routing is unit-testable with plain fakes. Also importable
directly (each test file does `import conftest`) so the files run under a bare
`python3 test_*.py` as well as under pytest.
"""

import os
import sys
import types
from pathlib import Path

# 1. Force Langfuse disabled BEFORE importing any demo module (LANGFUSE_ENABLED
#    is computed at import time in langfuse_config).
for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_k, None)
os.environ.pop("DEMO_FAULT", None)
# ChatAnthropic construction wants a key present (no network call at init).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

# 2. Make demos/text-to-sql/ importable (parent of this tests/ dir).
_DEMO_DIR = str(Path(__file__).resolve().parent.parent)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)


# 3. Stub heavy LangChain modules if they aren't installed, so `import
#    sql_pipeline` succeeds. Routing tests never call these — they build the
#    pipeline via object.__new__ and inject fake chains — so trivial stubs suffice.
def _ensure_stub(name, **attrs):
    try:
        __import__(name)
        return  # real module available; prefer it
    except Exception:
        pass
    parts = name.split(".")
    for i in range(1, len(parts)):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            parent = types.ModuleType(pkg)
            parent.__path__ = []  # mark as package
            sys.modules[pkg] = parent
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


class _StubChatModel:
    def __init__(self, *a, **k):
        pass


class _StubPromptTemplate:
    def __init__(self, *a, **k):
        pass

    @classmethod
    def from_template(cls, *a, **k):
        return cls()

    def __or__(self, other):
        return other


class _StubParser:
    def __init__(self, *a, **k):
        pass


_ensure_stub("langchain_anthropic", ChatAnthropic=_StubChatModel)
_ensure_stub("langchain_core")
_ensure_stub("langchain_core.prompts", ChatPromptTemplate=_StubPromptTemplate)
_ensure_stub("langchain_core.output_parsers", StrOutputParser=_StubParser)


# --------------- Shared fakes ---------------

class FakeChain:
    """Scripted stand-in for a LangChain runnable: returns `outputs` in order
    (repeating the last), and records every invocation for assertions."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self._i = 0

    def invoke(self, inputs, config=None):
        self.calls.append({"inputs": inputs, "config": config})
        out = self.outputs[min(self._i, len(self.outputs) - 1)]
        self._i += 1
        return out


def run_tests(namespace):
    """Minimal runner so a test file works under `python3 test_x.py` (no pytest).
    Returns the process exit code."""
    fns = [(n, f) for n, f in sorted(namespace.items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {n}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0
