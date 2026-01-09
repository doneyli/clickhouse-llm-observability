"""
ClickHouse Client for querying LLM traces from HyperDX/ClickStack.

Connects to the ClickHouse backend and extracts gen_ai.* attributes
from OpenTelemetry spans.
"""

import os
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import clickhouse_connect


@dataclass
class LLMTrace:
    """Represents an LLM interaction extracted from traces."""
    trace_id: str
    span_id: str
    timestamp: datetime
    service_name: str
    prompt: str
    completion: str
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    duration_ms: Optional[int] = None


class ClickHouseTraceClient:
    """Client for querying LLM traces from ClickHouse."""

    def __init__(
        self,
        host: str = None,
        port: int = None,
        database: str = None,
        username: str = None,
        password: str = None,
    ):
        self.host = host or os.getenv("CLICKHOUSE_TRACE_HOST", "clickstack")
        self.port = port or int(os.getenv("CLICKHOUSE_TRACE_PORT", "8123"))
        self.database = database or os.getenv("CLICKHOUSE_TRACE_DATABASE", "default")
        self.username = username or os.getenv("CLICKHOUSE_TRACE_USER", "default")
        self.password = password or os.getenv("CLICKHOUSE_TRACE_PASSWORD", "")

        self._client = None

    def connect(self):
        """Establish connection to ClickHouse."""
        print(f"Connecting to ClickHouse at {self.host}:{self.port}/{self.database}")
        self._client = clickhouse_connect.get_client(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
        )
        # Test connection
        result = self._client.query("SELECT 1")
        print(f"Connected to ClickHouse successfully")
        return self

    def get_llm_traces(
        self,
        service_name: str = "librechat-api",
        hours_ago: int = 24,
        limit: int = 100,
        evaluated_trace_ids: Optional[List[str]] = None,
    ) -> List[LLMTrace]:
        """
        Query LLM traces from ClickHouse.

        Args:
            service_name: Filter by service name (default: librechat-api)
            hours_ago: How far back to look (default: 24 hours)
            limit: Max number of traces to return
            evaluated_trace_ids: List of trace IDs to exclude (already evaluated)

        Returns:
            List of LLMTrace objects with prompt/completion pairs
        """
        if not self._client:
            self.connect()

        # Build exclusion clause for already-evaluated traces
        exclusion_clause = ""
        if evaluated_trace_ids:
            ids_str = ", ".join(f"'{tid}'" for tid in evaluated_trace_ids)
            exclusion_clause = f"AND TraceId NOT IN ({ids_str})"

        # Query for spans with gen_ai attributes
        # OpenLLMetry uses gen_ai.prompt.0.content and gen_ai.completion.0.content
        # Also check for legacy naming conventions
        query = f"""
        SELECT
            TraceId,
            SpanId,
            Timestamp,
            ServiceName,
            SpanName,
            Duration,
            SpanAttributes['gen_ai.prompt.0.content'] as prompt,
            SpanAttributes['gen_ai.completion.0.content'] as completion,
            SpanAttributes['gen_ai.request.model'] as model,
            SpanAttributes['gen_ai.usage.input_tokens'] as input_tokens,
            SpanAttributes['gen_ai.usage.output_tokens'] as output_tokens,
            SpanAttributes['gen_ai.prompt'] as legacy_prompt,
            SpanAttributes['gen_ai.completion'] as legacy_completion
        FROM otel_traces
        WHERE ServiceName = '{service_name}'
          AND Timestamp >= now() - INTERVAL {hours_ago} HOUR
          AND (
              length(SpanAttributes['gen_ai.prompt.0.content']) > 0
              OR length(SpanAttributes['gen_ai.completion.0.content']) > 0
              OR length(SpanAttributes['gen_ai.prompt']) > 0
          )
          {exclusion_clause}
        ORDER BY Timestamp DESC
        LIMIT {limit}
        """

        print(f"Querying traces for service: {service_name}, last {hours_ago}h")
        result = self._client.query(query)

        traces = []
        for row in result.result_rows:
            (trace_id, span_id, timestamp, svc_name, span_name, duration,
             prompt, completion, model, input_tokens, output_tokens,
             legacy_prompt, legacy_completion) = row

            # Handle different attribute naming conventions
            # OpenLLMetry uses gen_ai.prompt.0.content or legacy gen_ai.prompt
            actual_prompt = prompt or legacy_prompt or ""
            actual_completion = completion or legacy_completion or ""

            # Skip if we don't have both prompt and completion
            if not actual_prompt or not actual_completion:
                continue

            # Parse token counts if present
            try:
                input_tok = int(input_tokens) if input_tokens else None
            except (ValueError, TypeError):
                input_tok = None
            try:
                output_tok = int(output_tokens) if output_tokens else None
            except (ValueError, TypeError):
                output_tok = None

            traces.append(LLMTrace(
                trace_id=trace_id,
                span_id=span_id,
                timestamp=timestamp,
                service_name=svc_name,
                prompt=actual_prompt,
                completion=actual_completion,
                model=model or None,
                input_tokens=input_tok,
                output_tokens=output_tok,
                duration_ms=int(duration / 1_000_000) if duration else None,  # ns to ms
            ))

        print(f"Found {len(traces)} LLM traces with prompt/completion pairs")
        return traces

    def get_available_services(self) -> List[str]:
        """Get list of services that have LLM traces."""
        if not self._client:
            self.connect()

        query = """
        SELECT DISTINCT ServiceName
        FROM otel_traces
        WHERE length(SpanAttributes['gen_ai.prompt.0.content']) > 0
           OR length(SpanAttributes['gen_ai.prompt']) > 0
        ORDER BY ServiceName
        """

        result = self._client.query(query)
        return [row[0] for row in result.result_rows]

    def get_trace_count(self, service_name: str = "librechat-api", hours_ago: int = 24) -> int:
        """Get count of LLM traces for a service."""
        if not self._client:
            self.connect()

        query = f"""
        SELECT COUNT(*)
        FROM otel_traces
        WHERE ServiceName = '{service_name}'
          AND Timestamp >= now() - INTERVAL {hours_ago} HOUR
          AND (
              length(SpanAttributes['gen_ai.prompt.0.content']) > 0
              OR length(SpanAttributes['gen_ai.prompt']) > 0
          )
        """

        result = self._client.query(query)
        return result.result_rows[0][0] if result.result_rows else 0

    def close(self):
        """Close the connection."""
        if self._client:
            self._client.close()
            self._client = None


# Convenience function for quick queries
def get_recent_llm_traces(
    service_name: str = "librechat-api",
    hours_ago: int = 24,
    limit: int = 100,
) -> List[LLMTrace]:
    """Quick helper to get recent LLM traces."""
    client = ClickHouseTraceClient()
    try:
        client.connect()
        return client.get_llm_traces(service_name, hours_ago, limit)
    finally:
        client.close()


if __name__ == "__main__":
    # Test the client
    client = ClickHouseTraceClient()
    client.connect()

    print("\n=== Available Services with LLM Traces ===")
    services = client.get_available_services()
    for svc in services:
        count = client.get_trace_count(svc)
        print(f"  {svc}: {count} traces (last 24h)")

    print("\n=== Recent LLM Traces ===")
    traces = client.get_llm_traces(limit=5)
    for trace in traces:
        print(f"\n  TraceId: {trace.trace_id[:16]}...")
        print(f"  Service: {trace.service_name}")
        print(f"  Model: {trace.model}")
        print(f"  Prompt: {trace.prompt[:100]}...")
        print(f"  Completion: {trace.completion[:100]}...")

    client.close()
