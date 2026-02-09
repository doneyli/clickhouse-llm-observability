#!/bin/bash
# ==============================================================================
# Demo Reset Script
# ==============================================================================
# Completely resets the demo environment to a fresh state.
#
# WARNING: This is a destructive operation that will:
# - Stop all containers
# - Remove all Docker volumes (databases, caches)
# - Remove bind-mounted data directories
# - Clear generated credentials from .env
#
# Usage: ./scripts/reset.sh [--force]
#   --force: Skip confirmation prompts
# ==============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Change to project root
cd "$(dirname "$0")/.."

echo ""
echo -e "${RED}=============================================="
echo "Demo Reset - DESTRUCTIVE OPERATION"
echo -e "==============================================${NC}"
echo ""
echo "This will permanently delete:"
echo ""
echo "  - All Docker volumes (Langfuse, MongoDB, Meilisearch)"
echo "  - Bind-mounted data directories (./data-node, ./meili_data, etc.)"
echo "  - Langfuse API keys from .env"
echo "  - LibreChat secrets from .env (will be regenerated)"
echo "  - All LibreChat conversations and user accounts"
echo "  - All traces and evaluations"
echo ""

# ------------------------------------------------------------------------------
# Confirmation
# ------------------------------------------------------------------------------
if [[ "$1" != "--force" ]]; then
    echo -e "${YELLOW}Are you sure you want to reset everything?${NC}"
    echo ""
    read -p "Type 'yes' to confirm: " CONFIRM

    if [ "$CONFIRM" != "yes" ]; then
        echo ""
        echo "Reset cancelled."
        exit 0
    fi
fi

echo ""
echo -e "${BLUE}Starting reset...${NC}"
echo ""

# ------------------------------------------------------------------------------
# Stop All Services
# ------------------------------------------------------------------------------
echo "[1/5] Stopping all services..."
docker compose --profile langfuse --profile demo --profile tools down 2>/dev/null || true
echo -e "${GREEN}✓${NC} Services stopped"
echo ""

# ------------------------------------------------------------------------------
# Remove Docker Volumes
# ------------------------------------------------------------------------------
echo "[2/5] Removing Docker volumes..."

# Get project name (usually directory name)
PROJECT_NAME=$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')

# List of named volumes used by the demo
VOLUME_SUFFIXES=(
    # Langfuse volumes
    "langfuse-postgres-data"
    "langfuse-minio-data"
    "langfuse-clickhouse-data"
)

for suffix in "${VOLUME_SUFFIXES[@]}"; do
    vol="${PROJECT_NAME}_${suffix}"
    if docker volume ls -q | grep -q "^${vol}$"; then
        docker volume rm "$vol" 2>/dev/null || true
        echo "  Removed volume: $vol"
    fi
done

# Also try with clickhouse-llm-observability prefix (in case project name differs)
for suffix in "${VOLUME_SUFFIXES[@]}"; do
    vol="clickhouse-llm-observability_${suffix}"
    if docker volume ls -q | grep -q "^${vol}$"; then
        docker volume rm "$vol" 2>/dev/null || true
        echo "  Removed volume: $vol"
    fi
done

echo -e "${GREEN}✓${NC} Docker volumes removed"
echo ""

# ------------------------------------------------------------------------------
# Remove Bind-Mounted Data
# ------------------------------------------------------------------------------
echo "[3/5] Removing bind-mounted data directories..."

DIRS_TO_REMOVE=(
    "./data-node"
    "./meili_data"
    "./logs"
    "./mcp-logs"
    "./uploads"
    "./images"
)

for dir in "${DIRS_TO_REMOVE[@]}"; do
    if [ -d "$dir" ]; then
        rm -rf "$dir"
        echo "  Removed: $dir"
    fi
done

echo -e "${GREEN}✓${NC} Data directories removed"
echo ""

# ------------------------------------------------------------------------------
# Clean .env of Generated Credentials
# ------------------------------------------------------------------------------
echo "[4/5] Cleaning generated credentials from .env..."

if [ -f ".env" ]; then
    # Create backup
    cp .env .env.backup

    # Remove all generated credentials
    grep -v "^LANGFUSE_PUBLIC_KEY=" .env | \
    grep -v "^LANGFUSE_SECRET_KEY=" | \
    grep -v "^LANGFUSE_MCP_AUTH_TOKEN=" | \
    grep -v "^# Langfuse API Keys (added by setup script)" | \
    grep -v "^CREDS_KEY=" | \
    grep -v "^CREDS_IV=" | \
    grep -v "^JWT_SECRET=" | \
    grep -v "^JWT_REFRESH_SECRET=" > .env.tmp

    mv .env.tmp .env

    echo "  Backup saved to: .env.backup"
    echo -e "${GREEN}✓${NC} Generated credentials removed from .env"
else
    echo "  No .env file found"
fi
echo ""

# ------------------------------------------------------------------------------
# Prune Docker Resources
# ------------------------------------------------------------------------------
echo "[5/5] Pruning unused Docker resources..."
docker system prune -f --volumes 2>/dev/null || true
echo -e "${GREEN}✓${NC} Docker resources pruned"
echo ""

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo -e "${GREEN}=============================================="
echo "Reset Complete!"
echo -e "==============================================${NC}"
echo ""
echo "The demo environment has been reset to a fresh state."
echo ""
echo "To start fresh:"
echo "  1. Run ${GREEN}./setup.sh${NC}"
echo "  2. Run ${GREEN}./scripts/seed-demo-data.sh${NC} to populate demo data"
echo ""
