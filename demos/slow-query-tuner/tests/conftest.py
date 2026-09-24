"""Test configuration — all tests are LLM-free and DB-free.

Langfuse keys are popped before any demo module imports so instrumentation
no-ops; the environment (ClickHouse) and Anthropic client are never touched (the
env is mocked, the loop's LLM call is never exercised in unit tests).
"""

import os
import sys

for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_k, None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

# Make the demo package importable (parent of this tests/ dir).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
