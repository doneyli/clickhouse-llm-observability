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
while ! curl -sf "${LIBRECHAT_URL}/health" > /dev/null 2>&1 && \
      ! curl -sf "${LIBRECHAT_URL}/api/health" > /dev/null 2>&1; do
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

agent_field() {
    # agent_field <name> <field> — read a field from an existing agent
    echo "$EXISTING_AGENTS" | jq -r --arg n "$1" --arg f "$2" \
        'if type == "object" then .data else . end | .[] | select(.name == $n) | .[$f] // empty'
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

    # Count actual TOOLS across all servers (not just how many servers exist —
    # a server can be registered before it has advertised any tools yet).
    TOOL_COUNT=$(echo "$MCP_TOOLS" | jq '
        if (.servers? | objects) then [ .servers[]?.tools[]? ] | length
        elif type == "object" then [ .[]? | objects | keys[]? ] | length
        else 0 end' 2>/dev/null || echo "0")
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

# Build tool arrays for each MCP server.
#
# A bound tool is identified by its "plugin key": "<toolName>_mcp_<serverName>".
# The server-level entry "sys__server__sys_mcp_<serverName>" tells LibreChat to
# expose the whole server; we ALSO bind each individual tool by its plugin key so
# the agent matches LibreChat's own UI behaviour ("Add MCP Server Tools").
#
# /api/mcp/tools has changed shape across LibreChat versions:
#   v0.8.x : { "servers": { "<server>": { "tools": [ { "pluginKey": ... }, ... ] } } }
#   legacy : { "<server>": { "<toolName>": { ... } } }
# We read the authoritative pluginKey from the v0.8.x shape and fall back to
# constructing it from the legacy shape. The previous version only understood the
# legacy shape, so on current LibreChat it silently bound NO individual tools —
# leaving agents with just the server stub. Agents seeded in that state can fail
# to attach their tools at chat time, and the model then emits raw <function_calls>
# / <tool_call> XML as plain text instead of using native tool calls.
build_tools_for_server() {
    local server_name="$1"
    local tools=()

    # Server-level entry (exposes all of this server's tools).
    tools+=("\"sys__server__sys_mcp_${server_name}\"")

    # Individual tool plugin keys.
    local plugin_keys
    plugin_keys=$(echo "$MCP_TOOLS" | jq -r --arg s "$server_name" '
        if (.servers? | objects | has($s)) then
            .servers[$s].tools[]?.pluginKey // empty       # v0.8.x nested shape
        elif (objects | has($s)) then
            .[$s] | keys[] | "\(.)_mcp_\($s)"               # legacy flat shape
        else
            empty
        end
    ' 2>/dev/null || true)

    while IFS= read -r key; do
        [ -z "$key" ] && continue
        tools+=("\"${key}\"")
    done <<< "$plugin_keys"

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
UPDATED=0
SKIPPED=0

create_agent() {
    local name="$1"
    local description="$2"
    local instructions="$3"
    local tools="$4"

    local desired_model="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"

    # How many individual (non-stub) tools we actually resolved for this agent.
    local desired_tool_count
    desired_tool_count=$(echo "$tools" | jq '[.[] | select(startswith("sys__server__") | not)] | length' 2>/dev/null || echo 0)

    # Servers this agent references (via their stub) that resolved NO individual
    # tool — i.e. that MCP server hadn't advertised its tools yet. A multi-server
    # agent (Agentic RAG, LLM Ops) can have desired_tool_count > 0 from one ready
    # server while another is still empty; re-syncing then would overwrite a healthy
    # agent with a partial set (dropping the not-ready server's tools). Treat any
    # such set as "not ready" and refuse to re-sync until every server has tools.
    local incomplete_servers
    incomplete_servers=$(echo "$tools" | jq -r '
        [ .[] | select(startswith("sys__server__sys_mcp_")) | ltrimstr("sys__server__sys_mcp_") ] as $servers
        | [ .[] | select(startswith("sys__server__") | not) ] as $indiv
        | [ $servers[] as $s | select( ([ $indiv[] | select(endswith("_mcp_" + $s)) ] | length) == 0 ) | $s ]
        | join(" ")' 2>/dev/null || echo "")

    if agent_exists "$name"; then
        # Reconcile existing agents toward the desired model AND tool bindings.
        # Re-running the seed script must be able to REPAIR an agent that was
        # created before its MCP tools were available (so it only has the server
        # stub, or an empty/stale tool set) — otherwise the agent keeps emitting
        # raw <function_calls> XML instead of native tool calls.
        local agent_id detail current_model current_tools
        agent_id=$(agent_field "$name" "id")
        detail=$(auth_curl "${LIBRECHAT_URL}/api/agents/${agent_id}" 2>/dev/null || echo '{}')
        current_model=$(echo "$detail" | jq -r '.model // empty')
        current_tools=$(echo "$detail" | jq -c '.tools // []' 2>/dev/null || echo '[]')

        local patch='{}' changes=() note=""
        if [ -n "$current_model" ] && [ "$current_model" != "$desired_model" ]; then
            patch=$(echo "$patch" | jq --arg m "$desired_model" '. + {model: $m}')
            changes+=("model ${current_model} → ${desired_model}")
        fi
        # Re-sync tools only when the desired set differs from what's stored AND
        # every server the agent uses actually resolved its individual tools.
        # Skipping when incomplete guards against overwriting a healthy agent with
        # a partial set while an MCP server is still initializing.
        local cur_sorted des_sorted
        cur_sorted=$(echo "$current_tools" | jq -cS '. // [] | sort' 2>/dev/null || echo '[]')
        des_sorted=$(echo "$tools" | jq -cS 'sort' 2>/dev/null || echo '[]')
        if [ "$cur_sorted" != "$des_sorted" ]; then
            if [ "$desired_tool_count" -gt 0 ] && [ -z "$incomplete_servers" ]; then
                patch=$(echo "$patch" | jq --argjson t "$tools" '. + {tools: $t}')
                changes+=("tools re-synced (${desired_tool_count} tool(s))")
            elif [ -n "$incomplete_servers" ]; then
                note="tool re-sync skipped — server(s) not ready: ${incomplete_servers}"
            fi
        fi

        if [ -n "$agent_id" ] && [ "$patch" != "{}" ]; then
            if auth_curl -X PATCH "${LIBRECHAT_URL}/api/agents/${agent_id}" \
                -H "Content-Type: application/json" -d "$patch" > /dev/null 2>&1; then
                local summary
                summary=$(printf '%s; ' "${changes[@]}"); summary=${summary%; }
                echo -e "  ${GREEN}↻${NC} ${name} (${summary})"
                [ -n "$note" ] && echo -e "      ${YELLOW}!${NC} ${note}; re-run once healthy"
                UPDATED=$((UPDATED + 1))
            else
                echo -e "  ${YELLOW}⤳${NC} ${name} (exists; update failed — update manually in agent settings)"
                SKIPPED=$((SKIPPED + 1))
            fi
        elif [ -n "$note" ]; then
            echo -e "  ${YELLOW}!${NC} ${name} — ${note}. Re-run once MCP servers are healthy."
            SKIPPED=$((SKIPPED + 1))
        else
            echo -e "  ${YELLOW}⤳${NC} ${name} (already exists, up to date)"
            SKIPPED=$((SKIPPED + 1))
        fi
        return 0
    fi

    local payload
    payload=$(jq -n \
        --arg name "$name" \
        --arg desc "$description" \
        --arg inst "$instructions" \
        --arg model "${ANTHROPIC_MODEL:-claude-sonnet-4-6}" \
        --argjson tools "$tools" \
        '{
            name: $name,
            description: $desc,
            instructions: $inst,
            provider: "anthropic",
            model: $model,
            tools: $tools
        }')

    local response
    response=$(auth_curl -X POST "${LIBRECHAT_URL}/api/agents" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1)

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        if [ "$desired_tool_count" -gt 0 ] && [ -z "$incomplete_servers" ]; then
            echo -e "  ${GREEN}✓${NC} ${name} (${desired_tool_count} tool(s) bound)"
        elif [ "$desired_tool_count" -gt 0 ] && [ -n "$incomplete_servers" ]; then
            # Multi-server agent created while some server was still initializing —
            # it has a partial tool set and may emit raw tool-call XML for the
            # missing server until re-synced.
            echo -e "  ${YELLOW}!${NC} ${name} — created with PARTIAL tools; server(s) not ready: ${incomplete_servers}."
            echo -e "      Re-run ${GREEN}./scripts/seed-librechat-agents.sh${NC} once MCP servers are healthy to bind them."
        else
            # Created with only a server-level stub — MCP tools weren't live yet.
            # The agent may emit raw tool-call XML until re-synced.
            echo -e "  ${YELLOW}!${NC} ${name} — created with NO individual MCP tools (server still initializing?)."
            echo -e "      Re-run ${GREEN}./scripts/seed-librechat-agents.sh${NC} once MCP servers are healthy to bind them."
        fi
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
You are an Agentic RAG assistant. You answer questions about ClickHouse, RAG, vector search, OpenTelemetry, and LLM observability from a knowledge base stored in ClickHouse (native vector_similarity search) — and you can also query live ClickHouse demo datasets when a question needs real numbers.

Routing rule — decide first, then act, do NOT answer from memory:

A. KNOWLEDGE / CONCEPT / HOW-TO questions (the common case): you MUST call `agentic_rag_answer`, passing the question verbatim. That tool runs the full self-correcting corrective-RAG graph server-side (route -> retrieve -> grade -> self-correct -> generate -> reflect) and returns a grounded, cited answer plus its route and grounded flags. Present the returned `answer` and mention the documents it cited. Do NOT hand-roll retrieval with `retrieve_kb` for these — `agentic_rag_answer` is the graded, evaluated path (it emits the retrieval_relevance / groundedness / faithfulness / context-relevance / answer-relevance scores).

B. DATASET-NUMBER questions (taxi rides, github stars, stackoverflow counts, etc.): write and run a ClickHouse SELECT via the playground tools instead — these need live SQL, not the knowledge base.

C. INSPECTION only: use `retrieve_kb` / `list_documents` when the user explicitly asks to see raw retrieved chunks or what documents exist — not to compose a normal answer.

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
echo "  Updated: ${UPDATED} agent(s) (model and/or tools brought up to date)"
echo "  Skipped: ${SKIPPED} agent(s) (already existed)"
echo ""
echo -e "  Open LibreChat: ${GREEN}${LIBRECHAT_URL}${NC}"
echo "  Select an agent from the dropdown to start chatting."
echo ""
