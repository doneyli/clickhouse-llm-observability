"""The export filter drops the Anthropic SDK's native spans and keeps ours."""
from types import SimpleNamespace

from explorer.tracing import should_export_span


def span(scope: str, attrs: dict | None = None):
    return SimpleNamespace(instrumentation_scope=SimpleNamespace(name=scope), attributes=attrs or {})


def test_drops_anthropic_native_spans():
    assert should_export_span(span("com.anthropic.sdk.python", {"gen_ai.system": "anthropic"})) is False


def test_keeps_langfuse_spans():
    assert should_export_span(span("langfuse-sdk")) is True
