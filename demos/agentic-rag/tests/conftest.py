"""Test configuration: make agent modules importable and force Langfuse off.

These tests run with NO external services — the LLM and ClickHouse store are
mocked — so they're safe for CI. Langfuse keys are popped before any agent
module is imported so `is_langfuse_enabled()` is False and observations no-op.
"""

import os
import sys

# Force Langfuse disabled (computed at import time in langfuse_config).
for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_k, None)
# ChatAnthropic construction wants a key present (no network call at init).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

# Make agentic-rag/ importable (parent of this tests/ dir).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeStore:
    """In-memory stand-in for ClickHouseVectorStore (no DB connection)."""

    def __init__(self, hits=None, count=3):
        self._hits = hits if hits is not None else [
            {"id": 1, "doc_title": "What is ClickHouse?",
             "chunk": "ClickHouse is a columnar OLAP database.", "distance": 0.12},
        ]
        self._count = count

    def vector_search(self, embedding, top_k=4, title_filter=None):
        return list(self._hits)

    def count(self):
        return self._count


def make_ask(route="kb", grades=("yes",), reflect=("yes",),
             rewrite="rewritten query", answer="A grounded answer.", sql="SELECT 1"):
    """Build a fake `_ask` that scripts responses by matching prompt text.

    `grades` / `reflect` are sequences consumed in order across repeated calls,
    so a test can drive self-correction / regeneration deterministically.
    """
    grades = list(grades)
    reflect = list(reflect)
    state = {"g": 0, "r": 0}

    def _ask(prompt: str) -> str:
        p = prompt.lower()
        if "you route a user question" in p:
            return route
        if "you grade whether" in p:
            i = min(state["g"], len(grades) - 1)
            state["g"] += 1
            return grades[i]
        if "rewrite it to improve" in p:
            return rewrite
        if "is every claim in the answer" in p:
            i = min(state["r"], len(reflect) - 1)
            state["r"] += 1
            return reflect[i]
        if "write a single clickhouse select" in p:
            return sql
        return answer  # generation prompt (Langfuse fallback template)

    return _ask
