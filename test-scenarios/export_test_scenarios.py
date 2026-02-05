#!/usr/bin/env python3
"""
Test Scenarios Exporter

Exports synthetic LLM conversations with pre-crafted prompt/response pairs
that demonstrate common evaluation failure modes:

1. Low Relevance - Off-topic/tangential responses
2. Low Coherence - Rambling/contradictory responses
3. Hallucination - Fabricated information

These scenarios help demonstrate the value of LLM observability and evaluation
by showing cases where quality scores indicate issues that need investigation.

Usage:
    python export_test_scenarios.py                    # Export all scenarios
    python export_test_scenarios.py --scenario 1      # Export specific scenario
    python export_test_scenarios.py --list            # List available scenarios
"""

import os
import sys
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode

# Check if Langfuse is configured
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)


@dataclass
class TestScenario:
    """A test scenario with prompt, response, and expected scores."""
    id: int
    name: str
    category: str
    description: str
    prompt: str
    response: str
    model: str
    expected_relevance: str  # e.g., "0.3-0.5"
    expected_coherence: str  # e.g., "0.8-1.0"
    why_low: str  # Explanation of why scores should be low
    tags: List[str] = None  # Tags for evaluator filtering

    def __post_init__(self):
        if self.tags is None:
            self.tags = ["test-scenario"]


# =============================================================================
# TEST SCENARIOS
# =============================================================================

SCENARIOS = [
    # -------------------------------------------------------------------------
    # Scenario 1: Low Relevance (Off-Topic Response)
    # -------------------------------------------------------------------------
    TestScenario(
        id=1,
        name="Off-Topic Response",
        category="Low Relevance",
        description="The LLM provides a coherent response about the wrong topic",
        prompt="What are ClickHouse's pricing tiers for cloud hosting?",
        response="""ClickHouse is a powerful columnar database management system that uses
vectorized query execution for high performance. It was originally developed at Yandex
for web analytics use cases. The MergeTree engine family is the cornerstone of ClickHouse,
providing efficient data storage and retrieval through sorted data organization.

Key features include:
- Columnar storage for analytical workloads
- Real-time data ingestion capabilities
- SQL support with extensions for analytics
- Distributed query processing

The system excels at handling large volumes of data with sub-second query latency,
making it popular for log analysis, time-series data, and business intelligence applications.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.2-0.4",
        expected_coherence="0.9-1.0",
        why_low="Response discusses ClickHouse features but completely ignores the pricing question",
        tags=["test-scenario", "relevance-test"]
    ),

    # -------------------------------------------------------------------------
    # Scenario 2: Low Coherence (Contradictory Response)
    # -------------------------------------------------------------------------
    TestScenario(
        id=2,
        name="Contradictory Response",
        category="Low Coherence",
        description="The LLM contradicts itself multiple times within the response",
        prompt="Should I use ClickHouse or PostgreSQL for my analytics workload?",
        response="""For analytics workloads, ClickHouse is definitely the better choice because
of its columnar storage design. However, PostgreSQL is actually the superior option for
analytics due to its mature ecosystem.

You should absolutely use ClickHouse for OLAP queries. But wait, PostgreSQL's row-based
storage is actually ideal for analytical processing, so go with PostgreSQL instead.

In my professional opinion, neither database is suitable for analytics. Actually, both
are perfect choices. ClickHouse can't handle large datasets efficiently, except it's
specifically designed for petabyte-scale data.

My final recommendation: Use ClickHouse. No, use PostgreSQL. Actually, use MongoDB
for analytics. In conclusion, ClickHouse is the only viable option, unless you prefer
PostgreSQL, which is equally unviable and also the best choice.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Response repeatedly contradicts itself, making it impossible to extract a clear answer",
        tags=["test-scenario", "coherence-test"]
    ),

    # -------------------------------------------------------------------------
    # Scenario 3: Hallucination (Fabricated Information)
    # -------------------------------------------------------------------------
    TestScenario(
        id=3,
        name="Fabricated Information",
        category="Hallucination",
        description="The LLM confidently states false information",
        prompt="Who created ClickHouse and what is its history?",
        response="""ClickHouse was created by Dr. Elena Volkov at the Massachusetts Institute
of Technology (MIT) in 2008 as part of her doctoral research on distributed database systems.

The project was initially funded by a $50 million grant from the National Science Foundation
(NSF) under their "Big Data Infrastructure" initiative. Dr. Volkov named it "ClickHouse"
after her childhood home in St. Petersburg, Russia, which was known as the "Click House"
due to the sound the old wooden floors made.

Key milestones:
- 2008: Initial development at MIT
- 2010: First public release under Apache 2.0 license
- 2012: Acquired by Google for $200 million
- 2015: Spun off as independent company "ClickHouse Inc."
- 2018: Reached 1 billion downloads

The database gained popularity after being featured in a famous 2011 TED talk by
Dr. Volkov titled "The Future of Real-Time Analytics." Today, ClickHouse is maintained
by a team of over 500 engineers at their headquarters in Boston, Massachusetts.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Response is relevant and coherent but contains entirely fabricated history (ClickHouse was actually created at Yandex by Alexey Milovidov, open-sourced in 2016)",
        tags=["test-scenario", "hallucination-test"]
    ),

    # -------------------------------------------------------------------------
    # Scenario 4: Good Response (Control)
    # -------------------------------------------------------------------------
    TestScenario(
        id=4,
        name="Good Response (Control)",
        category="Control",
        description="A well-formed, accurate, and relevant response for comparison",
        prompt="What are the main advantages of ClickHouse for analytics?",
        response="""ClickHouse offers several key advantages for analytics workloads:

1. **Columnar Storage**: Data is stored by columns rather than rows, which dramatically
   improves query performance for analytical queries that typically access only a subset
   of columns.

2. **Vectorized Query Execution**: Processes data in batches using SIMD instructions,
   achieving very high CPU efficiency.

3. **Real-Time Data Ingestion**: Supports high-throughput inserts while maintaining
   query performance, making it suitable for real-time analytics.

4. **Compression**: Achieves excellent compression ratios (often 10x or more) due to
   columnar storage and specialized codecs.

5. **SQL Support**: Uses familiar SQL syntax with extensions for analytical functions,
   reducing the learning curve.

6. **Scalability**: Supports distributed queries across clusters for horizontal scaling.

7. **Cost Efficiency**: Open-source with no licensing costs, and efficient resource
   utilization reduces infrastructure costs.

These features make ClickHouse particularly well-suited for log analysis, time-series
data, business intelligence, and any workload requiring fast aggregations over large
datasets.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="This is a control scenario - scores should be high",
        tags=["test-scenario", "control"]
    ),
]


class LangfuseExporter:
    """Exports test scenarios to Langfuse."""

    def __init__(self):
        self._client = None
        self._enabled = LANGFUSE_ENABLED

    def setup(self):
        """Initialize Langfuse client."""
        if not self._enabled:
            print("Langfuse not configured - skipping Langfuse export")
            return self

        try:
            from langfuse import get_client
            self._client = get_client()
            print(f"Langfuse Exporter initialized: {os.getenv('LANGFUSE_HOST', 'cloud')}")
        except Exception as e:
            self._enabled = False
            print(f"Failed to initialize Langfuse: {e}")

        return self

    def is_enabled(self) -> bool:
        return self._enabled and self._client is not None

    def export_scenario(self, scenario: TestScenario) -> Optional[str]:
        """Export a single test scenario as a Langfuse trace with generation."""
        if not self.is_enabled():
            return None

        try:
            from langfuse import propagate_attributes

            session_id = f"test-scenarios-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

            with propagate_attributes(session_id=session_id):
                with self._client.start_as_current_observation(
                    as_type="span",
                    name=f"test-scenario-{scenario.id}",
                    input=scenario.prompt,
                    metadata={
                        "scenario_id": scenario.id,
                        "scenario_name": scenario.name,
                        "category": scenario.category,
                        "expected_relevance": scenario.expected_relevance,
                        "expected_coherence": scenario.expected_coherence,
                        "why_low": scenario.why_low,
                        "source": "test-scenarios",
                    },
                    tags=scenario.tags,
                ) as trace_span:
                    with self._client.start_as_current_observation(
                        as_type="generation",
                        name="chat-completion",
                        model=scenario.model,
                        input=[{"role": "user", "content": scenario.prompt}],
                        metadata={
                            "scenario_id": scenario.id,
                            "category": scenario.category,
                        },
                    ) as generation:
                        generation.update(
                            output=scenario.response,
                            usage={
                                "input": len(scenario.prompt.split()),
                                "output": len(scenario.response.split()),
                            },
                        )

                    trace_span.update(output=scenario.response)
                    trace_id = trace_span.trace_id if hasattr(trace_span, 'trace_id') else str(scenario.id)

            return trace_id

        except Exception as e:
            print(f"Failed to export scenario {scenario.id} to Langfuse: {e}")
            return None

    def export_scenarios(self, scenarios: List[TestScenario]) -> List[str]:
        """Export multiple scenarios to Langfuse."""
        trace_ids = []
        for scenario in scenarios:
            trace_id = self.export_scenario(scenario)
            if trace_id:
                trace_ids.append(trace_id)
                print(f"    Langfuse: [{scenario.id}] {scenario.name}")
        return trace_ids

    def flush(self):
        """Flush pending events."""
        if self._client and hasattr(self._client, 'flush'):
            self._client.flush()

    def shutdown(self):
        """Shutdown the client."""
        self.flush()


class TestScenarioExporter:
    """Exports test scenarios as OpenTelemetry spans."""

    def __init__(
        self,
        otlp_endpoint: str = None,
        service_name: str = "test-scenarios",
        api_key: str = None,
    ):
        self.otlp_endpoint = otlp_endpoint or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://localhost:4318/v1/traces"
        )
        self.service_name = service_name
        self.api_key = api_key or os.getenv("CLICKSTACK_API_KEY", "")

        self._tracer = None
        self._provider = None
        self._langfuse = LangfuseExporter()

    def setup(self):
        """Initialize OpenTelemetry tracer and exporter."""
        resource = Resource.create({
            SERVICE_NAME: self.service_name,
            "service.version": "1.0.0",
        })

        self._provider = TracerProvider(resource=resource)

        # Local HyperDX doesn't require authentication
        exporter = OTLPSpanExporter(endpoint=self.otlp_endpoint)

        self._provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(self._provider)
        self._tracer = trace.get_tracer(__name__, "1.0.0")

        print(f"OTLP Exporter initialized:")
        print(f"  Endpoint: {self.otlp_endpoint}")
        print(f"  Service: {self.service_name}")

        # Also setup Langfuse
        self._langfuse.setup()

        return self

    def export_scenario(self, scenario: TestScenario) -> str:
        """Export a single test scenario as an OpenTelemetry span."""
        if not self._tracer:
            self.setup()

        # Use current time minus a small offset to ensure visibility
        timestamp = datetime.utcnow() - timedelta(seconds=scenario.id)

        with self._tracer.start_as_current_span(
            name="chat",
            kind=trace.SpanKind.CLIENT,
            start_time=int(timestamp.timestamp() * 1_000_000_000),
        ) as span:
            # Gen AI semantic conventions (what trace-evaluator looks for)
            span.set_attribute("gen_ai.prompt.0.content", scenario.prompt)
            span.set_attribute("gen_ai.prompt.0.role", "user")
            span.set_attribute("gen_ai.completion.0.content", scenario.response)
            span.set_attribute("gen_ai.completion.0.role", "assistant")
            span.set_attribute("gen_ai.request.model", scenario.model)
            span.set_attribute("gen_ai.response.model", scenario.model)
            span.set_attribute("gen_ai.system", "anthropic")

            # Test scenario metadata (for easy identification)
            span.set_attribute("test_scenario.id", scenario.id)
            span.set_attribute("test_scenario.name", scenario.name)
            span.set_attribute("test_scenario.category", scenario.category)
            span.set_attribute("test_scenario.expected_relevance", scenario.expected_relevance)
            span.set_attribute("test_scenario.expected_coherence", scenario.expected_coherence)

            span.set_status(Status(StatusCode.OK))

            trace_id = format(span.get_span_context().trace_id, '032x')
            return trace_id

    def export_scenarios(self, scenarios: List[TestScenario]) -> List[str]:
        """Export multiple test scenarios."""
        trace_ids = []
        for scenario in scenarios:
            trace_id = self.export_scenario(scenario)
            trace_ids.append(trace_id)
            print(f"  [{scenario.id}] {scenario.name}: {trace_id}")

        # Also export to Langfuse
        if self._langfuse.is_enabled():
            print("\nExporting to Langfuse:")
            self._langfuse.export_scenarios(scenarios)

        return trace_ids

    def flush(self):
        """Flush pending spans."""
        if self._provider:
            self._provider.force_flush()
        self._langfuse.flush()

    def shutdown(self):
        """Shutdown the exporter."""
        if self._provider:
            self._provider.shutdown()
        self._langfuse.shutdown()


def list_scenarios():
    """Print all available test scenarios."""
    print("\n" + "=" * 70)
    print("AVAILABLE TEST SCENARIOS")
    print("=" * 70)

    for s in SCENARIOS:
        print(f"\n[{s.id}] {s.name}")
        print(f"    Category: {s.category}")
        print(f"    Expected Relevance: {s.expected_relevance}")
        print(f"    Expected Coherence: {s.expected_coherence}")
        print(f"    Description: {s.description}")
        print(f"    Why: {s.why_low}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Export test scenarios for LLM evaluation demonstration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python export_test_scenarios.py                    # Export all scenarios
  python export_test_scenarios.py --scenario 1      # Export scenario 1 only
  python export_test_scenarios.py --scenario 1 2 3  # Export specific scenarios
  python export_test_scenarios.py --list            # List all scenarios
        """
    )

    parser.add_argument(
        "--scenario", "-s",
        type=int,
        nargs="+",
        help="Specific scenario ID(s) to export"
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available scenarios"
    )
    parser.add_argument(
        "--service-name",
        default="test-scenarios",
        help="Service name for traces (default: test-scenarios)"
    )

    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    # Select scenarios to export
    if args.scenario:
        scenarios_to_export = [s for s in SCENARIOS if s.id in args.scenario]
        if not scenarios_to_export:
            print(f"Error: No scenarios found with IDs: {args.scenario}")
            print("Use --list to see available scenarios")
            sys.exit(1)
    else:
        scenarios_to_export = SCENARIOS

    print("\n" + "=" * 70)
    print("TEST SCENARIOS EXPORTER")
    print("=" * 70)
    print(f"Exporting {len(scenarios_to_export)} scenario(s)...")
    print()

    exporter = TestScenarioExporter(service_name=args.service_name)

    try:
        exporter.setup()
        print("\nExporting scenarios:")
        trace_ids = exporter.export_scenarios(scenarios_to_export)
        exporter.flush()

        print("\n" + "-" * 70)
        print(f"Exported {len(trace_ids)} scenarios to ClickStack")
        if LANGFUSE_ENABLED:
            print(f"Exported {len(trace_ids)} scenarios to Langfuse")
        print("-" * 70)

        print("\nNext steps:")
        print("1. View traces in HyperDX: http://localhost:8080")
        print("   Search: service:test-scenarios")
        if LANGFUSE_ENABLED:
            print()
            print("2. View traces in Langfuse: http://localhost:3001")
            print("   Filter by: test-scenarios")
        print()
        print("3. Run evaluations in Langfuse:")
        print("   Use Langfuse's built-in evaluation features")
        print()

        print("Expected results:")
        for s in scenarios_to_export:
            print(f"  [{s.id}] {s.name}:")
            print(f"      Relevance: {s.expected_relevance}, Coherence: {s.expected_coherence}")

    finally:
        exporter.shutdown()


if __name__ == "__main__":
    main()
