#!/usr/bin/env python3
"""
HyperDX Dashboard Creator for LLM Observability

Creates dashboards using the HyperDX External API v2.

IMPORTANT LIMITATION:
    The External API v2 only supports 'logs' and 'metrics' data sources.
    It does NOT support 'traces' (otel_traces table) where LLM data is stored.

    For LLM observability dashboards that need traces data, use:
    - scripts/create-hyperdx-dashboard-mongo.sh (MongoDB direct insert)
    - See docs/hyperdx-dashboard-api.md for details

Usage (inside ClickStack container):
    docker exec clickstack python3 -c "$(cat scripts/create-hyperdx-dashboard.py)" --list

Or copy and run:
    docker cp scripts/create-hyperdx-dashboard.py clickstack:/tmp/
    docker exec clickstack python3 /tmp/create-hyperdx-dashboard.py --api-key YOUR_KEY --list

Environment Variables:
    HYPERDX_API_URL: HyperDX API URL (default: http://localhost:8000)
    HYPERDX_API_KEY: Your Personal API Key from HyperDX Team Settings

The Personal API Key can be found:
    - In HyperDX UI under Team Settings > API Keys
    - In MongoDB: db.users.findOne({}).accessKey

API Reference:
    - External API v2: https://clickhouse.com/docs/clickstack/api-reference
    - See docs/hyperdx-dashboard-api.md for internal format details
"""

import os
import sys
import json
import argparse
import requests
from typing import Optional


class Config:
    """Configuration for HyperDX API."""
    api_url: str = os.getenv("HYPERDX_API_URL", "http://localhost:8000")
    api_key: str = os.getenv("HYPERDX_API_KEY", "")


config = Config()


def get_headers() -> dict:
    """Get headers for API requests."""
    if not config.api_key:
        raise ValueError(
            "HYPERDX_API_KEY environment variable is required.\n"
            "Get your Personal API Key from HyperDX Team Settings."
        )
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


def list_dashboards() -> list:
    """List all existing dashboards."""
    response = requests.get(
        f"{config.api_url}/api/v2/dashboards",
        headers=get_headers(),
    )
    response.raise_for_status()
    return response.json().get("data", [])


def delete_dashboard(dashboard_id: str) -> bool:
    """Delete a dashboard by ID."""
    response = requests.delete(
        f"{config.api_url}/api/v2/dashboards/{dashboard_id}",
        headers=get_headers(),
    )
    return response.status_code == 200


def create_dashboard(name: str, tiles: list, tags: list = None) -> dict:
    """Create a new dashboard."""
    payload = {
        "name": name,
        "tiles": tiles,
        "tags": tags or [],
    }
    response = requests.post(
        f"{config.api_url}/api/v2/dashboards",
        headers=get_headers(),
        json=payload,
    )
    response.raise_for_status()
    return response.json().get("data", {})


def create_llm_observability_dashboard() -> dict:
    """
    Create the LLM Observability Dashboard.

    Maps the SQL queries from llm-observability-queries.sql to HyperDX tiles.

    HyperDX uses Lucene query syntax for filtering, not SQL.
    The data model maps OpenTelemetry span attributes to searchable fields.
    """

    # Dashboard layout configuration
    # Grid is 12 units wide, each row can have multiple tiles

    tiles = [
        # =======================================================================
        # ROW 1: Summary Metrics (Single Values)
        # =======================================================================
        {
            "name": "Total LLM Requests (24h)",
            "x": 0, "y": 0, "w": 3, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "count",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },
        {
            "name": "Total Input Tokens (24h)",
            "x": 3, "y": 0, "w": 3, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "sum",
                "field": "gen_ai.usage.input_tokens",
                "where": "gen_ai.usage.input_tokens:*",
                "groupBy": [],
            }],
        },
        {
            "name": "Total Output Tokens (24h)",
            "x": 6, "y": 0, "w": 3, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "sum",
                "field": "gen_ai.usage.output_tokens",
                "where": "gen_ai.usage.output_tokens:*",
                "groupBy": [],
            }],
        },
        {
            "name": "Avg Latency (ms)",
            "x": 9, "y": 0, "w": 3, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "avg",
                "field": "duration",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },

        # =======================================================================
        # ROW 2: Time Series Charts
        # =======================================================================
        {
            "name": "LLM Requests Over Time",
            "x": 0, "y": 2, "w": 6, "h": 3,
            "series": [{
                "type": "time",
                "dataSource": "events",
                "aggFn": "count",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },
        {
            "name": "Token Usage Over Time",
            "x": 6, "y": 2, "w": 6, "h": 3,
            "series": [
                {
                    "type": "time",
                    "dataSource": "events",
                    "aggFn": "sum",
                    "field": "gen_ai.usage.input_tokens",
                    "where": "gen_ai.usage.input_tokens:*",
                    "groupBy": [],
                },
                {
                    "type": "time",
                    "dataSource": "events",
                    "aggFn": "sum",
                    "field": "gen_ai.usage.output_tokens",
                    "where": "gen_ai.usage.output_tokens:*",
                    "groupBy": [],
                },
            ],
        },

        # =======================================================================
        # ROW 3: Latency Charts
        # =======================================================================
        {
            "name": "P50 Latency Over Time",
            "x": 0, "y": 5, "w": 4, "h": 3,
            "series": [{
                "type": "time",
                "dataSource": "events",
                "aggFn": "quantile",
                "field": "duration",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },
        {
            "name": "P95 Latency Over Time",
            "x": 4, "y": 5, "w": 4, "h": 3,
            "series": [{
                "type": "time",
                "dataSource": "events",
                "aggFn": "quantile",
                "field": "duration",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },
        {
            "name": "P99 Latency Over Time",
            "x": 8, "y": 5, "w": 4, "h": 3,
            "series": [{
                "type": "time",
                "dataSource": "events",
                "aggFn": "quantile",
                "field": "duration",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },

        # =======================================================================
        # ROW 4: Breakdown Tables
        # =======================================================================
        {
            "name": "Requests by Model",
            "x": 0, "y": 8, "w": 6, "h": 3,
            "series": [{
                "type": "table",
                "dataSource": "events",
                "aggFn": "count",
                "where": "gen_ai.request.model:*",
                "groupBy": ["gen_ai.request.model"],
            }],
        },
        {
            "name": "Requests by Service",
            "x": 6, "y": 8, "w": 6, "h": 3,
            "series": [{
                "type": "table",
                "dataSource": "events",
                "aggFn": "count",
                "where": "gen_ai.request.model:*",
                "groupBy": ["service"],
            }],
        },

        # =======================================================================
        # ROW 5: Service Breakdown Over Time
        # =======================================================================
        {
            "name": "Requests by Service Over Time",
            "x": 0, "y": 11, "w": 12, "h": 3,
            "series": [{
                "type": "time",
                "dataSource": "events",
                "aggFn": "count",
                "where": "gen_ai.request.model:*",
                "groupBy": ["service"],
            }],
        },

        # =======================================================================
        # ROW 6: Evaluation Metrics (if available)
        # =======================================================================
        {
            "name": "Avg Relevance Score",
            "x": 0, "y": 14, "w": 4, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "avg",
                "field": "eval.relevance_score",
                "where": "eval.relevance_score:*",
                "groupBy": [],
            }],
        },
        {
            "name": "Avg Coherence Score",
            "x": 4, "y": 14, "w": 4, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "avg",
                "field": "eval.coherence_score",
                "where": "eval.coherence_score:*",
                "groupBy": [],
            }],
        },
        {
            "name": "Total Evaluations",
            "x": 8, "y": 14, "w": 4, "h": 2,
            "series": [{
                "type": "number",
                "dataSource": "events",
                "aggFn": "count",
                "where": "eval.relevance_score:*",
                "groupBy": [],
            }],
        },

        # =======================================================================
        # ROW 7: Evaluation Trends
        # =======================================================================
        {
            "name": "Evaluation Scores Over Time",
            "x": 0, "y": 16, "w": 12, "h": 3,
            "series": [
                {
                    "type": "time",
                    "dataSource": "events",
                    "aggFn": "avg",
                    "field": "eval.relevance_score",
                    "where": "eval.relevance_score:*",
                    "groupBy": [],
                },
                {
                    "type": "time",
                    "dataSource": "events",
                    "aggFn": "avg",
                    "field": "eval.coherence_score",
                    "where": "eval.coherence_score:*",
                    "groupBy": [],
                },
            ],
        },

        # =======================================================================
        # ROW 8: Recent Activity (Search)
        # =======================================================================
        {
            "name": "Recent LLM Calls",
            "x": 0, "y": 19, "w": 12, "h": 4,
            "series": [{
                "type": "search",
                "dataSource": "events",
                "aggFn": "count",
                "where": "gen_ai.request.model:*",
                "groupBy": [],
            }],
        },
    ]

    return create_dashboard(
        name="LLM Observability Dashboard",
        tiles=tiles,
        tags=["llm", "observability", "gen-ai", "auto-generated"],
    )


def find_dashboard_by_name(name: str) -> Optional[dict]:
    """Find a dashboard by name."""
    dashboards = list_dashboards()
    for dashboard in dashboards:
        if dashboard.get("name") == name:
            return dashboard
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Create LLM Observability Dashboard in HyperDX"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing dashboards",
    )
    parser.add_argument(
        "--delete",
        metavar="ID",
        help="Delete a dashboard by ID",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete existing LLM dashboard and create new one",
    )
    parser.add_argument(
        "--api-url",
        default=config.api_url,
        help=f"HyperDX API URL (default: {config.api_url})",
    )
    parser.add_argument(
        "--api-key",
        default=config.api_key,
        help="HyperDX Personal API Key",
    )

    args = parser.parse_args()

    # Update config from args
    config.api_url = args.api_url
    if args.api_key:
        config.api_key = args.api_key

    try:
        if args.list:
            dashboards = list_dashboards()
            print(f"Found {len(dashboards)} dashboard(s):\n")
            for d in dashboards:
                print(f"  ID: {d['id']}")
                print(f"  Name: {d['name']}")
                print(f"  Tags: {', '.join(d.get('tags', []))}")
                print(f"  Tiles: {len(d.get('tiles', []))}")
                print()
            return

        if args.delete:
            if delete_dashboard(args.delete):
                print(f"Deleted dashboard: {args.delete}")
            else:
                print(f"Failed to delete dashboard: {args.delete}")
            return

        # Check for existing dashboard
        existing = find_dashboard_by_name("LLM Observability Dashboard")
        if existing:
            if args.recreate:
                print(f"Deleting existing dashboard: {existing['id']}")
                delete_dashboard(existing["id"])
            else:
                print(f"Dashboard already exists: {existing['id']}")
                print("Use --recreate to delete and recreate it.")
                return

        # Create the dashboard
        print("Creating LLM Observability Dashboard...")
        dashboard = create_llm_observability_dashboard()

        print(f"\nDashboard created successfully!")
        print(f"  ID: {dashboard['id']}")
        print(f"  Name: {dashboard['name']}")
        print(f"  Tiles: {len(dashboard.get('tiles', []))}")
        print(f"  Tags: {', '.join(dashboard.get('tags', []))}")
        print(f"\nView at: http://localhost:8080/dashboards/{dashboard['id']}")

    except requests.exceptions.HTTPError as e:
        print(f"API Error: {e}")
        print(f"Response: {e.response.text if e.response else 'No response'}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
