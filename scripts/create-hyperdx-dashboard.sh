#!/bin/bash
#
# HyperDX Dashboard Creator for LLM Observability
#
# Creates a comprehensive LLM observability dashboard using the HyperDX API.
# Must be run from inside the ClickStack container or with API port exposed.
#
# Usage:
#   # List dashboards
#   docker exec clickstack /bin/sh -c 'API_KEY=your-key /scripts/create-dashboard.sh --list'
#
#   # Create dashboard
#   docker exec clickstack /bin/sh -c 'API_KEY=your-key /scripts/create-dashboard.sh --create'
#
# Or copy and run:
#   docker cp scripts/create-hyperdx-dashboard.sh clickstack:/scripts/create-dashboard.sh
#   docker exec clickstack /bin/sh /scripts/create-dashboard.sh --api-key YOUR_KEY --list
#

set -e

# Configuration
API_URL="${HYPERDX_API_URL:-http://localhost:8000}"
API_KEY="${HYPERDX_API_KEY:-}"

# Parse arguments
ACTION=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --api-key)
            API_KEY="$2"
            shift 2
            ;;
        --list)
            ACTION="list"
            shift
            ;;
        --create)
            ACTION="create"
            shift
            ;;
        --delete)
            ACTION="delete"
            DASHBOARD_ID="$2"
            shift 2
            ;;
        --recreate)
            ACTION="recreate"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$API_KEY" ]; then
    echo "Error: API key required. Use --api-key or set HYPERDX_API_KEY"
    echo ""
    echo "Get your Personal API Key from:"
    echo "  - HyperDX UI: Team Settings > API Keys"
    echo "  - MongoDB: db.users.findOne({}).accessKey"
    exit 1
fi

if [ -z "$ACTION" ]; then
    echo "Usage: $0 [--api-key KEY] [--api-url URL] [--list|--create|--delete ID|--recreate]"
    exit 1
fi

# API functions
list_dashboards() {
    curl -s "${API_URL}/api/v2/dashboards" \
        -H "Authorization: Bearer ${API_KEY}" \
        -H "Content-Type: application/json"
}

delete_dashboard() {
    local id="$1"
    curl -s -X DELETE "${API_URL}/api/v2/dashboards/${id}" \
        -H "Authorization: Bearer ${API_KEY}" \
        -H "Content-Type: application/json"
}

create_dashboard() {
    local payload="$1"
    curl -s -X POST "${API_URL}/api/v2/dashboards" \
        -H "Authorization: Bearer ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$payload"
}

# Get the Traces source ID from MongoDB
get_traces_source_id() {
    # Default traces source ID - override with --source-id if different
    echo "${TRACES_SOURCE_ID:-696018e0111b88a75f8b3677}"
}

# LLM Observability Dashboard definition
# Requires sourceId to link tiles to the correct data source (otel_traces)
get_dashboard_payload() {
    local source_id=$(get_traces_source_id)
    cat <<EOF
{
  "name": "LLM Observability Dashboard",
  "tags": ["llm", "observability", "gen-ai", "auto-generated"],
  "tiles": [
    {
      "name": "Total LLM Requests (24h)",
      "x": 0, "y": 0, "w": 3, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "gen_ai.request.model:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "Total Input Tokens (24h)",
      "x": 3, "y": 0, "w": 3, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "sum",
        "field": "gen_ai.usage.input_tokens",
        "where": "gen_ai.usage.input_tokens:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "Total Output Tokens (24h)",
      "x": 6, "y": 0, "w": 3, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "sum",
        "field": "gen_ai.usage.output_tokens",
        "where": "gen_ai.usage.output_tokens:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "Avg Latency (ms)",
      "x": 9, "y": 0, "w": 3, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "avg",
        "field": "duration",
        "where": "gen_ai.request.model:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "LLM Requests Over Time",
      "x": 0, "y": 2, "w": 6, "h": 3,
      "series": [{
        "type": "time",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "gen_ai.request.model:*",
        "groupBy": []
      }]
    },
    {
      "name": "Token Usage Over Time",
      "x": 6, "y": 2, "w": 6, "h": 3,
      "series": [
        {
          "type": "time",
          "dataSource": "events",
          "sourceId": "${source_id}",
          "aggFn": "sum",
          "field": "gen_ai.usage.input_tokens",
          "where": "gen_ai.usage.input_tokens:*",
          "groupBy": []
        },
        {
          "type": "time",
          "dataSource": "events",
          "sourceId": "${source_id}",
          "aggFn": "sum",
          "field": "gen_ai.usage.output_tokens",
          "where": "gen_ai.usage.output_tokens:*",
          "groupBy": []
        }
      ]
    },
    {
      "name": "Latency Percentiles Over Time",
      "x": 0, "y": 5, "w": 12, "h": 3,
      "series": [{
        "type": "time",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "quantile",
        "field": "duration",
        "where": "gen_ai.request.model:*",
        "groupBy": []
      }]
    },
    {
      "name": "Requests by Model",
      "x": 0, "y": 8, "w": 6, "h": 3,
      "series": [{
        "type": "table",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "gen_ai.request.model:*",
        "groupBy": ["gen_ai.request.model"]
      }]
    },
    {
      "name": "Requests by Service",
      "x": 6, "y": 8, "w": 6, "h": 3,
      "series": [{
        "type": "table",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "gen_ai.request.model:*",
        "groupBy": ["service"]
      }]
    },
    {
      "name": "Requests by Service Over Time",
      "x": 0, "y": 11, "w": 12, "h": 3,
      "series": [{
        "type": "time",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "gen_ai.request.model:*",
        "groupBy": ["service"]
      }]
    },
    {
      "name": "Avg Relevance Score",
      "x": 0, "y": 14, "w": 4, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "avg",
        "field": "eval.relevance_score",
        "where": "eval.relevance_score:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "Avg Coherence Score",
      "x": 4, "y": 14, "w": 4, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "avg",
        "field": "eval.coherence_score",
        "where": "eval.coherence_score:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "Total Evaluations",
      "x": 8, "y": 14, "w": 4, "h": 2,
      "series": [{
        "type": "number",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "eval.relevance_score:*",
        "groupBy": []
      }],
      "numberFormat": {
        "factor": 1,
        "output": "number",
        "mantissa": 2,
        "thousandSeparated": true,
        "average": false,
        "decimalBytes": false
      }
    },
    {
      "name": "Evaluation Scores Over Time",
      "x": 0, "y": 16, "w": 12, "h": 3,
      "series": [
        {
          "type": "time",
          "dataSource": "events",
          "sourceId": "${source_id}",
          "aggFn": "avg",
          "field": "eval.relevance_score",
          "where": "eval.relevance_score:*",
          "groupBy": []
        },
        {
          "type": "time",
          "dataSource": "events",
          "sourceId": "${source_id}",
          "aggFn": "avg",
          "field": "eval.coherence_score",
          "where": "eval.coherence_score:*",
          "groupBy": []
        }
      ]
    },
    {
      "name": "Recent LLM Calls",
      "x": 0, "y": 19, "w": 12, "h": 4,
      "series": [{
        "type": "search",
        "dataSource": "events",
        "sourceId": "${source_id}",
        "aggFn": "count",
        "where": "gen_ai.request.model:*",
        "groupBy": []
      }]
    }
  ]
}
EOF
}

# Execute action
case $ACTION in
    list)
        echo "Listing dashboards..."
        result=$(list_dashboards)
        echo "$result" | grep -q '"data"' && {
            echo "$result" | sed 's/.*"data":\[\(.*\)\].*/\1/' | tr '},{' '\n' | grep -E '"(id|name)"' | sed 's/.*"id":"\([^"]*\)".*/ID: \1/; s/.*"name":"\([^"]*\)".*/Name: \1/'
        } || echo "Error: $result"
        ;;
    create)
        echo "Creating LLM Observability Dashboard..."
        payload=$(get_dashboard_payload)
        result=$(create_dashboard "$payload")
        echo "$result" | grep -q '"id"' && {
            id=$(echo "$result" | sed 's/.*"id":"\([^"]*\)".*/\1/')
            echo "Dashboard created successfully!"
            echo "ID: $id"
            echo "View at: http://localhost:8080/dashboards/$id"
        } || echo "Error: $result"
        ;;
    delete)
        echo "Deleting dashboard: $DASHBOARD_ID"
        delete_dashboard "$DASHBOARD_ID"
        echo "Done"
        ;;
    recreate)
        echo "Finding existing LLM Observability Dashboard..."
        result=$(list_dashboards)
        existing_id=$(echo "$result" | grep -o '"id":"[^"]*","name":"LLM Observability Dashboard"' | head -1 | sed 's/"id":"\([^"]*\)".*/\1/')
        if [ -n "$existing_id" ]; then
            echo "Deleting existing dashboard: $existing_id"
            delete_dashboard "$existing_id"
        fi
        echo "Creating new LLM Observability Dashboard..."
        payload=$(get_dashboard_payload)
        result=$(create_dashboard "$payload")
        echo "$result" | grep -q '"id"' && {
            id=$(echo "$result" | sed 's/.*"id":"\([^"]*\)".*/\1/')
            echo "Dashboard created successfully!"
            echo "ID: $id"
            echo "View at: http://localhost:8080/dashboards/$id"
        } || echo "Error: $result"
        ;;
esac
