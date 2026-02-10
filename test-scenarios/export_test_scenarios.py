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
    ground_truth: str  # The correct answer — sent as expected_output for evaluators
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
        name="off-topic-response",
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
        ground_truth="""ClickHouse Cloud offers three pricing tiers: (1) Development — for small workloads and experimentation, starting at $0.10/hr for compute; (2) Production — for business-critical workloads with SLA guarantees, higher availability, and auto-scaling; (3) Dedicated — for enterprises needing isolated infrastructure, custom configurations, and premium support. All tiers charge separately for compute (per hour) and storage (per GB/month). A free trial tier is also available.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.2-0.4",
        expected_coherence="0.9-1.0",
        why_low="Response discusses ClickHouse features but completely ignores the pricing question",
        tags=["test-scenario", "off-topic-response", "relevance-test"]
    ),

    # -------------------------------------------------------------------------
    # Scenario 2: Low Coherence (Contradictory Response)
    # -------------------------------------------------------------------------
    TestScenario(
        id=2,
        name="contradictory-response",
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
        ground_truth="""For analytics workloads, ClickHouse is generally the better choice. ClickHouse uses columnar storage optimized for OLAP queries — it can scan billions of rows per second and achieves excellent compression. PostgreSQL uses row-based storage designed for OLTP (transactions), which is slower for large analytical scans. Choose ClickHouse if your primary need is fast aggregations over large datasets. Choose PostgreSQL if you need a mix of transactional and light analytical workloads, or if your data volume is small. They can also complement each other — PostgreSQL for application data, ClickHouse for analytics.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.5-0.7",
        expected_coherence="0.1-0.3",
        why_low="Response repeatedly contradicts itself, making it impossible to extract a clear answer",
        tags=["test-scenario", "contradictory-response", "coherence-test"]
    ),

    # -------------------------------------------------------------------------
    # Scenario 3: Hallucination (Fabricated Information)
    # -------------------------------------------------------------------------
    TestScenario(
        id=3,
        name="fabricated-information",
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
        ground_truth="""ClickHouse was created by Alexey Milovidov at Yandex, the Russian search engine company. It was developed internally starting around 2009 to power Yandex.Metrica, one of the world's largest web analytics platforms. Key milestones: 2009 — development began at Yandex; 2016 — open-sourced under Apache 2.0 license on GitHub; 2021 — ClickHouse Inc. was founded as an independent company with $50M Series A; 2022 — raised $250M Series B at $2B valuation. The project is maintained by ClickHouse Inc., headquartered in San Francisco, with a large open-source community.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.8-1.0",
        expected_coherence="0.9-1.0",
        why_low="Response is relevant and coherent but contains entirely fabricated history (ClickHouse was actually created at Yandex by Alexey Milovidov, open-sourced in 2016)",
        tags=["test-scenario", "fabricated-information", "hallucination-test"]
    ),

    # -------------------------------------------------------------------------
    # Scenario 4: Good Response (Control)
    # -------------------------------------------------------------------------
    TestScenario(
        id=4,
        name="good-response-control",
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
        ground_truth="""ClickHouse's main advantages for analytics: (1) Columnar storage — reads only needed columns, dramatically faster for analytical queries; (2) Vectorized execution — processes data in batches using SIMD CPU instructions; (3) Real-time ingestion — handles millions of inserts/second while serving queries; (4) Compression — columnar layout achieves 10x+ compression ratios; (5) SQL interface — standard SQL with analytical extensions; (6) Horizontal scalability — distributed queries across shards; (7) Open-source — no licensing costs, Apache 2.0 license. Well-suited for log analysis, time-series, BI, and large-scale aggregations.""",
        model="claude-sonnet-4-20250514",
        expected_relevance="0.9-1.0",
        expected_coherence="0.9-1.0",
        why_low="This is a control scenario - scores should be high",
        tags=["test-scenario", "good-response-control", "control"]
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
            print("Langfuse not configured - skipping export")
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

            with propagate_attributes(
                session_id=session_id,
                trace_name=scenario.name,
                tags=scenario.tags,
            ):
                with self._client.start_as_current_observation(
                    as_type="span",
                    name=f"test-scenario-{scenario.id}",
                    input=scenario.prompt,
                    metadata={
                        "scenario_id": scenario.id,
                        "scenario_name": scenario.name,
                        "category": scenario.category,
                        "ground_truth": scenario.ground_truth,
                        "expected_relevance": scenario.expected_relevance,
                        "expected_coherence": scenario.expected_coherence,
                        "why_low": scenario.why_low,
                        "source": "test-scenarios",
                    },
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
                print(f"  [{scenario.id}] {scenario.name}")
        return trace_ids

    def flush(self):
        """Flush pending events."""
        if self._client and hasattr(self._client, 'flush'):
            self._client.flush()

    def shutdown(self):
        """Shutdown the client."""
        self.flush()


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
    print(f"Exporting {len(scenarios_to_export)} scenario(s) to Langfuse...")
    print()

    exporter = LangfuseExporter()

    try:
        exporter.setup()

        if not exporter.is_enabled():
            print("\nLangfuse is not configured. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
            sys.exit(1)

        print("\nExporting scenarios:")
        trace_ids = exporter.export_scenarios(scenarios_to_export)
        exporter.flush()

        print("\n" + "-" * 70)
        print(f"Exported {len(trace_ids)} scenarios to Langfuse")
        print("-" * 70)

        print("\nNext steps:")
        print("  1. View traces in Langfuse: http://localhost:3001")
        print("     Filter by tag: test-scenario")
        print()
        print("  2. Configure LLM-as-a-Judge evaluators:")
        print("     Langfuse UI -> Evaluations -> LLM-as-a-Judge")
        print()

        print("Expected results:")
        for s in scenarios_to_export:
            print(f"  [{s.id}] {s.name}:")
            print(f"      Relevance: {s.expected_relevance}, Coherence: {s.expected_coherence}")

    finally:
        exporter.shutdown()


if __name__ == "__main__":
    main()
