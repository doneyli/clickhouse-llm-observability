"""Make the demo modules importable and provide fakes.

These tests run WITHOUT Langfuse keys (langfuse_config no-ops) and WITHOUT any
live handler services — the Anthropic client and the httpx dispatch are stubbed,
so nothing hits the network.
"""

import os
import sys
from pathlib import Path

import pytest

# Import the demo package modules (router.py, handlers.py, ...) without a package.
DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))

# Ensure Langfuse is disabled for the whole test session (no accidental exports).
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)


class _Block:
    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Msg(self._text)


class FakeAnthropic:
    """Stand-in Anthropic client whose messages.create returns a fixed text."""

    def __init__(self, text):
        self.messages = FakeMessages(text)


@pytest.fixture
def fake_anthropic():
    return lambda text: (lambda: FakeAnthropic(text))
