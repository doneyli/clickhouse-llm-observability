"""
Trace Evaluator - Async LLM Quality Evaluation from HyperDX Traces

This service queries LLM traces from ClickHouse (HyperDX backend) and runs
TruLens quality evaluations on them asynchronously.

Usage:
    python main.py                    # Evaluate all services (default)
    python main.py --service librechat-api --hours 24 --limit 50
    python main.py --list-services    # Show available services
    python main.py --sample-rate 0.05 # Evaluate 5% sample

Environment Variables:
    CLICKHOUSE_TRACE_HOST     - ClickHouse host (default: clickstack)
    CLICKHOUSE_TRACE_PORT     - ClickHouse HTTP port (default: 8123)
    TRULENS_DATABASE_URL      - TruLens SQLite URL
    TRULENS_MODEL             - Model for evaluations (default: claude-3-5-haiku-20241022)
    ANTHROPIC_API_KEY         - Anthropic API key
"""

import os
import sys
import argparse
from datetime import datetime

# Setup instrumentation before other imports
from instrumentation import setup_instrumentation
setup_instrumentation()

from clickhouse_client import ClickHouseTraceClient
from trulens_evaluator import TraceEvaluator


def list_services(client: ClickHouseTraceClient):
    """List all services with LLM traces."""
    print("\n=== Services with LLM Traces ===\n")

    services = client.get_available_services()
    if not services:
        print("No services found with LLM traces.")
        print("Make sure LibreChat or other LLM apps have sent traces to HyperDX.")
        return

    for svc in services:
        count_24h = client.get_trace_count(svc, hours_ago=24)
        count_1h = client.get_trace_count(svc, hours_ago=1)
        print(f"  {svc}")
        print(f"    Last 1h: {count_1h} traces")
        print(f"    Last 24h: {count_24h} traces")
        print()


def run_evaluation(
    service_name: str = "librechat-api",
    hours_ago: int = 24,
    limit: int = 100,
    sample_rate: float = 1.0,
    app_name: str = None,
):
    """
    Run evaluation pipeline on LLM traces.

    Args:
        service_name: Service to evaluate traces from
        hours_ago: How far back to look for traces
        limit: Maximum traces to fetch
        sample_rate: Fraction of traces to evaluate (0.0-1.0)
        app_name: Name for this evaluation app in TruLens
    """
    print("\n" + "=" * 60)
    print("TRACE EVALUATOR - Async LLM Quality Evaluation")
    print("=" * 60)
    print(f"Service: {service_name}")
    print(f"Time range: Last {hours_ago} hours")
    print(f"Max traces: {limit}")
    print(f"Sample rate: {sample_rate * 100:.1f}%")
    print("=" * 60)

    # Initialize ClickHouse client
    print("\n[1/4] Connecting to ClickHouse...")
    ch_client = ClickHouseTraceClient()
    try:
        ch_client.connect()
    except Exception as e:
        print(f"ERROR: Could not connect to ClickHouse: {e}")
        print("\nTroubleshooting:")
        print("  1. Make sure ClickStack is running")
        print("  2. Check CLICKHOUSE_TRACE_HOST and CLICKHOUSE_TRACE_PORT")
        print("  3. Ensure trace-evaluator is on the same Docker network as clickstack")
        sys.exit(1)

    # Check for traces
    trace_count = ch_client.get_trace_count(service_name, hours_ago)
    print(f"   Found {trace_count} LLM traces for {service_name}")

    if trace_count == 0:
        print(f"\nNo traces found for service '{service_name}'.")
        print("Available services:")
        list_services(ch_client)
        ch_client.close()
        return

    # Fetch traces
    print("\n[2/4] Fetching LLM traces from ClickHouse...")
    traces = ch_client.get_llm_traces(
        service_name=service_name,
        hours_ago=hours_ago,
        limit=limit,
    )
    ch_client.close()

    if not traces:
        print("No traces with prompt/completion pairs found.")
        return

    print(f"   Fetched {len(traces)} traces with prompt/completion pairs")

    # Preview traces
    print("\n   Sample traces:")
    for trace in traces[:3]:
        prompt_preview = trace.prompt[:80].replace('\n', ' ')
        print(f"   - {trace.trace_id[:12]}... | {prompt_preview}...")

    # Initialize evaluator
    print("\n[3/4] Initializing TruLens evaluator...")
    eval_app_name = app_name or f"{service_name}-eval"
    evaluator = TraceEvaluator(app_name=eval_app_name)
    try:
        evaluator.initialize()
    except Exception as e:
        print(f"ERROR: Could not initialize TruLens: {e}")
        print("\nTroubleshooting:")
        print("  1. Check ANTHROPIC_API_KEY is set")
        print("  2. Check TRULENS_DATABASE_URL points to shared volume")
        sys.exit(1)

    # Run evaluations
    print("\n[4/4] Running evaluations...")
    results = evaluator.evaluate_traces(traces, sample_rate=sample_rate)

    # Print summary
    evaluator.print_summary(results)

    # Final message
    print("\nEvaluation complete!")
    print("View results in TruLens Dashboard: http://localhost:8501")
    print("View traces in HyperDX: http://localhost:8080")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate LLM traces from HyperDX using TruLens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                           # Evaluate librechat-api traces
  python main.py --service mcp-clickhouse  # Evaluate specific service
  python main.py --hours 1 --limit 10      # Last hour, max 10 traces
  python main.py --sample-rate 0.05        # Evaluate 5% sample
  python main.py --list-services           # Show available services
        """
    )

    parser.add_argument(
        "--service", "-s",
        default="librechat-api",
        help="Service name to evaluate (default: librechat-api)"
    )
    parser.add_argument(
        "--hours", "-H",
        type=int,
        default=24,
        help="How many hours back to look (default: 24)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=100,
        help="Maximum traces to fetch (default: 100)"
    )
    parser.add_argument(
        "--sample-rate", "-r",
        type=float,
        default=1.0,
        help="Fraction of traces to evaluate, 0.0-1.0 (default: 1.0)"
    )
    parser.add_argument(
        "--app-name", "-a",
        help="Name for this evaluation app in TruLens (default: {service}-eval)"
    )
    parser.add_argument(
        "--list-services",
        action="store_true",
        help="List available services with LLM traces"
    )

    args = parser.parse_args()

    if args.list_services:
        client = ClickHouseTraceClient()
        try:
            client.connect()
            list_services(client)
        except Exception as e:
            print(f"ERROR: {e}")
        finally:
            client.close()
        return

    run_evaluation(
        service_name=args.service,
        hours_ago=args.hours,
        limit=args.limit,
        sample_rate=args.sample_rate,
        app_name=args.app_name,
    )


if __name__ == "__main__":
    main()
