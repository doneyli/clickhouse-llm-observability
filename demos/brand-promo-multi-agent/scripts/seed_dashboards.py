#!/usr/bin/env python3
"""Check the three persona dashboards in Langfuse, and print how to build any that are missing.

The dashboards are built by hand in the UI: their widgets need the dashboard-widget API,
which this demo does not seed. This script reads the dashboards back by name through the
unstable dashboards API and prints the manual checklist for the missing ones.

Exit codes: 0 all three exist; 2 one or more still need the manual step (or the server
has no dashboards API to read them back); 1 the read failed or no keys are set.
"""

import sys

from rich.console import Console
from rich.panel import Panel

from src.config import load_env
from src.langfuse_rest import ApiError, Client, LangfuseEnvError

MANUAL = 2

console = Console()

DASHBOARDS = [
    {
        "name": "Executive - Agent Fleet",
        "description": "High-level fleet performance for executive stakeholders",
        "widgets": [
            {"title": "Total Invocations (Last 7 Days)", "type": "numeric", "metric": "trace_count", "days": 7},
            {"title": "Error Rate Trend (30 Days)", "type": "line_chart", "metric": "error_rate", "days": 30},
            {"title": "Cost Trend by Agent (30 Days)", "type": "stacked_line", "metric": "cost", "group_by": "agent_name", "days": 30},
            {"title": "Top 5 Failing Flows (24h)", "type": "table", "metric": "errors", "days": 1, "limit": 5},
            {"title": "Invocations by Agent", "type": "bar_chart", "metric": "trace_count", "group_by": "agent_name"},
        ],
    },
    {
        "name": "Ops - Agent Health",
        "description": "Operational health metrics for the on-call team",
        "widgets": [
            {"title": "Latency p50/p95/p99 by Agent", "type": "multi_line", "metrics": ["p50", "p95", "p99"], "group_by": "agent_name"},
            {"title": "Throughput per Agent per Hour", "type": "heatmap", "metric": "trace_count", "group_by": "agent_name", "bucket": "hour"},
            {"title": "Error Rate by Tool", "type": "bar_chart", "metric": "error_rate", "group_by": "tool_name"},
            {"title": "Top 10 Slowest Traces (24h)", "type": "table", "metric": "latency", "days": 1, "limit": 10},
        ],
    },
    {
        "name": "Engineer - PromoPlanner Deep Dive",
        "description": "Deep dive into PromoPlanner quality metrics for AI engineers",
        "filter": {"agent_name": "PromoPlanner"},
        "widgets": [
            {"title": "Score: Tool-Call Correctness", "type": "histogram", "score": "tool-call-correctness"},
            {"title": "Score: Response Factuality", "type": "histogram", "score": "response-factuality"},
            {"title": "Score: Compliance Adherence", "type": "histogram", "score": "compliance-adherence"},
            {"title": "Cost by Model Tier", "type": "stacked_bar", "metric": "cost", "group_by": "model"},
            {"title": "Trace Volume by Intent", "type": "bar_chart", "metric": "trace_count", "group_by": "intent"},
            {"title": "Recent Failed Traces", "type": "table", "metric": "errors", "days": 7, "limit": 20},
        ],
    },
]


def _emit_manual_dashboards() -> None:
    console.print(
        Panel(
            "[bold yellow]MANUAL: Create Dashboards in Langfuse UI[/bold yellow]\n\n"
            "The Langfuse v3 dashboard API does not support programmatic creation.\n"
            "Create the following dashboards manually via the UI:\n\n"
            "Navigate to: Dashboards -> New Dashboard\n\n"
            "=== Dashboard 1: Executive - Agent Fleet ===\n"
            "Widgets to add:\n"
            "  1. Number card: Total trace count, last 7 days\n"
            "  2. Line chart: Error rate over 30 days\n"
            "  3. Line chart: Cost over 30 days, grouped by metadata.agent_name\n"
            "  4. Table: Traces with error status, last 24h, top 5\n"
            "  5. Bar chart: Trace count grouped by metadata.agent_name\n\n"
            "=== Dashboard 2: Ops - Agent Health ===\n"
            "Widgets to add:\n"
            "  1. Multi-line chart: Latency percentiles (p50/p95/p99) by agent\n"
            "  2. Bar chart: Trace count per hour, grouped by agent\n"
            "  3. Bar chart: Error rate grouped by span name (tool calls)\n"
            "  4. Table: Slowest 10 traces, last 24h\n\n"
            "=== Dashboard 3: Engineer - PromoPlanner Deep Dive ===\n"
            "Filter: metadata.agent_name = PromoPlanner\n"
            "Widgets to add:\n"
            "  1. Histogram: Score 'tool-call-correctness'\n"
            "  2. Histogram: Score 'response-factuality'\n"
            "  3. Histogram: Score 'compliance-adherence'\n"
            "  4. Stacked bar: Cost grouped by model\n"
            "  5. Bar chart: Trace count grouped by metadata.intent\n"
            "  6. Table: Failed traces last 7 days",
            title="Manual Dashboard Setup",
        )
    )


def seed_dashboards(client: Client | None = None) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1

    try:
        names = {d.get("name") for d in client.paged("/api/public/unstable/dashboards")}
    except ApiError as e:
        if e.status == 404:
            console.print("[yellow]This server has no dashboards API; check the dashboards in the UI.[/yellow]")
            _emit_manual_dashboards()
            return MANUAL
        console.print(f"[red]Dashboard read failed: {e}[/red]")
        return 1

    missing = [d["name"] for d in DASHBOARDS if d["name"] not in names]
    for d in DASHBOARDS:
        state = "missing (manual)" if d["name"] in missing else "present"
        console.print(f"  {d['name']}: {state}")
    console.print(f"\n[bold]Dashboards: {len(DASHBOARDS) - len(missing)}/{len(DASHBOARDS)} present[/bold]")
    if missing:
        _emit_manual_dashboards()
        return MANUAL
    return 0


if __name__ == "__main__":
    sys.exit(seed_dashboards())
