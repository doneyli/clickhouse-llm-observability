#!/bin/bash
#
# HyperDX Dashboard Creator for LLM Observability (MongoDB Method)
#
# Creates dashboards using MongoDB direct insert, which supports all data sources
# including traces (required for LLM observability data).
#
# The External API v2 only supports logs/metrics, NOT traces.
# This script uses the internal 'config' format that the UI uses.
#
# Usage:
#   # List dashboards
#   ./scripts/create-hyperdx-dashboard-mongo.sh --list
#
#   # Create LLM Observability Dashboard
#   ./scripts/create-hyperdx-dashboard-mongo.sh --create
#
#   # Delete and recreate
#   ./scripts/create-hyperdx-dashboard-mongo.sh --recreate
#

set -e

# Configuration
CONTAINER_NAME="${CLICKSTACK_CONTAINER:-clickstack}"
DASHBOARD_NAME="LLM Observability Dashboard"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
ACTION=""
while [[ $# -gt 0 ]]; do
    case $1 in
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
        --help|-h)
            echo "Usage: $0 [--list|--create|--delete ID|--recreate]"
            echo ""
            echo "Options:"
            echo "  --list      List all dashboards"
            echo "  --create    Create LLM Observability Dashboard"
            echo "  --delete ID Delete a dashboard by ID"
            echo "  --recreate  Delete existing and create new dashboard"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$ACTION" ]; then
    echo "Usage: $0 [--list|--create|--delete ID|--recreate]"
    exit 1
fi

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${RED}Error: Container '${CONTAINER_NAME}' is not running${NC}"
    exit 1
fi

# MongoDB helper function
mongo_eval() {
    docker exec "$CONTAINER_NAME" mongo --quiet --eval "$1" 2>/dev/null
}

# Get team ID (first team in the database)
get_team_id() {
    mongo_eval 'db = db.getSiblingDB("hyperdx"); print(db.teams.findOne({})._id.str)'
}

# Get traces source ID
get_traces_source_id() {
    mongo_eval 'db = db.getSiblingDB("hyperdx"); var s = db.sources.findOne({kind: "trace"}); print(s ? s._id.str : "")'
}

# List dashboards
list_dashboards() {
    echo "Listing dashboards..."
    echo ""
    mongo_eval '
        db = db.getSiblingDB("hyperdx");
        db.dashboards.find({}, {name: 1, tags: 1, tiles: 1}).forEach(function(d) {
            print("ID: " + d._id.str);
            print("Name: " + d.name);
            print("Tags: " + (d.tags || []).join(", "));
            print("Tiles: " + (d.tiles || []).length);
            print("");
        });
    '
}

# Delete dashboard by ID
delete_dashboard() {
    local id="$1"
    echo "Deleting dashboard: $id"
    mongo_eval "
        db = db.getSiblingDB('hyperdx');
        var result = db.dashboards.deleteOne({_id: ObjectId('$id')});
        print('Deleted: ' + result.deletedCount);
    "
}

# Find dashboard by name
find_dashboard_by_name() {
    local name="$1"
    mongo_eval "
        db = db.getSiblingDB('hyperdx');
        var d = db.dashboards.findOne({name: '$name'});
        print(d ? d._id.str : '');
    "
}

# Create LLM Observability Dashboard
create_dashboard() {
    local team_id=$(get_team_id)
    local traces_source_id=$(get_traces_source_id)

    if [ -z "$team_id" ]; then
        echo -e "${RED}Error: Could not find team ID${NC}"
        exit 1
    fi

    if [ -z "$traces_source_id" ]; then
        echo -e "${RED}Error: Could not find traces source ID${NC}"
        exit 1
    fi

    echo "Team ID: $team_id"
    echo "Traces Source ID: $traces_source_id"
    echo ""
    echo "Creating $DASHBOARD_NAME..."

    # Create dashboard with LLM observability tiles
    # Uses the internal 'config' format with SQL where clauses
    local result=$(mongo_eval "
        db = db.getSiblingDB('hyperdx');

        var dashboard = {
            name: '$DASHBOARD_NAME',
            team: ObjectId('$team_id'),
            tags: ['llm', 'observability', 'gen-ai', 'auto-generated'],
            filters: [],
            tiles: [
                // Row 1: Summary metrics
                {
                    id: 'llm-total-requests',
                    x: 0, y: 0, w: 3, h: 2,
                    config: {
                        name: 'Total LLM Requests',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'count',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: ''
                        }],
                        where: \"SpanAttributes['gen_ai.request.model'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'number',
                        granularity: 'auto',
                        numberFormat: {
                            factor: 1,
                            output: 'number',
                            mantissa: 2,
                            thousandSeparated: true,
                            average: false,
                            decimalBytes: false
                        }
                    }
                },
                {
                    id: 'llm-input-tokens',
                    x: 3, y: 0, w: 3, h: 2,
                    config: {
                        name: 'Total Input Tokens',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'sum',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: \"SpanAttributes['gen_ai.usage.input_tokens']\"
                        }],
                        where: \"SpanAttributes['gen_ai.usage.input_tokens'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'number',
                        granularity: 'auto',
                        numberFormat: {
                            factor: 1,
                            output: 'number',
                            mantissa: 2,
                            thousandSeparated: true,
                            average: false,
                            decimalBytes: false
                        }
                    }
                },
                {
                    id: 'llm-output-tokens',
                    x: 6, y: 0, w: 3, h: 2,
                    config: {
                        name: 'Total Output Tokens',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'sum',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: \"SpanAttributes['gen_ai.usage.output_tokens']\"
                        }],
                        where: \"SpanAttributes['gen_ai.usage.output_tokens'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'number',
                        granularity: 'auto',
                        numberFormat: {
                            factor: 1,
                            output: 'number',
                            mantissa: 2,
                            thousandSeparated: true,
                            average: false,
                            decimalBytes: false
                        }
                    }
                },
                {
                    id: 'llm-avg-latency',
                    x: 9, y: 0, w: 3, h: 2,
                    config: {
                        name: 'Avg Latency (ms)',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'avg',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: 'Duration / 1000000'
                        }],
                        where: \"SpanAttributes['gen_ai.request.model'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'number',
                        granularity: 'auto',
                        numberFormat: {
                            factor: 1,
                            output: 'number',
                            mantissa: 2,
                            thousandSeparated: true,
                            average: false,
                            decimalBytes: false
                        }
                    }
                },
                // Row 2: Time series
                {
                    id: 'llm-requests-time',
                    x: 0, y: 2, w: 6, h: 3,
                    config: {
                        name: 'LLM Requests Over Time',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'count',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: ''
                        }],
                        where: \"SpanAttributes['gen_ai.request.model'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'line',
                        granularity: 'auto'
                    }
                },
                {
                    id: 'llm-tokens-time',
                    x: 6, y: 2, w: 6, h: 3,
                    config: {
                        name: 'Token Usage Over Time',
                        source: '$traces_source_id',
                        select: [
                            {
                                aggFn: 'sum',
                                aggCondition: '',
                                aggConditionLanguage: 'sql',
                                valueExpression: \"SpanAttributes['gen_ai.usage.input_tokens']\"
                            },
                            {
                                aggFn: 'sum',
                                aggCondition: '',
                                aggConditionLanguage: 'sql',
                                valueExpression: \"SpanAttributes['gen_ai.usage.output_tokens']\"
                            }
                        ],
                        where: \"SpanAttributes['gen_ai.usage.input_tokens'] != '' OR SpanAttributes['gen_ai.usage.output_tokens'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'stacked_bar',
                        granularity: 'auto'
                    }
                },
                // Row 3: Latency
                {
                    id: 'llm-latency-avg',
                    x: 0, y: 5, w: 6, h: 3,
                    config: {
                        name: 'Avg Latency Over Time (ms)',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'avg',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: 'Duration / 1000000'
                        }],
                        where: \"SpanAttributes['gen_ai.request.model'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'line',
                        granularity: 'auto'
                    }
                },
                {
                    id: 'llm-latency-max',
                    x: 6, y: 5, w: 6, h: 3,
                    config: {
                        name: 'Max Latency Over Time (ms)',
                        source: '$traces_source_id',
                        select: [{
                            aggFn: 'max',
                            aggCondition: '',
                            aggConditionLanguage: 'sql',
                            valueExpression: 'Duration / 1000000'
                        }],
                        where: \"SpanAttributes['gen_ai.request.model'] != ''\",
                        whereLanguage: 'sql',
                        displayType: 'line',
                        granularity: 'auto'
                    }
                }
            ],
            createdAt: new Date(),
            updatedAt: new Date()
        };

        var result = db.dashboards.insertOne(dashboard);
        print(result.insertedId.str);
    ")

    if [ -n "$result" ]; then
        echo -e "${GREEN}Dashboard created successfully!${NC}"
        echo ""
        echo "Dashboard ID: $result"
        echo "URL: http://localhost:8080/dashboards/$result"
    else
        echo -e "${RED}Failed to create dashboard${NC}"
        exit 1
    fi
}

# Execute action
case $ACTION in
    list)
        list_dashboards
        ;;
    create)
        existing_id=$(find_dashboard_by_name "$DASHBOARD_NAME")
        if [ -n "$existing_id" ]; then
            echo -e "${YELLOW}Dashboard already exists: $existing_id${NC}"
            echo "Use --recreate to delete and recreate it."
            exit 0
        fi
        create_dashboard
        ;;
    delete)
        delete_dashboard "$DASHBOARD_ID"
        ;;
    recreate)
        existing_id=$(find_dashboard_by_name "$DASHBOARD_NAME")
        if [ -n "$existing_id" ]; then
            echo "Deleting existing dashboard: $existing_id"
            delete_dashboard "$existing_id"
            echo ""
        fi
        create_dashboard
        ;;
esac
