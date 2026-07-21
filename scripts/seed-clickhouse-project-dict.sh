#!/bin/bash
# ==============================================================================
# Seed ClickHouse Project-Name Dictionary
# ==============================================================================
# Creates the `langfuse_projects` ClickHouse dictionary, sourced live from the
# Langfuse Postgres `projects` table, so the langfuse-traces MCP (which is
# ClickHouse-only) can resolve project_id -> friendly name:
#
#     dictGet('default.langfuse_projects','name', project_id)
#
# Why: Langfuse's ClickHouse trace tables (traces/observations/scores) store only
# `project_id` (an opaque cuid). The human-readable project name lives in Postgres,
# which the ClickHouse MCP can't reach — so without this lookup, agents show raw IDs.
#
# Idempotent (CREATE DICTIONARY IF NOT EXISTS). Self-hosted only — cloud mode uses
# Langfuse Cloud's managed ClickHouse, which is not reachable for DDL.
#
# Usage: ./scripts/seed-clickhouse-project-dict.sh
# ==============================================================================
set -e

# Change to project root and source .env
cd "$(dirname "$0")/.."
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

DEPLOY_MODE="${DEPLOY_MODE:-self-hosted}"
if [ "$DEPLOY_MODE" = "cloud" ]; then
    echo -e "${YELLOW}Cloud mode: skipping project-name dictionary (managed ClickHouse not reachable).${NC}"
    exit 0
fi

CH_CONTAINER="langfuse-clickhouse"
# NOTE: use the Langfuse-INTERNAL ClickHouse creds (fixed in docker-compose.yaml's
# x-langfuse-common-env), NOT .env's CLICKHOUSE_* — those point at the public
# ClickHouse Playground (demo@sql-clickhouse.clickhouse.com) used by the
# clickhouse-playground MCP, and would fail auth against langfuse-clickhouse.
CH_USER="langfuse"
CH_PASS="langfuse123"

# Postgres source (Langfuse compose defaults — see x-langfuse-common-env DATABASE_URL)
PG_HOST="langfuse-postgres"
PG_PORT="5432"
PG_DB="langfuse"
PG_USER="langfuse"
PG_PASS="langfuse"

ch() { docker exec "$CH_CONTAINER" clickhouse-client -u "$CH_USER" --password "$CH_PASS" "$@"; }

# Wait for ClickHouse to accept queries
echo -n "Waiting for ClickHouse (${CH_CONTAINER})..."
for i in $(seq 1 30); do
    if ch -q "SELECT 1" >/dev/null 2>&1; then
        echo " ready"
        break
    fi
    sleep 2
    echo -n "."
    if [ "$i" -eq 30 ]; then
        echo ""
        echo "ClickHouse not reachable — is the langfuse profile up? (docker compose --profile langfuse up -d)"
        exit 1
    fi
done

# Create the dictionary (idempotent). COMPLEX_KEY_HASHED is required for a String key.
# LIFETIME keeps it in sync with Postgres (refresh every 5-10 min).
ch -q "
CREATE DICTIONARY IF NOT EXISTS default.langfuse_projects
(
    id String,
    name String
)
PRIMARY KEY id
SOURCE(POSTGRESQL(
    host '${PG_HOST}' port ${PG_PORT}
    user '${PG_USER}' password '${PG_PASS}'
    db '${PG_DB}' table 'projects'
))
LAYOUT(COMPLEX_KEY_HASHED())
LIFETIME(MIN 300 MAX 600);
"

# Verify (SELECT forces the dictionary to load from Postgres)
COUNT=$(ch -q "SELECT count() FROM default.langfuse_projects" 2>/dev/null || echo "0")
echo -e "${GREEN}✓${NC} langfuse_projects dictionary ready (${COUNT} project name(s) loaded)"
echo "  Use in the langfuse-traces MCP: dictGet('default.langfuse_projects','name', project_id)"
