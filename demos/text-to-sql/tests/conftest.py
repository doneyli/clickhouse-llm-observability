"""Test config: make the demo modules importable and force Langfuse OFF.

These tests run with NO external services — the ClickHouse evidence client and the
LLM (`_ask`) are mocked — so they're safe for CI. Langfuse keys are popped before
any demo module is imported so `LANGFUSE_ENABLED` is False and every observation /
score is a no-op.
"""

import os
import sys

for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_k, None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

# Make demos/text-to-sql/ importable (parent of this tests/ dir).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
