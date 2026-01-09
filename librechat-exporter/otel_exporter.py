"""
OTLP Exporter for LibreChat conversations.

Transforms ConversationPair objects into OpenTelemetry spans with gen_ai.*
semantic conventions, then exports them to ClickHouse via OTLP protocol.
"""

import os
from typing import List, Optional
from datetime import datetime

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode

from mongodb_client import ConversationPair


class LibreChatOTLPExporter:
    """Exports LibreChat conversation pairs as OpenTelemetry spans."""

    def __init__(
        self,
        otlp_endpoint: str = None,
        service_name: str = "librechat-conversations",
        api_key: str = None,
    ):
        self.otlp_endpoint = otlp_endpoint or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://clickstack:4318/v1/traces"
        )
        self.service_name = service_name
        self.api_key = api_key or os.getenv("CLICKSTACK_API_KEY", "")

        self._tracer = None
        self._provider = None

    def setup(self):
        """Initialize OpenTelemetry tracer and exporter."""
        # Create resource with service name
        resource = Resource.create({
            SERVICE_NAME: self.service_name,
            "service.version": "1.0.0",
            "telemetry.sdk.language": "python",
        })

        # Create tracer provider
        self._provider = TracerProvider(resource=resource)

        # Configure OTLP exporter
        headers = {}
        if self.api_key:
            headers["Authorization"] = self.api_key

        exporter = OTLPSpanExporter(
            endpoint=self.otlp_endpoint,
            headers=headers,
        )

        # Add batch processor for efficient export
        self._provider.add_span_processor(BatchSpanProcessor(exporter))

        # Set as global tracer provider
        trace.set_tracer_provider(self._provider)

        # Get tracer
        self._tracer = trace.get_tracer(__name__, "1.0.0")

        print(f"OTLP Exporter initialized:")
        print(f"  Endpoint: {self.otlp_endpoint}")
        print(f"  Service: {self.service_name}")

        return self

    def export_conversation_pair(self, pair: ConversationPair) -> str:
        """
        Export a single conversation pair as an OpenTelemetry span.

        Uses gen_ai.* semantic conventions to match OpenLLMetry format,
        allowing HyperDX and trace-evaluator to process these spans.

        Returns:
            The trace ID of the exported span
        """
        if not self._tracer:
            self.setup()

        # Create span with conversation timestamp
        with self._tracer.start_as_current_span(
            name="chat",
            kind=trace.SpanKind.CLIENT,
            start_time=self._datetime_to_ns(pair.timestamp),
        ) as span:
            # Set gen_ai.* attributes matching OpenLLMetry conventions
            # These are the attributes that trace-evaluator looks for
            span.set_attribute("gen_ai.prompt.0.content", pair.user_text)
            span.set_attribute("gen_ai.prompt.0.role", "user")
            span.set_attribute("gen_ai.completion.0.content", pair.assistant_text)
            span.set_attribute("gen_ai.completion.0.role", "assistant")

            # Request/response metadata
            if pair.model:
                span.set_attribute("gen_ai.request.model", pair.model)
                span.set_attribute("gen_ai.response.model", pair.model)

            if pair.endpoint:
                span.set_attribute("gen_ai.system", pair.endpoint)

            # Token counts
            if pair.user_token_count:
                span.set_attribute("gen_ai.usage.input_tokens", pair.user_token_count)
                span.set_attribute("gen_ai.usage.prompt_tokens", pair.user_token_count)

            if pair.assistant_token_count:
                span.set_attribute("gen_ai.usage.output_tokens", pair.assistant_token_count)
                span.set_attribute("gen_ai.usage.completion_tokens", pair.assistant_token_count)

            # Extended thinking (if present)
            if pair.assistant_thinking:
                span.set_attribute("gen_ai.completion.thinking", pair.assistant_thinking)

            # Tool calls (if present)
            if pair.tool_calls:
                # Store tool calls as JSON string for complex data
                import json
                span.set_attribute("gen_ai.tool_calls", json.dumps(pair.tool_calls))
                span.set_attribute("gen_ai.tool_calls.count", len(pair.tool_calls))

            # LibreChat-specific metadata for traceability
            span.set_attribute("librechat.conversation_id", pair.conversation_id)
            span.set_attribute("librechat.message_id", pair.message_id)
            span.set_attribute("librechat.user_message_id", pair.user_message_id)

            # Mark as successful
            span.set_status(Status(StatusCode.OK))

            # Get trace ID for reference
            trace_id = format(span.get_span_context().trace_id, '032x')

            return trace_id

    def export_conversation_pairs(self, pairs: List[ConversationPair]) -> List[str]:
        """
        Export multiple conversation pairs.

        Returns:
            List of trace IDs for exported spans
        """
        trace_ids = []
        for pair in pairs:
            trace_id = self.export_conversation_pair(pair)
            trace_ids.append(trace_id)

        return trace_ids

    def flush(self):
        """Flush any pending spans to the exporter."""
        if self._provider:
            self._provider.force_flush()

    def shutdown(self):
        """Shutdown the exporter cleanly."""
        if self._provider:
            self._provider.shutdown()

    def _datetime_to_ns(self, dt: datetime) -> int:
        """Convert datetime to nanoseconds since epoch."""
        return int(dt.timestamp() * 1_000_000_000)


def export_conversations_to_clickhouse(
    pairs: List[ConversationPair],
    otlp_endpoint: str = None,
    service_name: str = "librechat-conversations",
) -> List[str]:
    """
    Convenience function to export conversations to ClickHouse.

    Args:
        pairs: List of ConversationPair objects to export
        otlp_endpoint: OTLP endpoint URL (defaults to OTEL_EXPORTER_OTLP_ENDPOINT)
        service_name: Service name for traces

    Returns:
        List of trace IDs for exported spans
    """
    exporter = LibreChatOTLPExporter(
        otlp_endpoint=otlp_endpoint,
        service_name=service_name,
    )

    try:
        exporter.setup()
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

    print("Testing OTLP exporter...")
    trace_ids = export_conversations_to_clickhouse([test_pair])
    print(f"Exported {len(trace_ids)} traces")
    for tid in trace_ids:
        print(f"  Trace ID: {tid}")
