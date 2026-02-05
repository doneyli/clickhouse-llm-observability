#!/bin/bash
# ==============================================================================
# Langfuse Integration Validation Script
# ==============================================================================
# Validates that Langfuse is properly configured and working with the demo.
#
# Usage: ./scripts/validate-langfuse.sh
# ==============================================================================

set -e

echo "=============================================="
echo "Langfuse Integration Validation"
echo "=============================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0
WARNINGS=0

pass() {
    echo -e "${GREEN}✓${NC} $1"
    PASSED=$((PASSED + 1))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    FAILED=$((FAILED + 1))
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

# ------------------------------------------------------------------------------
# 1. Check Environment Variables
# ------------------------------------------------------------------------------
echo "1. Checking environment variables..."

if [ -n "$LANGFUSE_PUBLIC_KEY" ]; then
    pass "LANGFUSE_PUBLIC_KEY is set"
else
    warn "LANGFUSE_PUBLIC_KEY is not set (optional for base demo)"
fi

if [ -n "$LANGFUSE_SECRET_KEY" ]; then
    pass "LANGFUSE_SECRET_KEY is set"
else
    warn "LANGFUSE_SECRET_KEY is not set (optional for base demo)"
fi

echo ""

# ------------------------------------------------------------------------------
# 2. Check Langfuse Services
# ------------------------------------------------------------------------------
echo "2. Checking Langfuse services..."

# Check if Langfuse web is running
if curl -s http://localhost:3001 > /dev/null 2>&1; then
    pass "Langfuse web UI is accessible at http://localhost:3001"
else
    warn "Langfuse web UI is not accessible (start with: docker compose --profile langfuse up -d)"
fi

# Check PostgreSQL
if docker ps --format '{{.Names}}' | grep -q langfuse-postgres; then
    pass "langfuse-postgres container is running"
else
    warn "langfuse-postgres container is not running"
fi

# Check Redis
if docker ps --format '{{.Names}}' | grep -q langfuse-redis; then
    pass "langfuse-redis container is running"
else
    warn "langfuse-redis container is not running"
fi

# Check MinIO
if docker ps --format '{{.Names}}' | grep -q langfuse-minio; then
    pass "langfuse-minio container is running"
else
    warn "langfuse-minio container is not running"
fi

# Check Langfuse worker
if docker ps --format '{{.Names}}' | grep -q langfuse-worker; then
    pass "langfuse-worker container is running"
else
    warn "langfuse-worker container is not running"
fi

# Check Langfuse web
if docker ps --format '{{.Names}}' | grep -q langfuse-web; then
    pass "langfuse-web container is running"
else
    warn "langfuse-web container is not running"
fi

echo ""

# ------------------------------------------------------------------------------
# 3. Check ClickHouse Database for Langfuse
# ------------------------------------------------------------------------------
echo "3. Checking ClickHouse Langfuse database..."

# Check if ClickStack is running
if docker ps --format '{{.Names}}' | grep -q clickstack; then
    pass "ClickStack container is running"

    # Check if langfuse database exists
    if docker exec clickstack clickhouse-client --user api --password api --query "SHOW DATABASES" 2>/dev/null | grep -q langfuse; then
        pass "Langfuse database exists in ClickHouse"
    else
        warn "Langfuse database does not exist in ClickHouse (create with: docker exec clickstack clickhouse-client --user api --password api --query 'CREATE DATABASE IF NOT EXISTS langfuse')"
    fi
else
    fail "ClickStack container is not running"
fi

echo ""

# ------------------------------------------------------------------------------
# 4. Check Demo Apps Configuration
# ------------------------------------------------------------------------------
echo "4. Checking demo apps for Langfuse support..."

# Check if text-to-sql has langfuse_config.py
if [ -f "text-to-sql/langfuse_config.py" ]; then
    pass "text-to-sql has langfuse_config.py"
else
    fail "text-to-sql is missing langfuse_config.py"
fi

# Check if vector-rag has langfuse_config.py
if [ -f "vector-rag/langfuse_config.py" ]; then
    pass "vector-rag has langfuse_config.py"
else
    fail "vector-rag is missing langfuse_config.py"
fi

# Check if langfuse is in requirements
if grep -q "langfuse" text-to-sql/requirements.txt 2>/dev/null; then
    pass "text-to-sql requirements.txt includes langfuse"
else
    fail "text-to-sql requirements.txt is missing langfuse"
fi

if grep -q "langfuse" vector-rag/requirements.txt 2>/dev/null; then
    pass "vector-rag requirements.txt includes langfuse"
else
    fail "vector-rag requirements.txt is missing langfuse"
fi

echo ""

# ------------------------------------------------------------------------------
# 5. Check Test Scenarios (for evaluation)
# ------------------------------------------------------------------------------
echo "5. Checking test-scenarios service..."

if [ -f "test-scenarios/export_test_scenarios.py" ]; then
    pass "test-scenarios/export_test_scenarios.py exists"
else
    fail "test-scenarios/export_test_scenarios.py is missing"
fi

if grep -q "tags" test-scenarios/export_test_scenarios.py 2>/dev/null; then
    pass "test scenarios include tags for evaluator filtering"
else
    warn "Test scenarios may not include tags for evaluator filtering"
fi

echo ""
echo "Note: Langfuse native LLM-as-a-Judge evaluators replace the custom evaluator."
echo "      Configure evaluators in Langfuse UI: Evaluations → LLM-as-a-Judge"

echo ""

# ------------------------------------------------------------------------------
# 6. Check docker-compose.yaml
# ------------------------------------------------------------------------------
echo "6. Checking docker-compose.yaml for Langfuse services..."

if grep -q "langfuse-web:" docker-compose.yaml 2>/dev/null; then
    pass "docker-compose.yaml has langfuse-web service"
else
    fail "docker-compose.yaml is missing langfuse-web service"
fi

if grep -q "langfuse-worker:" docker-compose.yaml 2>/dev/null; then
    pass "docker-compose.yaml has langfuse-worker service"
else
    fail "docker-compose.yaml is missing langfuse-worker service"
fi

if grep -q "profile.*langfuse" docker-compose.yaml 2>/dev/null; then
    pass "Langfuse services use 'langfuse' profile"
else
    fail "Langfuse services are not under 'langfuse' profile"
fi

echo ""

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo "=============================================="
echo "Validation Summary"
echo "=============================================="
echo -e "Passed:   ${GREEN}${PASSED}${NC}"
echo -e "Failed:   ${RED}${FAILED}${NC}"
echo -e "Warnings: ${YELLOW}${WARNINGS}${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}Langfuse integration is properly configured!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Start Langfuse: docker compose --profile langfuse up -d"
    echo "  2. Get API keys from: http://localhost:3001"
    echo "  3. Add to .env: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY"
    echo "  4. Restart demo apps: docker compose restart text-to-sql vector-rag"
    exit 0
else
    echo -e "${RED}Some validation checks failed. Please fix the issues above.${NC}"
    exit 1
fi
