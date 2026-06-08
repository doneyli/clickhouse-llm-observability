#!/bin/bash
# ==============================================================================
# Seed LibreChat Agents Script
# ==============================================================================
# Creates pre-configured LibreChat agents with MCP tool bindings.
#
# Agents created:
#   1. ClickHouse Data Analyst → clickhouse-playground tools
#   2. LLM Observability Analyst → langfuse-traces tools
#   3. Prompt Engineer → langfuse-prompts tools
#   4. LLM Ops Assistant → all 3 operational MCP servers combined
#   5. Agentic RAG Assistant → rag-retriever + clickhouse-playground tools
#
# Prerequisites: jq, running LibreChat instance
# Usage: ./scripts/seed-librechat-agents.sh
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

# Source .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Browser-like User-Agent (LibreChat's uaParser rejects curl's default)
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Demo user credentials (matches seed-demo-data convention)
DEMO_EMAIL="demo@example.com"
DEMO_PASSWORD="demodemo1!"
DEMO_NAME="Demo User"

LIBRECHAT_PORT=3080
LIBRECHAT_URL="http://localhost:${LIBRECHAT_PORT}"

echo ""
echo -e "${BLUE}=============================================="
echo "Seeding LibreChat Agents"
echo -e "==============================================${NC}"
echo ""

# ------------------------------------------------------------------------------
# Prereq Check
# ------------------------------------------------------------------------------
if ! command -v jq &> /dev/null; then
    echo -e "${RED}Error: jq is required but not installed.${NC}"
    echo "  Install with: brew install jq (macOS) or apt-get install jq (Linux)"
    exit 1
fi

# ------------------------------------------------------------------------------
# Health Check — Wait for LibreChat
# ------------------------------------------------------------------------------
echo -n "Waiting for LibreChat at ${LIBRECHAT_URL}..."
ATTEMPTS=0
MAX_ATTEMPTS=30
while ! curl -sf "${LIBRECHAT_URL}/api/health" > /dev/null 2>&1; do
    sleep 2
    ATTEMPTS=$((ATTEMPTS + 1))
    if [ $ATTEMPTS -ge $MAX_ATTEMPTS ]; then
        echo ""
        echo -e "${RED}LibreChat is not available at ${LIBRECHAT_URL}${NC}"
        echo "  Start it first with: ./setup.sh"
        exit 1
    fi
    echo -n "."
done
echo ""
echo -e "${GREEN}✓${NC} LibreChat is healthy"
echo ""

# ------------------------------------------------------------------------------
# Auth — Register (idempotent) + Login
# ------------------------------------------------------------------------------
echo "Authenticating..."

# Register demo user (ignore "already exists" errors)
curl -sf -X POST "${LIBRECHAT_URL}/api/auth/register" \
    -H "Content-Type: application/json" \
    -H "User-Agent: ${UA}" \
    -d "{\"name\":\"${DEMO_NAME}\",\"email\":\"${DEMO_EMAIL}\",\"password\":\"${DEMO_PASSWORD}\",\"confirm_password\":\"${DEMO_PASSWORD}\"}" \
    > /dev/null 2>&1 || true

# Login to get JWT token
LOGIN_RESPONSE=$(curl -sf -X POST "${LIBRECHAT_URL}/api/auth/login" \
    -H "Content-Type: application/json" \
    -H "User-Agent: ${UA}" \
    -d "{\"email\":\"${DEMO_EMAIL}\",\"password\":\"${DEMO_PASSWORD}\"}")

TOKEN=$(echo "$LOGIN_RESPONSE" | jq -r '.token')
if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo -e "${RED}Failed to authenticate with LibreChat${NC}"
    echo "  Response: ${LOGIN_RESPONSE}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Authenticated as ${DEMO_EMAIL}"
echo ""

# Helper: authenticated curl
auth_curl() {
    curl -sf \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "User-Agent: ${UA}" \
        "$@"
}

# ------------------------------------------------------------------------------
# Fetch Existing Agents (for idempotency)
# ------------------------------------------------------------------------------
echo "Checking existing agents..."

EXISTING_AGENTS=$(auth_curl "${LIBRECHAT_URL}/api/agents" 2>/dev/null || echo '[]')
# Handle both array and object-with-data response formats
if echo "$EXISTING_AGENTS" | jq -e '.data' > /dev/null 2>&1; then
    EXISTING_NAMES=$(echo "$EXISTING_AGENTS" | jq -r '.data[].name // empty')
else
    EXISTING_NAMES=$(echo "$EXISTING_AGENTS" | jq -r '.[].name // empty')
fi

agent_exists() {
    echo "$EXISTING_NAMES" | grep -qFx "$1"
}

# ------------------------------------------------------------------------------
# Fetch MCP Tools (with retries — MCP servers initialize async)
# ------------------------------------------------------------------------------
echo "Fetching MCP tools..."

MCP_TOOLS=""
MCP_ATTEMPTS=0
MCP_MAX_ATTEMPTS=15
while [ $MCP_ATTEMPTS -lt $MCP_MAX_ATTEMPTS ]; do
    MCP_TOOLS=$(auth_curl "${LIBRECHAT_URL}/api/mcp/tools" 2>/dev/null || echo '{}')

    # Check if we have tools from at least one server
    TOOL_COUNT=$(echo "$MCP_TOOLS" | jq 'if type == "object" then [.[] | keys[]] | length else 0 end' 2>/dev/null || echo "0")
    if [ "$TOOL_COUNT" -gt 0 ]; then
        break
    fi

    MCP_ATTEMPTS=$((MCP_ATTEMPTS + 1))
    echo -n "."
    sleep 2
done

if [ "$TOOL_COUNT" -eq 0 ]; then
    echo ""
    echo -e "${YELLOW}Warning: No MCP tools found. Agents will be created without tools.${NC}"
    echo "  MCP servers may still be initializing. Re-run this script later to update."
    echo ""
fi

echo -e "${GREEN}✓${NC} Found ${TOOL_COUNT} MCP tools"
echo ""

# Build tool arrays for each MCP server
# Tool format: server entry = "sys__server__sys_mcp_<serverName>"
#              individual tool = "<toolName>_mcp_<serverName>"
build_tools_for_server() {
    local server_name="$1"
    local tools=()

    # Add server entry
    tools+=("\"sys__server__sys_mcp_${server_name}\"")

    # Extract individual tool names for this server from pluginKey pattern
    local tool_names
    tool_names=$(echo "$MCP_TOOLS" | jq -r "
        if type == \"object\" then
            to_entries[] |
            select(.key == \"${server_name}\") |
            .value | keys[]
        else
            empty
        end
    " 2>/dev/null || true)

    while IFS= read -r tool; do
        [ -z "$tool" ] && continue
        tools+=("\"${tool}_mcp_${server_name}\"")
    done <<< "$tool_names"

    # Return as JSON array
    local IFS=','
    echo "[${tools[*]}]"
}

CLICKHOUSE_TOOLS=$(build_tools_for_server "clickhouse-playground")
LANGFUSE_TRACES_TOOLS=$(build_tools_for_server "langfuse-traces")
LANGFUSE_PROMPTS_TOOLS=$(build_tools_for_server "langfuse-prompts")
RAG_RETRIEVER_TOOLS=$(build_tools_for_server "rag-retriever")

# Agentic RAG agent uses both the KB retriever and the SQL playground
AGENTIC_RAG_TOOLS=$(jq -n \
    --argjson a "$RAG_RETRIEVER_TOOLS" \
    --argjson b "$CLICKHOUSE_TOOLS" \
    '$a + $b')

# Combine all tools for the Ops Assistant
ALL_TOOLS=$(jq -n \
    --argjson a "$CLICKHOUSE_TOOLS" \
    --argjson b "$LANGFUSE_TRACES_TOOLS" \
    --argjson c "$LANGFUSE_PROMPTS_TOOLS" \
    '$a + $b + $c')

# ------------------------------------------------------------------------------
# Create Agents
# ------------------------------------------------------------------------------
CREATED=0
SKIPPED=0

create_agent() {
    local name="$1"
    local description="$2"
    local instructions="$3"
    local tools="$4"

    if agent_exists "$name"; then
        echo -e "  ${YELLOW}⤳${NC} ${name} (already exists, skipping)"
        SKIPPED=$((SKIPPED + 1))
        return 0
    fi

    local payload
    payload=$(jq -n \
        --arg name "$name" \
        --arg desc "$description" \
        --arg inst "$instructions" \
        --argjson tools "$tools" \
        '{
            name: $name,
            description: $desc,
            instructions: $inst,
            provider: "anthropic",
            model: "claude-sonnet-4-20250514",
            tools: $tools
        }')

    local response
    response=$(auth_curl -X POST "${LIBRECHAT_URL}/api/agents" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1)

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} ${name}"
        CREATED=$((CREATED + 1))
    else
        echo -e "  ${RED}✗${NC} ${name} — failed to create"
        echo "    Response: ${response}"
    fi
}

echo "Creating agents..."
echo ""

# Agent 1: ClickHouse Data Analyst
create_agent \
    "ClickHouse Data Analyst" \
    "Explore and analyze data in ClickHouse using SQL" \
    "$(cat <<'INST'
You are a ClickHouse data analyst. You have access to the ClickHouse Playground — a public demo database with 35+ real-world datasets (UK property prices, GitHub events, Stack Overflow, NYC taxi rides, and more).

Your workflow:
1. Use the available MCP tools to discover databases and tables
2. Write and execute ClickHouse SQL queries to answer user questions
3. Present results clearly with summaries and insights

Tips:
- Start by listing available databases and tables when the user asks what data is available
- ClickHouse uses SQL with extensions — use formatReadableSize(), bar() functions for nice output
- For large result sets, use LIMIT and summarize patterns
- Prefer aggregations and summaries over raw row dumps
INST
)" \
    "$CLICKHOUSE_TOOLS"

# Agent 2: LLM Observability Analyst
create_agent \
    "LLM Observability Analyst" \
    "Analyze LLM traces and performance data from Langfuse" \
    "$(cat <<'INST'
You are an LLM observability analyst. You have access to Langfuse trace data stored in ClickHouse, allowing you to analyze LLM application performance, costs, and behavior patterns.

Your workflow:
1. Use MCP tools to query the Langfuse ClickHouse tables
2. Analyze traces, generations, scores, and token usage
3. Provide actionable insights about LLM application performance

Key tables to explore:
- traces: Top-level trace records with metadata, tags, and timing
- observations: Individual LLM calls (generations) with model, tokens, cost, duration
- scores: Evaluation scores (human or LLM-as-judge) linked to traces

Common analyses:
- Token usage and cost trends over time
- Latency percentiles (p50, p95, p99) by model or service
- Error rates and failure patterns
- Score distributions for quality evaluation
- Comparison across different models or prompt versions
INST
)" \
    "$LANGFUSE_TRACES_TOOLS"

# Agent 3: Prompt Engineer
create_agent \
    "Prompt Engineer" \
    "Manage and iterate on prompts stored in Langfuse" \
    "$(cat <<'INST'
You are a prompt engineer. You have access to the Langfuse prompt management system, which lets you version, manage, and iterate on prompts used by LLM applications.

Your workflow:
1. Use MCP tools to list, read, and manage prompts in Langfuse
2. Help users understand prompt structure and versioning
3. Suggest improvements based on prompt engineering best practices

Capabilities:
- List all prompts and their versions
- Read prompt content and configuration
- Help design new prompts with proper variable templating
- Suggest A/B testing strategies for prompt variants
- Advise on prompt structure (system/user message patterns, few-shot examples)
INST
)" \
    "$LANGFUSE_PROMPTS_TOOLS"

# Agent 4: LLM Ops Assistant
create_agent \
    "LLM Ops Assistant" \
    "Full-stack LLM operations — data, traces, and prompts" \
    "$(cat <<'INST'
You are an LLM operations assistant with access to the complete observability stack:

1. **ClickHouse Playground** — Public demo database with 35+ datasets for SQL analysis
2. **Langfuse Traces** — LLM trace data (latency, tokens, costs, scores) stored in ClickHouse
3. **Langfuse Prompts** — Prompt management system for versioning and iterating on prompts

You can help with:
- End-to-end analysis: trace a user request from prompt → LLM call → evaluation score
- Cost optimization: identify expensive patterns and suggest improvements
- Performance debugging: find slow traces, high-latency models, or error spikes
- Prompt management: review current prompts, suggest iterations, track version performance
- Data exploration: query any ClickHouse dataset for demos or analysis

Start by understanding what the user needs, then use the appropriate tools. You can combine data from multiple sources for comprehensive analysis.
INST
)" \
    "$ALL_TOOLS"

# Agent 5: Agentic RAG Assistant
create_agent \
    "Agentic RAG Assistant" \
    "Agentic RAG over a ClickHouse-native vector store with self-correction" \
    "$(cat <<'INST'
You are an Agentic RAG assistant. You answer questions about ClickHouse, RAG, vector search, OpenTelemetry, and LLM observability by retrieving from a knowledge base stored in ClickHouse (native vector_similarity search) — and you can also query live ClickHouse demo datasets when a question needs real numbers.

Follow a corrective-RAG (CRAG) loop, do NOT answer from memory:
1. ROUTE: decide if the question needs the knowledge base (concepts/how-to) or live SQL (dataset numbers).
2. RETRIEVE: call `retrieve_kb` with a focused query to get relevant chunks.
3. GRADE: judge whether the retrieved chunks actually answer the question.
4. SELF-CORRECT: if the chunks are weak or off-topic, rewrite the query (expand abbreviations, add synonyms, be specific) and call `retrieve_kb` again before answering. Use `list_documents` if you need to see what's available.
5. TOOL USE: for questions about dataset numbers (taxi rides, github stars, stackoverflow, etc.), write and run a ClickHouse SELECT via the playground tools instead.
6. ANSWER: respond using ONLY the retrieved context. Cite the document titles you used.
7. REFLECT: before finishing, verify every claim is supported by the context; if not, retrieve more or state what's missing.

Be concise, accurate, and grounded. Never fabricate sources.
INST
)" \
    "$AGENTIC_RAG_TOOLS"

echo ""

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo -e "${GREEN}=============================================="
echo "LibreChat Agent Seeding Complete!"
echo -e "==============================================${NC}"
echo ""
echo "  Created: ${CREATED} agent(s)"
echo "  Skipped: ${SKIPPED} agent(s) (already existed)"
echo ""
echo -e "  Open LibreChat: ${GREEN}${LIBRECHAT_URL}${NC}"
echo "  Select an agent from the dropdown to start chatting."
echo ""
