#!/usr/bin/env python3
"""Seed three persona dashboards in Langfuse."""

import sys

from rich.console import Console
from rich.panel import Panel

from src.config import load_config, load_env

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


def seed_dashboards() -> int:
    env = load_env()
    cfg = load_config()

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print("[yellow]No Langfuse keys - emitting manual checklist[/yellow]")
        _emit_manual_dashboards()
        return 0

    try:
        import httpx
    except ImportError:
        console.print("[red]httpx not installed[/red]")
        return 1

    host = env.langfuse_host or cfg.langfuse.host

    # Attempt programmatic dashboard creation via Langfuse API
    # If endpoint not available, emit manual instructions
    success_count = 0
    for dash in DASHBOARDS:
        try:
            resp = httpx.post(
                f"{host}/api/public/dashboards",
                auth=(env.langfuse_public_key, env.langfuse_secret_key),
                json={"name": dash["name"], "description": dash["description"]},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                console.print(f"[green]Dashboard created: {dash['name']}[/green]")
                success_count += 1
            elif resp.status_code == 409:
                console.print(f"[yellow]Dashboard already exists: {dash['name']}[/yellow]")
                success_count += 1
            else:
                console.print(f"[yellow]Dashboard API returned {resp.status_code} for '{dash['name']}' - needs manual setup[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Dashboard API failed: {e}[/yellow]")

    if success_count < len(DASHBOARDS):
        console.print("\n[yellow]Dashboard API not available. Emitting manual instructions:[/yellow]")
        _emit_manual_dashboards()

    console.print(f"\n[bold]Dashboards: {success_count}/{len(DASHBOARDS)} via API[/bold]")
    return 0


if __name__ == "__main__":
    sys.exit(seed_dashboards())
