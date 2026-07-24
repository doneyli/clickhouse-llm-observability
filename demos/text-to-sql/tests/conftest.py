"""Test setup for the text-to-sql gate AND refine-loop tests.

Runs with NO external services: Langfuse is forced off (so gate spans / trace
tags no-op), the heavy LangChain deps are stubbed when absent so `sql_pipeline`
imports, and the ClickHouse evidence client and the LLM (`_ask`) are faked. Safe
for CI. Also importable directly (each test file does `import conftest`) so the
files run under a bare `python3 test_*.py` as well as under pytest.

Fixtures by consumer:
    FakeChain, run_tests      -> test_gates.py, test_query_routing.py
    FakeClient, FakeResult    -> test_evidence.py
    RecordingAsk              -> test_refine_loop.py
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


# --------------- Shared fakes: gates / routing ---------------

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


# --------------- Shared fakes: refine loop / evidence ---------------

class FakeResult:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


class FakeClient:
    """Stand-in for a clickhouse_connect client.

    explain_error / exec_error simulate EXPLAIN / execution failures; exec_rows
    controls the returned rows (empty list -> nonempty_result False).
    """

    def __init__(self, explain_error=None, exec_error=None, exec_rows=None, cols=None):
        self.explain_error = explain_error
        self.exec_error = exec_error
        self.exec_rows = exec_rows if exec_rows is not None else [[42]]
        self.cols = cols or ["count()"]

    def query(self, sql, settings=None):
        if sql.strip().upper().startswith("EXPLAIN"):
            if self.explain_error:
                raise RuntimeError(self.explain_error)
            return FakeResult(["explain"], [["Expression"], ["ReadFromMergeTree"]])
        if self.exec_error:
            raise RuntimeError(self.exec_error)
        return FakeResult(self.cols, self.exec_rows)


class RecordingAsk:
    """A fake `_ask` that scripts generator + critic responses and records every
    prompt it sees (so tests can assert critique feedback was fed back)."""

    def __init__(self, gen_sqls, critic_jsons):
        self._gen = list(gen_sqls)
        self._crit = list(critic_jsons)
        self.prompts = []
        self._gi = 0
        self._ci = 0

    def __call__(self, prompt, temperature=0.0):
        self.prompts.append(prompt)
        low = prompt.lower()
        if "sql critic" in low:  # both critic fallbacks contain "SQL critic"
            i = min(self._ci, len(self._crit) - 1)
            self._ci += 1
            return self._crit[i]
        i = min(self._gi, len(self._gen) - 1)
        self._gi += 1
        return self._gen[i]


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
