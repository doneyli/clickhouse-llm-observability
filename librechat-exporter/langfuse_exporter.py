"""
Langfuse Exporter for LibreChat conversations.

Transforms ConversationPair objects into Langfuse traces/generations,
enabling LLM-specific observability features like cost tracking,
evaluations, and prompt management.

Updated for Langfuse SDK v3 (OpenTelemetry-based API).
"""

import os
from typing import List, Optional
from datetime import datetime

from mongodb_client import ConversationPair
from langfuse_config import is_langfuse_enabled, flush as langfuse_flush


class LibreChatLangfuseExporter:
    """Exports LibreChat conversation pairs as Langfuse traces using v3 API."""

    def __init__(self):
        self._client = None
        self._enabled = is_langfuse_enabled()

    def setup(self):
        """Initialize Langfuse client using v3 API."""
        if not self._enabled:
            print("Langfuse not configured - skipping Langfuse export")
            return self

        try:
            from langfuse import get_client
            self._client = get_client()
            print("Langfuse Exporter initialized (v3 API)")
        except Exception as e:
            self._enabled = False
            print(f"Failed to initialize Langfuse client: {e}")

        return self

    def is_enabled(self) -> bool:
        """Check if Langfuse export is enabled and ready."""
        return self._enabled and self._client is not None

    def export_conversation_pair(self, pair: ConversationPair) -> Optional[str]:
        """
        Export a single conversation pair as a Langfuse trace with generation.
        Uses Langfuse SDK v3 context manager API.

        Returns:
            The trace ID of the exported trace, or None if export failed/disabled
        """
        if not self.is_enabled():
            return None

        try:
            from langfuse import propagate_attributes

            # Use conversation_id as session_id to group related messages
            with propagate_attributes(session_id=pair.conversation_id):
                # Create a trace using v3 context manager API
                with self._client.start_as_current_observation(
                    as_type="span",
                    name="librechat-conversation",
                    input=pair.user_text,
                    metadata={
                        "conversation_id": pair.conversation_id,
                        "message_id": pair.message_id,
                        "user_message_id": pair.user_message_id,
                        "endpoint": pair.endpoint or "unknown",
                        "source": "librechat-exporter",
                        "has_thinking": bool(pair.assistant_thinking),
                        "has_tool_calls": bool(pair.tool_calls),
                    },
                ) as trace_span:
                    # Create a generation span within the trace
                    with self._client.start_as_current_observation(
                        as_type="generation",
                        name="chat-completion",
                        model=pair.model or "unknown",
                        input=[{"role": "user", "content": pair.user_text}],
                        metadata={
                            "conversation_id": pair.conversation_id,
                            "tool_calls_count": len(pair.tool_calls) if pair.tool_calls else 0,
                        },
                    ) as generation:
                        # Update generation with output and usage
                        generation.update(
                            output=pair.assistant_text,
                            usage={
                                "input": pair.user_token_count or 0,
                                "output": pair.assistant_token_count or 0,
                                "total": (pair.user_token_count or 0) + (pair.assistant_token_count or 0),
                            },
                        )

                    # Update trace with output
                    trace_span.update(output=pair.assistant_text)

                    # Get trace ID
                    trace_id = trace_span.trace_id if hasattr(trace_span, 'trace_id') else str(pair.message_id)

            return trace_id

        except Exception as e:
            print(f"Failed to export pair {pair.message_id} to Langfuse: {e}")
            return None

    def export_conversation_pairs(self, pairs: List[ConversationPair]) -> List[str]:
        """
        Export multiple conversation pairs.

        Returns:
            List of trace IDs for exported traces (excludes failed exports)
        """
        trace_ids = []
        for pair in pairs:
            trace_id = self.export_conversation_pair(pair)
            if trace_id:
                trace_ids.append(trace_id)

        return trace_ids

    def flush(self):
        """Flush any pending traces to Langfuse."""
        langfuse_flush()

    def shutdown(self):
        """Shutdown the exporter cleanly."""
        from langfuse_config import shutdown
        shutdown()
        self._client = None


def export_conversations_to_langfuse(
    pairs: List[ConversationPair],
) -> List[str]:
    """
    Convenience function to export conversations to Langfuse.

    Args:
        pairs: List of ConversationPair objects to export

    Returns:
        List of trace IDs for exported traces
    """
    exporter = LibreChatLangfuseExporter()

    try:
        exporter.setup()
        if not exporter.is_enabled():
            return []

        trace_ids = exporter.export_conversation_pairs(pairs)
        exporter.flush()
        return trace_ids
    finally:
        exporter.shutdown()


if __name__ == "__main__":
    # Test the exporter with mock data
    from datetime import datetime

    test_pair = ConversationPair(
        conversation_id="test-conv-123",
        message_id="test-msg-456",
        user_message_id="test-user-msg-789",
        timestamp=datetime.utcnow(),
        user_text="What is ClickHouse?",
        assistant_text="ClickHouse is a column-oriented database management system...",
        model="claude-3-sonnet",
        endpoint="anthropic",
        user_token_count=5,
        assistant_token_count=50,
    )

    print("Testing Langfuse exporter (v3 API)...")
    trace_ids = export_conversations_to_langfuse([test_pair])
    print(f"Exported {len(trace_ids)} traces to Langfuse")
    for tid in trace_ids:
        print(f"  Trace ID: {tid}")
