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
│                                  LibreChat      in HyperDX     scores       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Step 1: Launch the Demo (10 minutes)

**What you'll do:** Get everything running with a single command.

```bash
# Clone and start
git clone https://github.com/doneyli/clickhouse-llm-observability.git
cd clickhouse-llm-observability
./setup.sh
```

**What happens:**
1. ClickStack starts (your observability backend)
2. You'll be prompted for your Anthropic API key
3. You'll get a ClickStack API key from http://localhost:8080
4. All services build and start automatically

**You'll see:**
```
╔════════════════════════════════════════════════════════════╗
║     LLM Observability Demo - One-Click Setup               ║
╚════════════════════════════════════════════════════════════╝

[OK] Docker installed
[OK] ClickStack is ready
[OK] Environment configured
[OK] Services started

Access URLs:
  LibreChat (Chat UI):        http://localhost:3080
  HyperDX (Traces):           http://localhost:8080
  Langfuse (Evaluations):     http://localhost:3001
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
5. **Every step is traced and sent to HyperDX**

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

## Step 4: Explore Your Traces in HyperDX (5 minutes)

**What you'll do:** See exactly what happened during your LLM interactions - every prompt, completion, token count, and latency metric.

### 4.1 Open HyperDX

1. Go to http://localhost:8080
2. Click **Search** in the left sidebar
3. Select the **Traces** tab

### 4.2 Find Your Traces by Service

Use these filters to find different types of traces:

| Filter: `ServiceName =` | What It Shows |
|-------------------------|---------------|
| `text-to-sql-demo` | Your API queries from Step 2 |
| `librechat-api` | LibreChat backend activity |
| `librechat-conversations` | **Full conversation traces with complete LLM outputs** |

**To see the full LLM prompts and completions from your chat sessions:**
1. Filter by `ServiceName = librechat-conversations`
2. These traces contain the complete conversation data exported from LibreChat
3. Click any trace to see the full prompt/completion pairs

### 4.3 Explore a Trace

Click on any trace to see the span hierarchy:

```
Trace: text-to-sql query
├── user_request (root span) ─────────────── Duration: 3.2s
│
├── analysis_chain
│   ├── gen_ai.prompt: "Analyze this question about London..."
│   ├── gen_ai.completion: "This requires querying uk_price_paid..."
│   └── gen_ai.usage.input_tokens: 245
│
├── mcp_query
│   └── sql: "SELECT district, AVG(price)..."
│
└── response_chain
    ├── gen_ai.prompt: "Based on these results..."
    ├── gen_ai.completion: "The most expensive areas..."
    └── gen_ai.usage.output_tokens: 156
```

### 4.4 Key Attributes to Explore

| Attribute | What It Shows | Why It Matters |
|-----------|---------------|----------------|
| `gen_ai.prompt.0.content` | The exact prompt sent | Debug prompt engineering |
| `gen_ai.completion.0.content` | The full LLM response | Verify output quality |
| `gen_ai.usage.input_tokens` | Tokens in the prompt | Cost tracking |
| `gen_ai.usage.output_tokens` | Tokens in the response | Cost tracking |
| `gen_ai.request.model` | Which model was used | Model comparison |
| `Duration` | Time for the operation | Performance analysis |

### 4.5 Try These Searches

```
# Find all LLM calls
SpanAttributes['gen_ai.system'] != ''

# Find expensive calls (high token usage)
SpanAttributes['gen_ai.usage.total_tokens'] > 1000

# Find slow responses
Duration > 5000000000  (nanoseconds = 5 seconds)
```

---

## Step 5: Run Quality Evaluations (5 minutes)

**What you'll do:** Let an LLM judge the quality of your LLM's responses using Langfuse.

### 5.1 Run the Evaluator

```bash
# Start Langfuse first (if not already running)
docker compose --profile langfuse up -d
```

**Configure Native Evaluators:**

1. Open http://localhost:3001
2. Go to **Evaluations** → **LLM-as-a-Judge**
3. Click **+ New Evaluator**
4. Choose a template (Hallucination, Helpfulness, etc.)
5. Set sampling to 100% for demo
6. Save - evaluators now run automatically on new traces

**Run test scenarios to generate evaluation data:**
```bash
docker compose --profile tools run --rm test-scenarios
```

**What you'll see:**
Native evaluators automatically score new traces. Check the Traces view to see scores appear.

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

## Step 6: Explore More (Optional)

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
# See trace counts by service
docker exec clickstack clickhouse-client --user api --password api \
  --query "SELECT ServiceName, COUNT(*) as traces
           FROM otel_traces
           WHERE Timestamp > now() - INTERVAL 1 HOUR
           GROUP BY ServiceName
           ORDER BY traces DESC"

# See token usage summary
docker exec clickstack clickhouse-client --user api --password api \
  --query "SELECT
             ServiceName,
             SUM(toUInt32OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as input_tokens,
             SUM(toUInt32OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as output_tokens
           FROM otel_traces
           WHERE Timestamp > now() - INTERVAL 1 HOUR
           GROUP BY ServiceName"
```

---

## Quick Reference Card

| Task | Command/URL |
|------|-------------|
| **Start demo** | `./setup.sh` |
| **Stop demo** | `./setup.sh --cleanup` |
| **Check status** | `./setup.sh --status` |
| **Chat UI** | http://localhost:3080 |
| **View traces** | http://localhost:8080 |
| **Quality scores** | http://localhost:8501 |
| **Text-to-SQL API** | http://localhost:8002/query |
| **Vector RAG API** | http://localhost:8003/query |
| **View logs** | `docker compose logs -f [service]` |
| **Run evaluator** | `docker compose run --rm trace-evaluator --hours 1` |
| **List services with traces** | `docker compose run --rm trace-evaluator --list-services` |

---

## Troubleshooting

### No traces appearing?
```bash
# Check that services are running
docker compose ps

# Verify ClickStack API key is set
grep CLICKSTACK_API_KEY .env

# Check service logs for errors
docker compose logs text-to-sql --tail=50
```

### LibreChat tool not working?
- Make sure you enabled "clickhouse-playground" from the tools dropdown
- Check that the MCP server is running: `docker compose logs mcp-clickhouse`

### Langfuse dashboard empty?
- Generate traces first: `docker compose run --rm text-to-sql python main.py`
- The dashboard only shows data after traces have been generated
- Configure evaluators in UI: **Evaluations** → **LLM-as-a-Judge**

---

## What's Next?

| Goal | Resource |
|------|----------|
| Understand the architecture | [README](../README.md#architecture) |
| Learn evaluation strategies | [Evaluation Architecture](./EVALUATION_ARCHITECTURE.md) |
| Test failure scenarios | [Evaluation Scenarios](./EVALUATION_SCENARIOS.md) |
| Learn about Langfuse | [Langfuse Integration](./LANGFUSE_INTEGRATION.md) |
| Create custom dashboards | [Dashboard API](./hyperdx-dashboard-api.md) |

---

*Now you've experienced the complete LLM observability pipeline - from asking questions to tracing every interaction to evaluating quality. Apply these patterns to your own LLM applications!*
