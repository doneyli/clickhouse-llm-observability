# LLM Observability with ClickStack, TruLens, Langfuse, and OpenLLMetry

A comprehensive demo for LLM observability showing how to monitor, trace, and evaluate LLM applications in production. Features dual evaluation platforms (TruLens + Langfuse) both powered by ClickHouse.

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
│          │ Dual Instrumentation                  │ librechat-exporter       │
│          │ • OpenLLMetry (auto) ────────────┐    │ (polls MongoDB)          │
│          │ • Langfuse SDK (callbacks) ──┐   │    │        │                 │
│          │                   │          │   │    │        │                 │
│          └───────────────────┘          │   │    └────────┘                 │
│                                         │   │             │                 │
│                                         │   │    ┌────────┘                 │
│                                         │   │    │                          │
│                                         ▼   ▼    ▼                          │
│                              ┌───────────────────────────────┐              │
│                              │         ClickHouse            │              │
│                              │    (Unified Data Backend)     │              │
│                              │                               │              │
│                              │  • otel_traces (from HyperDX) │              │
│                              │  • langfuse_* (from Langfuse) │              │
│                              └───────────────────────────────┘              │
│                                    ▲               ▲                        │
│                                    │               │                        │
│                       ┌────────────┘               └────────────┐           │
│                       │                                         │           │
│              ┌────────┴────────┐                    ┌───────────┴───────┐   │
│              │ ClickStack/     │                    │     Langfuse      │   │
│              │ HyperDX         │                    │     (Web UI)      │   │
│              │ localhost:8080  │                    │   localhost:3001  │   │
│              │                 │                    │                   │   │
│              │ • OTEL traces   │                    │ • LLM traces      │   │
│              │ • gen_ai.*      │                    │ • Scores          │   │
│              │ • Dashboards    │                    │ • Visualization   │   │
│              └────────┬────────┘                    └─────────┬─────────┘   │
│                       │                                       │             │
│                       │ queries                               │ queries     │
│                       ▼                                       ▼             │
│              ┌─────────────────┐                    ┌─────────────────┐     │
│              │ trace-evaluator │                    │langfuse-evaluator│    │
│              │   (TruLens)     │                    │  (LLM-as-judge) │     │
│              │                 │                    │                 │     │
│              │ • Relevance     │                    │ • Relevance     │     │
│              │ • Coherence     │                    │ • Coherence     │     │
│              └────────┬────────┘                    └─────────────────┘     │
│                       │                                                     │
│                       ▼                                                     │
│              ┌─────────────────┐                                            │
│              │TruLens Dashboard│                                            │
│              │ localhost:8501  │                                            │
│              │                 │                                            │
│              │ • Quality scores│                                            │
│              │ • Judge reasoning                                            │
│              └─────────────────┘                                            │
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

### Phase 6: Demonstrate Evaluation Failure Modes (Optional)

Export synthetic conversations that show what low-quality LLM responses look like:

```bash
# Build and export test scenarios
docker compose build test-scenarios
docker compose run --rm test-scenarios
```

This exports 4 pre-crafted scenarios:
| Scenario | Issue | Expected Scores |
|----------|-------|-----------------|
| Off-Topic Response | Answers wrong question | Relevance: 0.0, Coherence: 1.0 |
| Contradictory Response | Self-contradicting | Relevance: 0.0, Coherence: 0.0 |
| Fabricated Information | Hallucinated facts | Relevance: 0.0, Coherence: 0.3 |
| Good Response (Control) | Correct answer | Relevance: 1.0, Coherence: 1.0 |

Run evaluations on them:
```bash
docker compose run --rm trace-evaluator python main.py \
  --service test-scenarios --hours 1
```

View results:
- **HyperDX**: Search `service:test-scenarios` to see traces
- **TruLens**: Look for `test-scenarios-eval` to compare scores

See [Evaluation Scenarios Documentation](docs/EVALUATION_SCENARIOS.md) for details on each failure mode.

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
| **HyperDX** | http://localhost:8080 | Traces, logs, metrics (OTEL) |
| **Langfuse** | http://localhost:3001 | Traces, scores, LLM visualization |
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

**This demo shows three complementary approaches:**
1. **Operational Observability** (OpenLLMetry → HyperDX) - What happened?
2. **Quality Evaluation** (TruLens) - How good was it? (SQLite storage)
3. **Quality Evaluation** (Langfuse) - How good was it? (ClickHouse storage)

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

### Langfuse (Alternative Evaluation Platform)
LLM observability with ClickHouse backend:
- Rich trace timeline visualization
- Custom numeric scores
- Native web UI for exploration
- Shares ClickHouse with HyperDX

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

## Langfuse Integration (Alternative Evaluation Platform)

This demo supports both **TruLens** and **Langfuse** for LLM evaluation, both using ClickHouse as the backend. This demonstrates the power of ClickHouse as a unified observability store.

### Starting Langfuse

```bash
# 1. Create Langfuse database in ClickStack
docker exec clickstack clickhouse-client --user api --password api \
  --query "CREATE DATABASE IF NOT EXISTS langfuse"

# 2. Start Langfuse services (uses docker compose profile)
docker compose --profile langfuse up -d

# 3. Wait for Langfuse to be ready (~2-3 minutes)
until curl -s http://localhost:3001 > /dev/null 2>&1; do sleep 5; echo "Waiting for Langfuse..."; done
echo "Langfuse ready!"

# 4. Create account and get API keys
# Open http://localhost:3001 → Sign up → Project Settings → API Keys

# 5. Add keys to .env
echo "LANGFUSE_PUBLIC_KEY=pk-lf-..." >> .env
echo "LANGFUSE_SECRET_KEY=sk-lf-..." >> .env

# 6. Restart demo apps to enable dual instrumentation
docker compose restart text-to-sql vector-rag
```

### Viewing Evaluations in Both Platforms

| Platform | URL | What You'll See |
|----------|-----|-----------------|
| **HyperDX** | http://localhost:8080 | Raw OTEL traces, gen_ai.* attributes |
| **TruLens** | http://localhost:8501 | Relevance, Coherence scores + judge reasoning |
| **Langfuse** | http://localhost:3001 | Traces, scores, LLM calls visualization |

### Running Langfuse Evaluations

```bash
# List traces in Langfuse
docker compose --profile langfuse run --rm langfuse-evaluator --list

# Evaluate recent traces (same metrics as TruLens)
docker compose --profile langfuse run --rm langfuse-evaluator --hours 24 --limit 50

# Force re-evaluation of already scored traces
docker compose --profile langfuse run --rm langfuse-evaluator --force
```

### Platform Comparison

| Feature | TruLens | Langfuse |
|---------|---------|----------|
| Storage Backend | SQLite (local) | ClickHouse (shared) |
| Trace Visualization | Basic table | Rich timeline |
| Score Types | Pre-built feedbacks | Custom scores |
| Dashboard | Streamlit | Native web UI |
| Production Use | Evaluation-focused | Full observability |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | **Required.** Your Anthropic API key |
| `CLICKSTACK_API_KEY` | - | **Required.** From HyperDX Team Settings |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model for generation |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model for evaluations |
| `LANGFUSE_PUBLIC_KEY` | - | *Optional.* For Langfuse dual instrumentation |
| `LANGFUSE_SECRET_KEY` | - | *Optional.* For Langfuse dual instrumentation |

---

## File Structure

```
├── text-to-sql/                # Text-to-SQL demo (OpenLLMetry + TruLens + Langfuse)
├── vector-rag/                 # Vector RAG demo (OpenLLMetry + TruLens + Langfuse)
├── trace-evaluator/            # Async evaluation from ClickHouse traces (TruLens)
├── langfuse-evaluator/         # Async evaluation using Langfuse API
├── librechat-exporter/         # MongoDB → ClickHouse exporter
├── scripts/
│   └── validate-langfuse.sh    # Langfuse setup validation
├── docs/
│   ├── EVALUATION_ARCHITECTURE.md   # Evaluation strategy deep-dive
│   ├── EVALUATION_SCENARIOS.md      # Test scenarios for failure modes
│   ├── LANGFUSE_INTEGRATION.md      # Langfuse setup and configuration
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

### Langfuse not starting or processing traces?
```bash
# Run validation script
./scripts/validate-langfuse.sh

# Check worker is using correct image (must be langfuse-worker:3)
docker ps | grep langfuse-worker

# Check queue processing
docker exec langfuse-redis redis-cli llen bull:ingestion-processing:wait

# View worker logs
docker logs langfuse-worker
```

See [Langfuse Integration Guide](docs/LANGFUSE_INTEGRATION.md) for detailed troubleshooting.

---

## Stopping Everything

```bash
# Stop demo services
docker compose down

# Stop Langfuse (if running)
docker compose --profile langfuse down

# Stop exporter
docker rm -f librechat-exporter-watcher

# Stop ClickStack
docker stop clickstack && docker rm clickstack

# Clean up volumes (including Langfuse data)
docker compose --profile langfuse down -v
docker volume rm librechat-exporter-state
```

---

## Creating Dashboards Programmatically

HyperDX/ClickStack supports programmatic dashboard creation. This section documents the working approach for LLM observability dashboards.

### Quick Start

```bash
# Create the LLM Observability Dashboard
./scripts/create-hyperdx-dashboard-mongo.sh --create

# List existing dashboards
./scripts/create-hyperdx-dashboard-mongo.sh --list

# Recreate (delete and create new)
./scripts/create-hyperdx-dashboard-mongo.sh --recreate
```

### Two Dashboard APIs

| Method | Data Sources | Use Case |
|--------|--------------|----------|
| External API v2 (`/api/v2/dashboards`) | logs, metrics only | Simple dashboards |
| MongoDB Direct Insert | **All sources including traces** | LLM observability |

> **Important:** LLM observability data is stored in **traces** (`otel_traces`), which is only accessible via MongoDB direct insert method.

### Dashboard Tile Format (config)

```javascript
{
  id: "unique-tile-id",
  x: 0, y: 0, w: 6, h: 3,  // Grid position (12 units wide)
  config: {
    name: "Tile Name",
    source: "TRACES_SOURCE_ID",  // From db.sources.find()
    select: [{
      aggFn: "count",            // count, sum, avg, min, max, count_distinct
      aggCondition: "",
      aggConditionLanguage: "sql",
      valueExpression: ""        // Field/expression to aggregate
    }],
    where: "SpanAttributes['gen_ai.request.model'] != ''",
    whereLanguage: "sql",        // Always use "sql" for SpanAttributes
    displayType: "line",         // number, line, stacked_bar, bar
    granularity: "auto"
  }
}
```

### Common Expressions for LLM Metrics

| Metric | valueExpression |
|--------|-----------------|
| Count | `""` (empty) |
| Input Tokens | `"SpanAttributes['gen_ai.usage.input_tokens']"` |
| Output Tokens | `"SpanAttributes['gen_ai.usage.output_tokens']"` |
| Latency (ms) | `"Duration / 1000000"` |
| Cost (USD) | `"toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens']) * 0.00000025 + toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens']) * 0.00000125"` |
| Unique Traces | Use `aggFn: "count_distinct"` with `valueExpression: "TraceId"` |

### Known Limitations

| Feature | Status | Workaround |
|---------|--------|------------|
| Percentiles (p50, p95, p99) | ❌ Not supported | Use `avg` or `max` |
| groupBy in config format | ⚠️ Limited | Use External API v2 for logs |
| Lucene for SpanAttributes | ❌ Doesn't work | Use `whereLanguage: "sql"` |
| Donut/pie charts | ❌ Not available | Use bar charts |

### Getting Source IDs

```bash
# List all sources
docker exec clickstack mongo --quiet --eval '
db = db.getSiblingDB("hyperdx");
db.sources.find({}, {_id: 1, name: 1, kind: 1}).forEach(function(s) {
  print(s.kind + ": " + s._id.str + " (" + s.name + ")");
});
'
```

Default source IDs:
- **Traces**: `696018e0111b88a75f8b3677` (for LLM data)
- **Logs**: `696018e0111b88a75f8b3675`
- **Metrics**: `696018e0111b88a75f8b3679`

### Example: Creating a Custom Dashboard

```bash
docker exec clickstack mongo --quiet --eval '
db = db.getSiblingDB("hyperdx");

var dashboard = {
  name: "My Custom Dashboard",
  team: db.teams.findOne({})._id,
  tags: ["custom"],
  filters: [],
  tiles: [{
    id: "tile-1",
    x: 0, y: 0, w: 6, h: 3,
    config: {
      name: "LLM Request Count",
      source: "696018e0111b88a75f8b3677",
      select: [{
        aggFn: "count",
        aggCondition: "",
        aggConditionLanguage: "sql",
        valueExpression: ""
      }],
      where: "SpanAttributes['"'"'gen_ai.request.model'"'"'] != '"'"''"'"'",
      whereLanguage: "sql",
      displayType: "line",
      granularity: "auto"
    }
  }],
  createdAt: new Date(),
  updatedAt: new Date()
};

var result = db.dashboards.insertOne(dashboard);
print("Dashboard URL: http://localhost:8080/dashboards/" + result.insertedId.str);
'
```

For complete API documentation, see [docs/hyperdx-dashboard-api.md](docs/hyperdx-dashboard-api.md).

---

## Learn More

**External Resources:**
- [OpenLLMetry](https://github.com/traceloop/openllmetry) - LLM auto-instrumentation
- [TruLens](https://www.trulens.org/) - LLM evaluation framework
- [Langfuse](https://langfuse.com/) - LLM observability platform
- [HyperDX/ClickStack](https://github.com/hyperdxio/hyperdx) - Observability platform

**Project Documentation:**
- [HyperDX Dashboard API](docs/hyperdx-dashboard-api.md) - Programmatic dashboard creation
- [Langfuse Integration Guide](docs/LANGFUSE_INTEGRATION.md) - Setup, configuration, and troubleshooting
- [Evaluation Architecture](docs/EVALUATION_ARCHITECTURE.md) - Production evaluation strategies
- [Evaluation Scenarios](docs/EVALUATION_SCENARIOS.md) - Test scenarios for failure modes
- [Quick Start Guide](docs/QUICKSTART.md) - Fast setup instructions
