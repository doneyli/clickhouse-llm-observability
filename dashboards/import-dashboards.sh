#!/bin/bash
#
# Import HyperDX Dashboards
#
# Imports dashboard definitions from JSON files into HyperDX via MongoDB.
# The JSON files use {{TRACES_SOURCE_ID}} as a placeholder which is replaced
# with the actual traces source ID at import time.
#
# Usage:
#   ./dashboards/import-dashboards.sh [--all | --dashboard NAME]
#
# Examples:
#   ./dashboards/import-dashboards.sh --all
#   ./dashboards/import-dashboards.sh --dashboard cost-dashboard.json
#

set -e

# Configuration
CONTAINER_NAME="${CLICKSTACK_CONTAINER:-clickstack}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${RED}Error: Container '${CONTAINER_NAME}' is not running${NC}"
    echo "Start the container with: docker compose up -d"
    exit 1
fi

# Get team ID
get_team_id() {
    docker exec "$CONTAINER_NAME" mongo --quiet --eval '
        db = db.getSiblingDB("hyperdx");
        var team = db.teams.findOne({});
        print(team ? team._id.str : "");
    ' 2>/dev/null
}

# Get traces source ID
get_traces_source_id() {
    docker exec "$CONTAINER_NAME" mongo --quiet --eval '
        db = db.getSiblingDB("hyperdx");
        var source = db.sources.findOne({kind: "trace"});
        print(source ? source._id.str : "");
    ' 2>/dev/null
}

# Check if dashboard exists
dashboard_exists() {
    local name="$1"
    local result=$(docker exec "$CONTAINER_NAME" mongo --quiet --eval "
        db = db.getSiblingDB('hyperdx');
        var d = db.dashboards.findOne({name: '$name'});
        print(d ? 'exists' : 'not_found');
    " 2>/dev/null)
    [ "$result" = "exists" ]
}

# Import a single dashboard
import_dashboard() {
    local json_file="$1"
    local team_id="$2"
    local traces_source_id="$3"

    if [ ! -f "$json_file" ]; then
        echo -e "${RED}Error: File not found: $json_file${NC}"
        return 1
    fi

    # Read and process the JSON file
    local dashboard_json=$(cat "$json_file" | sed "s/{{TRACES_SOURCE_ID}}/$traces_source_id/g")
    local dashboard_name=$(echo "$dashboard_json" | grep -o '"name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')

    # Check if dashboard already exists
    if dashboard_exists "$dashboard_name"; then
        echo -e "${YELLOW}Dashboard '$dashboard_name' already exists, skipping...${NC}"
        return 0
    fi

    echo "Importing: $dashboard_name"

    # Create a temporary file for the MongoDB script
    local temp_script=$(mktemp)
    cat > "$temp_script" << MONGO_EOF
db = db.getSiblingDB('hyperdx');
var dashboard = $dashboard_json;
dashboard.team = ObjectId('$team_id');
dashboard.createdAt = new Date();
dashboard.updatedAt = new Date();
var result = db.dashboards.insertOne(dashboard);
print(result.insertedId ? result.insertedId.str : 'FAILED');
MONGO_EOF

    # Copy script to container and execute
    docker cp "$temp_script" "$CONTAINER_NAME:/tmp/import_dashboard.js"
    local result=$(docker exec "$CONTAINER_NAME" mongo --quiet /tmp/import_dashboard.js 2>/dev/null)
    rm "$temp_script"

    if [ -n "$result" ] && [ "$result" != "FAILED" ]; then
        echo -e "${GREEN}  Created: $dashboard_name (ID: $result)${NC}"
        echo "  URL: http://localhost:8080/dashboards/$result"
    else
        echo -e "${RED}  Failed to import: $dashboard_name${NC}"
        return 1
    fi
}

# Main
main() {
    local import_all=false
    local specific_dashboard=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --all)
                import_all=true
                shift
                ;;
            --dashboard)
                specific_dashboard="$2"
                shift 2
                ;;
            --help|-h)
                echo "Usage: $0 [--all | --dashboard NAME]"
                echo ""
                echo "Options:"
                echo "  --all              Import all dashboard JSON files"
                echo "  --dashboard NAME   Import a specific dashboard file"
                echo ""
                echo "Available dashboards:"
                for f in "$SCRIPT_DIR"/*.json; do
                    [ -f "$f" ] && echo "  - $(basename "$f")"
                done
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    if [ "$import_all" = false ] && [ -z "$specific_dashboard" ]; then
        echo "Usage: $0 [--all | --dashboard NAME]"
        echo "Use --help for more information"
        exit 1
    fi

    # Get IDs
    echo "Getting HyperDX configuration..."
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

    # Import dashboards
    if [ "$import_all" = true ]; then
        echo "Importing all dashboards..."
        echo ""
        for json_file in "$SCRIPT_DIR"/*.json; do
            [ -f "$json_file" ] && import_dashboard "$json_file" "$team_id" "$traces_source_id"
        done
    else
        local json_file="$SCRIPT_DIR/$specific_dashboard"
        import_dashboard "$json_file" "$team_id" "$traces_source_id"
    fi

    echo ""
    echo -e "${GREEN}Done!${NC}"
}

main "$@"
