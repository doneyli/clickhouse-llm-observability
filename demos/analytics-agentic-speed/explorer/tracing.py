"""The Langfuse client this demo uses everywhere (agent, seeders, experiments).

One filter matters: the Anthropic Python SDK emits its own OpenTelemetry GenAI spans
(instrumentation scope ``com.anthropic.sdk.python``). Langfuse's default export filter
keeps GenAI spans, so every model call would appear twice — once as our `plan`/`analyse`
generation (with cost) and once as `anthropic.messages.create` — and the cost would be
counted twice. Our own generations already carry model, usage and cost.
"""
from __future__ import annotations

from typing import Any

from langfuse import Langfuse
from langfuse.span_filter import is_default_export_span

ANTHROPIC_NATIVE_SCOPE = "com.anthropic.sdk.python"

_client: Langfuse | None = None


def should_export_span(span: Any) -> bool:
    scope = getattr(getattr(span, "instrumentation_scope", None), "name", "") or ""
    if scope.startswith(ANTHROPIC_NATIVE_SCOPE):
        return False
    return is_default_export_span(span)


def get_langfuse() -> Langfuse:
    """Process-wide client, configured from the env that `config.load_env()` loaded."""
    global _client
    if _client is None:
        _client = Langfuse(should_export_span=should_export_span)
    return _client
