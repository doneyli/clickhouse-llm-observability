# LLM Observability with ClickStack, TruLens, and OpenLLMetry

A comprehensive demo for LLM observability showing how to monitor, trace, and evaluate LLM applications in production.

Based on: https://clickhouse.com/blog/llm-observability-clickstack-mcp

---

## Quick Demo Walkthrough

This demo shows the complete LLM observability pipeline in action:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           DEMO ARCHITECTURE                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│   │ Text-to-SQL │     │ Vector RAG  │     │  LibreChat  │                   │
│   │    Demo     │     │    Demo     │     │   (Chat)    │                   │
│   └──────┬──────┘     └──────┬──────┘     └──────┬──────┘                   │
│          │                   │                   │                          │
│          │ OpenLLMetry       │ OpenLLMetry       │ librechat-exporter       │
│          │ (auto)            │ (auto)            │ (polls MongoDB)          │
│          │                   │                   │                          │
│          └───────────────────┴───────────────────┘                          │
│                              │                                              │
│                              ▼                                              │
│          ┌───────────────────────────────────────┐                          │
│          │         ClickStack / HyperDX          │                          │
│          │         http://localhost:8080         │                          │
│          │                                       │                          │
│          │  • All LLM traces with gen_ai.*       │                          │
│          │  • Prompts, completions, tokens       │                          │
│          │  • Latency metrics                    │                          │
│          │  • Evaluation spans with eval.*       │                          │
│          └───────────────────┬───────────────────┘                          │
│                              │                                              │
│                              │ queries traces                               │
│                              ▼                                              │
│          ┌───────────────────────────────────────┐                          │
│          │          trace-evaluator              │                          │
│          │      (run manually or scheduled)      │                          │
│          │                                       │                          │
│          │  • Queries traces from ClickHouse     │                          │
│          │  • Runs TruLens LLM-as-judge evals    │                          │
│          │  • Emits eval spans back to ClickStack│                          │
│          └──────────┬────────────────┬───────────┘                          │
│                     │                │                                      │
│      eval spans     │                │  scores & reasoning                  │
│      (OTEL)         │                │                                      │
│          ┌──────────┘                └───────────┐                          │
│          │                                       │                          │
│          ▼                                       ▼                          │
│   ┌─────────────────────┐          ┌─────────────────────┐                  │
│   │  ClickStack/HyperDX │          │  TruLens Dashboard  │                  │
│   │                     │          │  http://localhost:  │                  │
│   │ • eval.source_model │          │       8501          │                  │
│   │ • eval.*_score      │          │                     │                  │
│   │ • gen_ai.request.   │          │ • Quality scores    │                  │
│   │   model (judge)     │          │ • Judge reasoning   │                  │
│   └─────────────────────┘          └─────────────────────┘                  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Demo Flow

### Phase 1: Start the Infrastructure

```bash
# 1. Start ClickStack (observability backend)
docker run -d --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  docker.hyperdx.io/hyperdx/hyperdx-all-in-one

# 2. Wait for it to be ready
until curl -s http://localhost:8080 > /dev/null; do sleep 2; done
echo "ClickStack ready!"

# 3. Get API key: Open http://localhost:8080 → Register → Team Settings → Copy Ingestion Key

# 4. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY and CLICKSTACK_API_KEY

# 5. Connect networks
docker network create clickhouse-llm-observability_default 2>/dev/null || true
docker network connect clickhouse-llm-observability_default clickstack
```

### Phase 2: Run the Demo Apps

```bash
# Build and start the demos + TruLens dashboard
docker compose build text-to-sql vector-rag trulens-dashboard
docker compose up -d text-to-sql vector-rag trulens-dashboard
```

### Phase 3: Generate Some LLM Traffic

**Option A: Use the Text-to-SQL API**
```bash
curl -X POST http://localhost:8002/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How many houses were sold in London in 2020?"}'
```

**Option B: Use the Vector RAG API**
```bash
curl -X POST http://localhost:8003/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is ClickHouse?"}'
```

**Option C: Use LibreChat (if running)**
1. Open http://localhost:3080
2. Ask: "What are the main use cases for ClickHouse?"

### Phase 4: View Traces in HyperDX

1. Open http://localhost:8080
2. Go to **Search** → **Traces**
3. You'll see LLM calls with:
   - `gen_ai.prompt.0.content` - The user's question
   - `gen_ai.completion.0.content` - The LLM's response
   - `gen_ai.usage.input_tokens` / `output_tokens` - Token counts
   - `gen_ai.request.model` - Model used

### Phase 5: View Quality Scores in TruLens

1. Open http://localhost:8501
2. See the **Leaderboard** with apps:
   - `text-to-sql-demo` - Text-to-SQL evaluations
   - `vector-rag-demo` - RAG evaluations
3. Click **Records** to see individual queries with scores
4. Click a feedback badge to see the judge's reasoning

---

## Adding LibreChat to the Pipeline

LibreChat stores conversations in MongoDB, not via OpenTelemetry. To include LibreChat conversations in the observability pipeline:

### Step 1: Start the LibreChat Exporter

```bash
# Build the exporter
docker compose build librechat-exporter

# Start continuous export (polls every 10 seconds)
source .env
docker run -d --name librechat-exporter-watcher \
  --network librechat_default \
  --add-host=host.docker.internal:host-gateway \
  -v librechat-exporter-state:/tmp \
  -e MONGO_URI=mongodb://chat-mongodb:27017/LibreChat \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318/v1/traces \
  -e CLICKSTACK_API_KEY="${CLICKSTACK_API_KEY}" \
  clickhouse-llm-observability-librechat-exporter \
  python main.py --watch --interval 10
```

**Note**: Adjust `--network` and `MONGO_URI` based on your LibreChat setup. Common variations:
- `librechat_default` network with `chat-mongodb` container
- `clickhouse-llm-observability_default` network with `librechat-mongodb` container

### Step 2: Send Messages in LibreChat

1. Open LibreChat at http://localhost:3080
2. Send a message like "What is ClickHouse?"
3. Wait ~10 seconds for the exporter to pick it up

### Step 3: Verify in HyperDX

1. Open http://localhost:8080
2. Filter by `ServiceName = librechat-conversations`
3. You should see your conversation with `gen_ai.*` attributes

### Step 4: Run Quality Evaluations

```bash
# Evaluate LibreChat conversations
docker compose run --rm trace-evaluator python main.py \
  --service librechat-conversations \
  --hours 24 \
  --limit 20
```

### Step 5: View in TruLens

1. Open http://localhost:8501
2. Look for `librechat-conversations-eval` in the app list
3. View quality scores and judge reasoning

### Step 6: Correlate Conversations with Evaluations in HyperDX

The trace-evaluator emits OTEL spans showing both the **generation model** and the **judge model** for each evaluation:

| Attribute | Description |
|-----------|-------------|
| `gen_ai.request.model` | Judge model (e.g., `claude-3-5-haiku`) |
| `eval.source_model` | Generation model being evaluated |
| `eval.source_trace_id` | Links back to original conversation |
| `eval.relevance_score` | Relevance score (0.0-1.0) |
| `eval.coherence_score` | Coherence score (0.0-1.0) |

**To find evaluations in HyperDX:**
1. Search: `service:trace-evaluator` to see all evaluations
2. To find evaluation for a specific conversation: `eval.source_trace_id:<conversation-trace-id>`

**SQL Query to join conversations with their evaluations:**
```sql
SELECT
    e.SpanAttributes['eval.source_model'] as generation_model,
    e.SpanAttributes['gen_ai.request.model'] as judge_model,
    e.SpanAttributes['eval.relevance_score'] as relevance,
    substring(e.SpanAttributes['eval.input'], 1, 50) as prompt
FROM otel_traces e
WHERE e.ServiceName = 'trace-evaluator'
  AND e.SpanName = 'llm.evaluation'
ORDER BY e.Timestamp DESC
```

> **Note:** Each evaluation shows 3-4 spans in HyperDX: 1 `llm.evaluation` parent span + 2-3 `ChatAnthropic.chat` child spans (the judge LLM calls). See [Evaluation Architecture](docs/EVALUATION_ARCHITECTURE.md) for details.

---

## Service Reference

| Service | URL | Purpose |
|---------|-----|---------|
| **HyperDX** | http://localhost:8080 | Traces, logs, metrics |
| **TruLens Dashboard** | http://localhost:8501 | Quality scores, evaluations |
| **Text-to-SQL API** | http://localhost:8002 | Demo: Natural language → SQL |
| **Vector RAG API** | http://localhost:8003 | Demo: RAG with embeddings |
| **LibreChat** | http://localhost:3080 | Chat UI (if running) |

---

## Why LLM Observability?

Traditional monitoring tracks requests, errors, and latency. LLM applications need more:

| Challenge | Solution |
|-----------|----------|
| **Non-deterministic outputs** | Capture every prompt/completion pair |
| **Quality is subjective** | LLM-as-judge automated scoring |
| **Cost scales with tokens** | Track token usage per request |
| **Debugging is hard** | Full trace visibility |

**This demo shows two complementary approaches:**
1. **Operational Observability** (OpenLLMetry → HyperDX) - What happened?
2. **Quality Evaluation** (TruLens) - How good was it?

---

## Understanding the Stack

### OpenLLMetry (Operational Data)
Auto-instruments LLM frameworks to capture:
- Prompts and completions
- Token counts
- Latency
- Model information

### TruLens (Quality Data)
LLM-as-judge evaluation:
- Answer relevance
- Coherence
- Groundedness (for RAG)
- Custom metrics

### ClickStack/HyperDX (Storage & Visualization)
Self-hosted observability platform:
- ClickHouse backend for traces
- Search and filter
- Dashboards

---

## Useful Commands

### Exporter Management
```bash
# View exporter logs
docker logs -f librechat-exporter-watcher

# Stop exporter
docker rm -f librechat-exporter-watcher

# Restart exporter with different interval
docker rm -f librechat-exporter-watcher
source .env
docker run -d --name librechat-exporter-watcher \
  --network librechat_default \
  --add-host=host.docker.internal:host-gateway \
  -v librechat-exporter-state:/tmp \
  -e MONGO_URI=mongodb://chat-mongodb:27017/LibreChat \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318/v1/traces \
  -e CLICKSTACK_API_KEY="${CLICKSTACK_API_KEY}" \
  clickhouse-llm-observability-librechat-exporter \
  python main.py --watch --interval 5
```

### Trace Evaluator
```bash
# List services with LLM traces
docker compose run --rm trace-evaluator python main.py --list-services

# One-time evaluation
docker compose run --rm trace-evaluator python main.py \
  --service librechat-conversations \
  --hours 24

# Watch mode - continuously evaluate new traces (recommended)
# Run as background container:
source .env
docker run -d --name trace-evaluator-watcher \
  --network clickhouse-llm-observability_default \
  -v clickhouse-llm-observability_trulens-data:/trulens-data \
  -v trace-evaluator-state:/tmp \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -e TRULENS_DATABASE_URL="sqlite:////trulens-data/trulens.sqlite" \
  -e TRULENS_OTEL_TRACING=0 \
  -e CLICKHOUSE_TRACE_USER=api \
  -e CLICKHOUSE_TRACE_PASSWORD=api \
  clickhouse-llm-observability-trace-evaluator \
  python main.py --watch --interval 60 --service librechat-conversations

# View evaluator logs
docker logs -f trace-evaluator-watcher

# Evaluate with sampling (for high volume)
docker compose run --rm trace-evaluator python main.py \
  --service librechat-conversations \
  --sample-rate 0.1
```

### Debugging
```bash
# Check what's in ClickHouse
docker exec clickstack clickhouse-client --user api --password api \
  --query "SELECT ServiceName, COUNT(*) FROM otel_traces GROUP BY ServiceName"

# Check MongoDB for conversations
docker exec chat-mongodb mongosh --quiet --eval "
  db = db.getSiblingDB('LibreChat');
  db.messages.find({}).sort({createdAt: -1}).limit(5).forEach(doc => {
    print(doc.createdAt + ' | ' + (doc.text || 'N/A').substring(0, 50));
  });
"
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | **Required.** Your Anthropic API key |
| `CLICKSTACK_API_KEY` | - | **Required.** From HyperDX Team Settings |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model for generation |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model for evaluations |

---

## File Structure

```
├── text-to-sql/                # Text-to-SQL demo (OpenLLMetry + TruLens)
├── vector-rag/                 # Vector RAG demo (OpenLLMetry + TruLens)
├── trace-evaluator/            # Async evaluation from ClickHouse traces
├── librechat-exporter/         # MongoDB → ClickHouse exporter
├── docs/
│   ├── EVALUATION_ARCHITECTURE.md   # Evaluation strategy deep-dive
│   └── QUICKSTART.md                # Quick setup guide
├── Dockerfile.*                # Container definitions
├── docker-compose.yaml         # Service orchestration
└── .env.example               # Environment template
```

---

## Troubleshooting

### Traces not appearing in HyperDX?
```bash
# Check ClickStack is running
curl http://localhost:8080

# Check API key
echo $CLICKSTACK_API_KEY

# Check network connectivity
docker exec clickstack clickhouse-client --user api --password api \
  --query "SELECT COUNT(*) FROM otel_traces"
```

### TruLens dashboard empty?
```bash
# Check database exists
docker compose exec trulens-dashboard ls -la /trulens-data/

# Run some evaluations first
docker compose run --rm trace-evaluator python main.py --service text-to-sql-demo --hours 24
```

### LibreChat exporter not finding conversations?
```bash
# Check MongoDB container name
docker ps | grep mongo

# Test MongoDB connection
docker exec <mongo-container> mongosh --eval "db.getSiblingDB('LibreChat').messages.countDocuments({})"
```

---

## Stopping Everything

```bash
# Stop demo services
docker compose down

# Stop exporter
docker rm -f librechat-exporter-watcher

# Stop ClickStack
docker stop clickstack && docker rm clickstack

# Clean up volumes
docker compose down -v
docker volume rm librechat-exporter-state
```

---

## Learn More

- [OpenLLMetry](https://github.com/traceloop/openllmetry) - LLM auto-instrumentation
- [TruLens](https://www.trulens.org/) - LLM evaluation framework
- [HyperDX/ClickStack](https://github.com/hyperdxio/hyperdx) - Observability platform
- [Evaluation Architecture Guide](docs/EVALUATION_ARCHITECTURE.md) - Production evaluation strategies
