# Guided User Journey - LLM Observability Demo

A hands-on walkthrough from setup to insights. Follow along step-by-step to experience the complete LLM observability pipeline.

**Total Time:** ~35 minutes

---

## Your Journey at a Glance

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          YOUR JOURNEY AT A GLANCE                            │
│                                                                             │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐  │
│   │  SETUP  │───▶│  QUERY  │───▶│  CHAT   │───▶│  TRACE  │───▶│ EVALUATE│  │
│   │ 10 min  │    │  5 min  │    │ 10 min  │    │  5 min  │    │  5 min  │  │
│   └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘  │
│                                                                             │
│   Launch the     Ask questions   Interactive    See what       Check        │
│   demo stack     via API         chat with      happened       quality      │
│                                  LibreChat      in Langfuse    scores       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

> **Cloud mode available:** Set `DEPLOY_MODE=cloud` in `.env` to use Langfuse Cloud instead of the local stack. See [Deployment Modes](../README.md#deployment-modes).

## Step 1: Launch the Demo (10 minutes)

**What you'll do:** Get everything running with a single command.

```bash
# Clone and start
git clone https://github.com/doneyli/clickhouse-llm-observability.git
cd clickhouse-llm-observability
./setup.sh
```

**What happens:**
1. You'll be prompted for your Anthropic API key
2. All services build and start automatically (including Langfuse)
3. Langfuse initializes with ClickHouse as its analytics backend

**You'll see:**
```
╔════════════════════════════════════════════════════════════╗
║     LLM Observability Demo - One-Click Setup               ║
╚════════════════════════════════════════════════════════════╝

[OK] Docker installed
[OK] Environment configured
[OK] Services started
[OK] Langfuse is ready

Access URLs:
  LibreChat (Chat UI):        http://localhost:3080
  Langfuse (Observability):   http://localhost:3001
```

**Verify everything is running:**
```bash
./setup.sh --status
```

---

## Step 2: Ask Questions via Text-to-SQL API (5 minutes)

**What you'll do:** Send natural language questions that get converted to SQL and executed against ClickHouse's public datasets (30M+ UK property records).

**Try these queries:**

```bash
# Query 1: Property prices
curl -X POST http://localhost:8002/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the top 5 most expensive areas in London?"}'

# Query 2: Market trends
curl -X POST http://localhost:8002/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How has the average house price changed year over year since 2020?"}'

# Query 3: Volume analysis
curl -X POST http://localhost:8002/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which month had the most property sales in 2023?"}'
```

**What you'll see:**
```json
{
  "answer": "Based on the UK property data, the top 5 most expensive areas in London are:
    1. Kensington and Chelsea - £1.2M average
    2. Westminster - £980K average
    ...",
  "trace_id": "abc123..."
}
```

**What's happening behind the scenes:**
1. Your question goes to Claude for analysis
2. Claude generates a SQL query
3. The query runs against ClickHouse's `uk_price_paid` dataset
4. Claude formats the results into a natural language answer
5. **Every step is traced and sent to Langfuse**

---

## Step 3: Interactive Chat with LibreChat (10 minutes)

**What you'll do:** Have a conversation with Claude through a full-featured chat interface, with access to the ClickHouse SQL Playground tool.

### 3.1 Open LibreChat

1. Go to http://localhost:3080
2. Create an account (any email/password - it's local only)
3. Start a new conversation

### 3.2 Enable the ClickHouse SQL Playground Tool

**Important:** To query ClickHouse data through chat, you need to activate the MCP tool:

1. Look at the chat input box at the bottom
2. Click the **wrench/tools icon** (or look for a dropdown near the input)
3. Find **"clickhouse-playground"** in the available tools list
4. **Enable/activate it** by clicking or toggling it on
5. You should see a confirmation that the tool is now available

Once enabled, Claude can execute SQL queries against ClickHouse's public datasets directly in the conversation.

### 3.3 Try These Conversation Flows

**Flow A: Data Analysis with SQL Playground**
```
You: What tables are available in the ClickHouse SQL Playground?

You: Query the uk_price_paid table to find the average house price in Manchester

You: Show me the top 10 most expensive property sales in 2023
```

**Flow B: General LLM Questions**
```
You: Explain how LLM observability differs from traditional APM

You: What are the key metrics I should track for my LLM application?

You: How does OpenTelemetry capture LLM interactions?
```

**What to notice:**
- When using the SQL Playground, you'll see Claude execute queries and return results
- Responses stream in real-time
- Conversation history is preserved
- **Each message generates traces you can view in the next step**

---

## Step 4: Explore Your Traces in Langfuse (5 minutes)

**What you'll do:** See exactly what happened during your LLM interactions - every prompt, completion, token count, and latency metric.

### 4.1 Open Langfuse

1. Go to http://localhost:3001
2. Click **Traces** in the sidebar

### 4.2 Find Your Traces

Browse traces from different sources:
- **Text-to-SQL demo** - Your API queries from Step 2
- **LibreChat** - Conversations from the chat interface

Click any trace to see the full prompt/completion pairs.

### 4.3 Explore a Trace

Click on any trace to see the span hierarchy, including:
- The original user input
- LLM calls with full prompts and completions
- Tool calls (e.g., SQL queries via MCP)
- Token usage and latency for each step

### 4.4 Key Details to Explore

| Detail | What It Shows | Why It Matters |
|--------|---------------|----------------|
| Input | The exact prompt sent | Debug prompt engineering |
| Output | The full LLM response | Verify output quality |
| Usage (input tokens) | Tokens in the prompt | Cost tracking |
| Usage (output tokens) | Tokens in the response | Cost tracking |
| Model | Which model was used | Model comparison |
| Latency | Time for the operation | Performance analysis |

---

## Step 5: Run Quality Evaluations (5 minutes)

**What you'll do:** Let an LLM judge the quality of your LLM's responses using Langfuse.

### 5.1 Run the Evaluator

```bash
# Start Langfuse first (if not already running)
docker compose --profile langfuse up -d
```

**Evaluators are already provisioned.** `./setup.sh` created three observation-level
LLM-as-a-Judge evaluators (Relevance, Correctness, Hallucination) plus five
deterministic [code evaluators](CODE_EVALUATORS.md) — see them under
**Evaluators** at http://localhost:3001. (Cloud mode: create them in the UI —
**Evaluators** → **+ Set up evaluator**, target **Observations**.)

**Run test scenarios to generate evaluation data:**
```bash
docker compose --profile tools run --rm test-scenarios
```

**What you'll see:**
Evaluators score new observations within ~30 seconds. Open a trace and check the
Scores panel — judge scores attach to the matching observation in the trace tree.

### 5.2 View Results in Langfuse Dashboard

1. Go to http://localhost:3001
2. Click **Traces** to see all traces with evaluation scores
3. Click any trace to see individual evaluation details

**What you'll see in the Traces view:**

| Trace | Answer Relevance | Coherence | Duration |
|-------|------------------|-----------|----------|
| text-to-sql query | 0.85 | 0.88 | 2.3s |

### 5.3 Explore Individual Traces

Click any trace to see:
- The original question
- The LLM's response
- Scores for each metric
- Token usage and cost

**Example reasoning:**
```
Answer Relevance: 0.92
Reason: "The response directly addresses the question about expensive
areas in London. It provides specific district names and price figures.
Minor deduction because it didn't explain the methodology used."

Coherence: 0.88
Reason: "The response is well-structured with a clear numbered list.
The explanation flows logically from introduction to data to conclusion."
```

---

## Step 6: Explore Traces via CLI (Optional, 3 minutes)

**What you'll do:** Use the Langfuse CLI to browse traces from your terminal.

**Prerequisites:** Node.js 18+ (for `npx`)

```bash
# List recent traces
./scripts/langfuse-cli.sh traces list --limit 5

# Get details for a specific trace (use an ID from the list above)
./scripts/langfuse-cli.sh traces get <trace-id>

# Check evaluation scores
./scripts/langfuse-cli.sh scores list
```

See [Langfuse CLI docs](./LANGFUSE_CLI.md) for more commands.

---

## Step 7: Explore More (Optional)

### Run the Vector RAG Demo

```bash
# Start the vector RAG service
docker compose up -d vector-rag

# Query it
curl -X POST http://localhost:8003/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is retrieval augmented generation?"}'
```

### Generate Load for Dashboard Testing

```bash
# Run 10 varied queries to populate dashboards
for q in \
  "What are the most expensive areas in London?" \
  "How many properties sold in 2023?" \
  "Average house price in Manchester?" \
  "Cheapest areas to buy property?" \
  "Property sales trend over last 5 years?" \
  "Most popular property types?" \
  "Price difference between flats and houses?" \
  "Which city has fastest growing prices?" \
  "Average time on market by region?" \
  "Seasonal patterns in property sales?"; do

  curl -s -X POST http://localhost:8002/query \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"$q\"}" > /dev/null
  echo "Sent: $q"
  sleep 2
done
```

### Query ClickHouse Directly

```bash
# Connect to ClickHouse and explore Langfuse data
docker compose exec clickhouse clickhouse-client \
  --query "SELECT count(*) FROM langfuse.traces"
```

---

## Quick Reference Card

| Task | Command/URL |
|------|-------------|
| **Start demo** | `./setup.sh` |
| **Stop demo** | `./setup.sh --cleanup` |
| **Check status** | `./setup.sh --status` |
| **Chat UI** | http://localhost:3080 |
| **View traces** | http://localhost:3001 |
| **Quality scores** | http://localhost:3001 |
| **Text-to-SQL API** | http://localhost:8002/query |
| **Vector RAG API** | http://localhost:8003/query |
| **View logs** | `docker compose logs -f [service]` |
| **Langfuse CLI** | `./scripts/langfuse-cli.sh traces list --limit 5` |
| **Seed demo data** | `./scripts/seed-demo-data.sh` |

---

## Troubleshooting

### No traces appearing?
```bash
# Check that services are running
docker compose ps

# Verify Langfuse API keys are set
grep LANGFUSE .env

# Check service logs for errors
docker compose logs text-to-sql --tail=50
```

### LibreChat tool not working?
- Make sure you enabled "clickhouse-playground" from the tools dropdown
- Check that the MCP server is running: `docker compose logs mcp-clickhouse`

### Langfuse dashboard empty?
- Generate traces first: `docker compose run --rm text-to-sql python main.py`
- The dashboard only shows data after traces have been generated
- Missing evaluator scores? Re-run `./scripts/seed-llm-judge-evaluators.sh` and `./scripts/seed-code-evaluators.sh`

---

## What's Next?

| Goal | Resource |
|------|----------|
| Understand the architecture | [README](../README.md#architecture) |
| Learn evaluation strategies | [Evaluation Architecture](./EVALUATION_ARCHITECTURE.md) |
| Test failure scenarios | [Evaluation Scenarios](./EVALUATION_SCENARIOS.md) |
| Learn about Langfuse | [Langfuse Integration](./LANGFUSE_INTEGRATION.md) |

---

*Now you've experienced the complete LLM observability pipeline - from asking questions to tracing every interaction to evaluating quality. Apply these patterns to your own LLM applications!*
