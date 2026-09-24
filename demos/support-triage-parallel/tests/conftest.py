"""Test configuration: make demo modules importable and force Langfuse off.

These tests exercise only the pure aggregation/merge logic — NO LLM, NO
ClickHouse, NO Langfuse — so they are safe for CI. Langfuse keys are popped
before any demo module is imported so ``is_langfuse_enabled()`` is False and
every observation no-ops. Third-party clients (anthropic, clickhouse-connect,
sqlglot) are imported lazily inside the modules, so they need not be installed
for these tests to run.
"""

import os
import sys

for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_k, None)

# Make the demo package root importable (parent of this tests/ dir).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
