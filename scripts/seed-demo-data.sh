#!/bin/bash
# ==============================================================================
# Demo Data Seeding Script
# ==============================================================================
# Populates the demo environment with sample traces and evaluations.
#
# This script:
# 1. Runs Text-to-SQL demo queries → generates Langfuse traces
# 2. Runs Vector RAG demo queries → generates Langfuse traces
# 3. Runs test scenarios (good/bad examples) → generates evaluation test data
# 4. Notes that Langfuse evaluators score the traces automatically
#
# Usage: ./scripts/seed-demo-data.sh [--quick] [--datasets]
#   --quick:    Only run text-to-sql and vector-rag demos (skip test scenarios)
#   --datasets: Also seed evaluation datasets (coding quality + security)
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

# Source .env to ensure Docker Compose uses project-level values
# (shell-level env vars override .env, so we must export explicitly)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

echo ""
echo -e "${BLUE}=============================================="
echo "Seeding Demo Data"
echo -e "==============================================${NC}"
echo ""

# ------------------------------------------------------------------------------
# Check Services
# ------------------------------------------------------------------------------
echo "Checking required services..."

# Check if Langfuse is healthy (needed for trace ingestion)
echo -n "  Checking Langfuse..."
DEPLOY_MODE="${DEPLOY_MODE:-self-hosted}"
LANGFUSE_PORT=${LANGFUSE_PORT:-3001}

if [ "$DEPLOY_MODE" = "cloud" ]; then
    # Cloud mode: check the remote Langfuse instance
    LANGFUSE_HEALTH_URL="${LANGFUSE_BASE_URL:-https://cloud.langfuse.com}/api/public/health"
    if curl -sf "$LANGFUSE_HEALTH_URL" > /dev/null 2>&1; then
        echo ""
        echo -e "${GREEN}✓${NC} Langfuse Cloud is reachable at ${LANGFUSE_BASE_URL}"
    else
        echo ""
        echo -e "${YELLOW}!${NC} Could not reach Langfuse Cloud at ${LANGFUSE_HEALTH_URL}"
        echo "  Traces may not be ingested. Continuing anyway..."
    fi
else
    # Self-hosted mode: wait for local Langfuse
    ATTEMPTS=0
    MAX_ATTEMPTS=30
    while ! curl -s "http://localhost:${LANGFUSE_PORT}" > /dev/null 2>&1; do
        sleep 2
        ATTEMPTS=$((ATTEMPTS + 1))
        if [ $ATTEMPTS -ge $MAX_ATTEMPTS ]; then
            echo ""
            echo -e "${RED}Langfuse is not available at http://localhost:${LANGFUSE_PORT}${NC}"
            echo "Start it first with: ./setup.sh"
            exit 1
        fi
        echo -n "."
    done
    echo ""
    echo -e "${GREEN}✓${NC} Langfuse is healthy"
fi

echo -e "${GREEN}✓${NC} Required services are running"
echo ""

# ------------------------------------------------------------------------------
# Build Demo Images
# ------------------------------------------------------------------------------
echo "Building demo images (using cache if available)..."
docker compose --profile demo --profile tools build --quiet 2>&1 || true
echo -e "${GREEN}✓${NC} Demo images ready"
echo ""

# ------------------------------------------------------------------------------
# Run Text-to-SQL Demo
# ------------------------------------------------------------------------------
echo -e "${BLUE}[1/4] Running Text-to-SQL demo queries...${NC}"
echo ""

if ! docker compose run --rm text-to-sql python main.py 2>&1 | while read line; do
    echo "  $line"
done; then
    echo -e "${RED}✗${NC} Text-to-SQL demo failed. Check your ANTHROPIC_API_KEY in .env"
    echo "  Run manually: docker compose run --rm text-to-sql python main.py"
    echo ""
else
    echo ""
    echo -e "${GREEN}✓${NC} Text-to-SQL traces generated"
    echo ""
fi

# ------------------------------------------------------------------------------
# Run Vector RAG Demo
# ------------------------------------------------------------------------------
echo -e "${BLUE}[2/4] Running Vector RAG demo queries...${NC}"
echo ""

if ! docker compose run --rm vector-rag python main.py 2>&1 | while read line; do
    echo "  $line"
done; then
    echo -e "${RED}✗${NC} Vector RAG demo failed. Check your ANTHROPIC_API_KEY in .env"
    echo "  Run manually: docker compose run --rm vector-rag python main.py"
    echo ""
else
    echo ""
    echo -e "${GREEN}✓${NC} Vector RAG traces generated"
    echo ""
fi

# ------------------------------------------------------------------------------
# Run Test Scenarios (unless --quick)
# ------------------------------------------------------------------------------
if [[ "$1" != "--quick" ]]; then
    echo -e "${BLUE}[3/4] Running test scenarios (good/bad examples)...${NC}"
    echo ""

    # Check if test-scenarios service exists and can run
    if docker compose --profile tools run --rm test-scenarios --help > /dev/null 2>&1; then
        if ! docker compose --profile tools run --rm test-scenarios 2>&1 | while read line; do
            echo "  $line"
        done; then
            echo -e "${RED}✗${NC} Test scenarios failed."
            echo "  Run manually: docker compose --profile tools run --rm test-scenarios"
            echo ""
        else
            echo ""
            echo -e "${GREEN}✓${NC} Test scenarios completed"
        fi
    else
        echo -e "${YELLOW}⚠${NC} Test scenarios service not available, skipping"
    fi
    echo ""
else
    echo -e "${YELLOW}[3/4] Skipping test scenarios (--quick mode)${NC}"
    echo ""
fi

# ------------------------------------------------------------------------------
# Seed Datasets (if --datasets flag)
# ------------------------------------------------------------------------------
if [[ "$*" == *"--datasets"* ]]; then
    echo -e "${BLUE}[4/5] Seeding evaluation datasets...${NC}"
    echo ""

    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if command -v python3 &> /dev/null && python3 -c "import langfuse" 2>/dev/null; then
        if python3 "${SCRIPT_DIR}/seed-datasets.py" 2>&1 | while read line; do
            echo "  $line"
        done; then
            echo ""
            echo -e "${GREEN}✓${NC} Evaluation datasets created"
            # Code evaluators that target experiments reference dataset IDs,
            # so (re-)provision them now that the datasets exist.
            "${SCRIPT_DIR}/seed-code-evaluators.sh" || true
        else
            echo -e "${RED}✗${NC} Dataset seeding failed"
            echo "  Run manually: python scripts/seed-datasets.py"
        fi
    else
        echo -e "${YELLOW}⚠${NC} Skipping datasets: python3 or langfuse package not available"
        echo "  Install with: pip install 'langfuse>=3.0,<4.0'"
        echo "  Then run: python scripts/seed-datasets.py"
    fi
    echo ""
else
    echo -e "${YELLOW}[4/5] Skipping evaluation datasets (use --datasets to include)${NC}"
    echo ""
fi

# ------------------------------------------------------------------------------
# Evaluation Note
# ------------------------------------------------------------------------------
echo -e "${BLUE}[5/5] Evaluation happens automatically via Langfuse native evaluators${NC}"
echo ""
echo "  Traces created. Native Langfuse evaluators will score them automatically."
echo -e "  Configure evaluators at: ${GREEN}http://localhost:3001${NC} → Evaluations → LLM-as-a-Judge"
echo ""

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo -e "${GREEN}=============================================="
echo "Demo Data Seeding Complete!"
echo -e "==============================================${NC}"
echo ""
echo "Your demo now has sample data. View it at:"
echo ""
echo -e "  Langfuse Traces:     ${GREEN}http://localhost:3001${NC}"
echo ""
echo "Try these in the UI:"
echo "  - View trace timelines and token usage"
echo "  - Filter by service (text-to-sql, vector-rag)"
echo "  - Configure LLM-as-a-Judge evaluators in Langfuse"
echo "  - Browse evaluation datasets (if --datasets was used)"
echo ""
echo "To set up automatic evaluation:"
echo "  1. Go to Langfuse → Evaluations → LLM-as-a-Judge"
echo "  2. Create evaluators (Hallucination, Helpfulness, etc.)"
echo "  3. Filter by tag 'test-scenario' for test data"
echo ""
echo "Additional scripts:"
echo "  python scripts/seed-datasets.py              # Seed evaluation datasets"
echo "  python scripts/import-external-traces.py     # Import traces from another Langfuse"
echo "  See docs/LANGFUSE_DEMO_RUNBOOK.md for a full demo walkthrough"
echo ""
