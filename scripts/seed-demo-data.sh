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
# 4. Runs Langfuse evaluator → scores the traces
#
# Usage: ./scripts/seed-demo-data.sh [--quick]
#   --quick: Only run text-to-sql and vector-rag demos (skip test scenarios)
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
echo -e "${BLUE}=============================================="
echo "Seeding Demo Data"
echo -e "==============================================${NC}"
echo ""

# ------------------------------------------------------------------------------
# Check Services
# ------------------------------------------------------------------------------
echo "Checking required services..."

# Check if text-to-sql is available
if ! docker compose ps text-to-sql 2>/dev/null | grep -q "Up"; then
    echo -e "${YELLOW}Starting text-to-sql service...${NC}"
    docker compose up -d text-to-sql
    sleep 5
fi

# Check if vector-rag is available
if ! docker compose ps vector-rag 2>/dev/null | grep -q "Up"; then
    echo -e "${YELLOW}Starting vector-rag service...${NC}"
    docker compose up -d vector-rag
    sleep 5
fi

echo -e "${GREEN}✓${NC} Required services are running"
echo ""

# ------------------------------------------------------------------------------
# Run Text-to-SQL Demo
# ------------------------------------------------------------------------------
echo -e "${BLUE}[1/4] Running Text-to-SQL demo queries...${NC}"
echo ""

docker compose run --rm text-to-sql python main.py 2>&1 | while read line; do
    echo "  $line"
done

echo ""
echo -e "${GREEN}✓${NC} Text-to-SQL traces generated"
echo ""

# ------------------------------------------------------------------------------
# Run Vector RAG Demo
# ------------------------------------------------------------------------------
echo -e "${BLUE}[2/4] Running Vector RAG demo queries...${NC}"
echo ""

docker compose run --rm vector-rag python main.py 2>&1 | while read line; do
    echo "  $line"
done

echo ""
echo -e "${GREEN}✓${NC} Vector RAG traces generated"
echo ""

# ------------------------------------------------------------------------------
# Run Test Scenarios (unless --quick)
# ------------------------------------------------------------------------------
if [[ "$1" != "--quick" ]]; then
    echo -e "${BLUE}[3/4] Running test scenarios (good/bad examples)...${NC}"
    echo ""

    # Check if test-scenarios service exists and can run
    if docker compose --profile tools run --rm test-scenarios --help > /dev/null 2>&1; then
        docker compose --profile tools run --rm test-scenarios 2>&1 | while read line; do
            echo "  $line"
        done
        echo ""
        echo -e "${GREEN}✓${NC} Test scenarios completed"
    else
        echo -e "${YELLOW}⚠${NC} Test scenarios service not available, skipping"
    fi
    echo ""
else
    echo -e "${YELLOW}[3/4] Skipping test scenarios (--quick mode)${NC}"
    echo ""
fi

# ------------------------------------------------------------------------------
# Evaluation Note
# ------------------------------------------------------------------------------
echo -e "${BLUE}[4/4] Evaluation happens automatically via Langfuse native evaluators${NC}"
echo ""
echo "  Traces created. Native Langfuse evaluators will score them automatically."
echo "  Configure evaluators at: ${GREEN}http://localhost:3001${NC} → Evaluations → LLM-as-a-Judge"
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
echo "  Langfuse Traces:     ${GREEN}http://localhost:3001${NC}"
echo "  HyperDX Traces:      ${GREEN}http://localhost:8080${NC}"
echo ""
echo "Try these in the UIs:"
echo "  - View trace timelines and token usage"
echo "  - Filter by service (text-to-sql, vector-rag)"
echo "  - Configure LLM-as-a-Judge evaluators in Langfuse"
echo ""
echo "To set up automatic evaluation:"
echo "  1. Go to Langfuse → Evaluations → LLM-as-a-Judge"
echo "  2. Create evaluators (Hallucination, Helpfulness, etc.)"
echo "  3. Filter by tag 'test-scenario' for test data"
echo ""
