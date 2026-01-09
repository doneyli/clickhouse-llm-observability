"""
Trace Evaluator - Async LLM Quality Evaluation from HyperDX Traces

This service queries LLM traces from ClickHouse (HyperDX backend) and runs
TruLens quality evaluations on them asynchronously.

Usage:
    python main.py                              # One-time evaluation
    python main.py --watch --interval 60        # Continuous watch mode
    python main.py --service librechat-api --hours 24 --limit 50
    python main.py --list-services              # Show available services

Environment Variables:
    CLICKHOUSE_TRACE_HOST     - ClickHouse host (default: clickstack)
    CLICKHOUSE_TRACE_PORT     - ClickHouse HTTP port (default: 8123)
    TRULENS_DATABASE_URL      - TruLens SQLite URL
    TRULENS_MODEL             - Model for evaluations (default: claude-3-5-haiku-20241022)
    ANTHROPIC_API_KEY         - Anthropic API key
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Set

# Disable TruLens OTEL tracing - it's incompatible with TruVirtual
# Must be done before importing trulens
os.environ["TRULENS_OTEL_TRACING"] = "false"

# Try to disable via the Feature API as well
try:
    from trulens.core.experimental import Feature
    Feature.OTEL_TRACING.disable()
except Exception:
    pass

from clickhouse_client import ClickHouseTraceClient
from trulens_evaluator import TraceEvaluator


# File to persist evaluated trace IDs (prevents duplicates across restarts)
EVALUATED_IDS_FILE = os.getenv("EVALUATED_IDS_FILE", "/tmp/trace_evaluator_ids.json")


def load_evaluated_ids() -> Set[str]:
    """Load previously evaluated trace IDs from file."""
    try:
        if Path(EVALUATED_IDS_FILE).exists():
            with open(EVALUATED_IDS_FILE, "r") as f:
                data = json.load(f)
                ids = set(data.get("evaluated_ids", []))
                print(f"Loaded {len(ids)} previously evaluated trace IDs")
                return ids
    except Exception as e:
        print(f"Warning: Could not load evaluated IDs: {e}")
    return set()


def save_evaluated_ids(ids: Set[str]):
    """Save evaluated trace IDs to file."""
    try:
        # Keep only the most recent 10000 IDs to prevent unbounded growth
        ids_list = list(ids)[-10000:]
        with open(EVALUATED_IDS_FILE, "w") as f:
            json.dump({"evaluated_ids": ids_list}, f)
    except Exception as e:
        print(f"Warning: Could not save evaluated IDs: {e}")


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
    ch_client: ClickHouseTraceClient,
    evaluator: TraceEvaluator,
    service_name: str,
    hours_ago: int = 1,
    limit: int = 100,
    sample_rate: float = 1.0,
    evaluated_ids: Set[str] = None,
) -> int:
    """
    Run evaluation on new traces.

    Args:
        ch_client: Connected ClickHouse client
        evaluator: Initialized TruLens evaluator
        service_name: Service to evaluate traces from
        hours_ago: How far back to look for traces
        limit: Maximum traces to fetch
        sample_rate: Fraction of traces to evaluate (0.0-1.0)
        evaluated_ids: Set of already-evaluated trace IDs to skip

    Returns:
        Number of traces evaluated
    """
    if evaluated_ids is None:
        evaluated_ids = set()

    # Fetch traces
    traces = ch_client.get_llm_traces(
        service_name=service_name,
        hours_ago=hours_ago,
        limit=limit,
    )

    if not traces:
        return 0

    # Filter out already-evaluated traces
    new_traces = [t for t in traces if t.trace_id not in evaluated_ids]

    if not new_traces:
        return 0

    print(f"Found {len(new_traces)} new traces to evaluate")

    # Run evaluations
    results = evaluator.evaluate_traces(new_traces, sample_rate=sample_rate)

    # Mark traces as evaluated
    for trace in new_traces:
        evaluated_ids.add(trace.trace_id)

    return len(results)


def run_once(
    service_name: str = "librechat-api",
    hours_ago: int = 24,
    limit: int = 100,
    sample_rate: float = 1.0,
    app_name: str = None,
):
    """
    Run one-time evaluation pipeline on LLM traces.

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


def watch_mode(
    service_name: str,
    interval: int = 60,
    hours_ago: int = 1,
    limit: int = 50,
    sample_rate: float = 1.0,
    app_name: str = None,
):
    """
    Continuously watch for new traces and evaluate them.

    Args:
        service_name: Service to evaluate traces from
        interval: Seconds between polls
        hours_ago: How far back to look each poll
        limit: Maximum traces per poll
        sample_rate: Fraction of traces to evaluate
        app_name: Name for this evaluation app in TruLens
    """
    print("\n" + "=" * 60)
    print("TRACE EVALUATOR - Watch Mode")
    print("=" * 60)
    print(f"Service: {service_name}")
    print(f"Poll interval: {interval} seconds")
    print(f"Lookback: {hours_ago} hour(s)")
    print(f"Sample rate: {sample_rate * 100:.1f}%")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    # Initialize ClickHouse client
    print("\nConnecting to ClickHouse...")
    ch_client = ClickHouseTraceClient()
    try:
        ch_client.connect()
    except Exception as e:
        print(f"ERROR: Could not connect to ClickHouse: {e}")
        sys.exit(1)

    # Initialize evaluator
    print("Initializing TruLens evaluator...")
    eval_app_name = app_name or f"{service_name}-eval"
    evaluator = TraceEvaluator(app_name=eval_app_name)
    try:
        evaluator.initialize()
    except Exception as e:
        print(f"ERROR: Could not initialize TruLens: {e}")
        sys.exit(1)

    # Load previously evaluated IDs
    evaluated_ids = load_evaluated_ids()
    total_evaluated = 0

    print("\nWatching for new traces...\n")

    try:
        while True:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Checking for new traces...")

            try:
                count = run_evaluation(
                    ch_client=ch_client,
                    evaluator=evaluator,
                    service_name=service_name,
                    hours_ago=hours_ago,
                    limit=limit,
                    sample_rate=sample_rate,
                    evaluated_ids=evaluated_ids,
                )

                if count > 0:
                    total_evaluated += count
                    save_evaluated_ids(evaluated_ids)
                    print(f"Evaluated {count} new traces (total: {total_evaluated})")
                else:
                    print("No new traces to evaluate")

            except Exception as e:
                print(f"Error during evaluation: {e}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\nStopping watch mode. Total evaluated: {total_evaluated}")
        save_evaluated_ids(evaluated_ids)
    finally:
        ch_client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate LLM traces from HyperDX using TruLens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                                  # One-time evaluation
  python main.py --watch --interval 60            # Watch mode (poll every 60s)
  python main.py --service librechat-conversations --hours 1
  python main.py --sample-rate 0.1                # Evaluate 10% sample
  python main.py --list-services                  # Show available services
        """
    )

    # Actions
    parser.add_argument(
        "--watch", "-w",
        action="store_true",
        help="Continuously watch for new traces and evaluate them"
    )
    parser.add_argument(
        "--list-services",
        action="store_true",
        help="List available services with LLM traces"
    )

    # Service selection
    parser.add_argument(
        "--service", "-s",
        default="librechat-conversations",
        help="Service name to evaluate (default: librechat-conversations)"
    )

    # Time and limits
    parser.add_argument(
        "--hours", "-H",
        type=int,
        default=24,
        help="How many hours back to look (default: 24, watch mode: 1)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=100,
        help="Maximum traces to fetch (default: 100)"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=60,
        help="Seconds between polls in watch mode (default: 60)"
    )

    # Sampling and naming
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

    args = parser.parse_args()

    # List services mode
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

    # Watch mode
    if args.watch:
        watch_mode(
            service_name=args.service,
            interval=args.interval,
            hours_ago=min(args.hours, 2),  # Cap lookback in watch mode
            limit=args.limit,
            sample_rate=args.sample_rate,
            app_name=args.app_name,
        )
        return

    # One-time evaluation
    run_once(
        service_name=args.service,
        hours_ago=args.hours,
        limit=args.limit,
        sample_rate=args.sample_rate,
        app_name=args.app_name,
    )


if __name__ == "__main__":
    main()
