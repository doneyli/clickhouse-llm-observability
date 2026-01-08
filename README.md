# LLM Observability with ClickStack, TruLens, and OpenLLMetry

A comprehensive demo for LLM observability showing:
- **OpenTelemetry/OpenLLMetry** - Trace prompts, completions, token usage, latency
- **TruLens** - Evaluate LLM output quality (relevance, coherence)
- **ClickStack/HyperDX** - Unified observability UI with ClickHouse backend

Based on: https://clickhouse.com/blog/llm-observability-clickstack-mcp

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────────────┐
│   LibreChat     │────▶│  ClickHouse MCP  │◀────│      Python RAG App         │
│  (Port 3080)    │     │   (Port 8001)    │     │      (Port 8002)            │
│                 │     │                  │     │                             │
│  Chat UI with   │     │  SQL Playground  │     │  ┌─────────────────────┐   │
│  Claude + MCP   │     │  35+ datasets    │     │  │ LangChain + Claude  │   │
└────────┬────────┘     └────────┬─────────┘     │  └──────────┬──────────┘   │
         │                       │               │             │              │
         │                       │               │  ┌──────────▼──────────┐   │
         │                       │               │  │      TruLens        │   │
         │                       │               │  │  ────────────────   │   │
         │                       │               │  │  • Relevance eval   │   │
         │                       │               │  │  • Coherence eval   │   │
         │                       │               │  └──────────┬──────────┘   │
         │                       │               └─────────────┼──────────────┘
         │ OTel Traces           │ OTel Traces                 │ OTel + TruLens
         ▼                       ▼                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         ClickStack / HyperDX                                 │
│                      UI: 8080 | OTLP: 4317/4318                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • Trace visualization    • Token analytics    • Latency percentiles        │
│  • Log aggregation        • Cost estimation    • Error tracking             │
└──────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────┐
                    │    TruLens Dashboard        │
                    │    (Port 8501)              │
                    │  ─────────────────────────  │
                    │  • Evaluation leaderboard   │
                    │  • Feedback details + CoT   │
                    │  • Record browser           │
                    └─────────────────────────────┘
```

---

## Choose Your Path

### Scenario A: Full Demo (LibreChat + Python RAG + All Observability)

Best for: Exploring all components together

```bash
# 1. Start ClickStack
docker run -d --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  docker.hyperdx.io/hyperdx/hyperdx-all-in-one

# 2. Get API key from http://localhost:8080 → Team Settings

# 3. Configure environment
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and CLICKSTACK_API_KEY

# 4. Generate secrets for LibreChat
echo "CREDS_KEY=$(openssl rand -hex 32)" >> .env
echo "CREDS_IV=$(openssl rand -hex 16)" >> .env
echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env
echo "JWT_REFRESH_SECRET=$(openssl rand -hex 32)" >> .env

# 5. Connect ClickStack to network and start everything
docker network create clickhouse-llm-observability_default 2>/dev/null || true
docker network connect clickhouse-llm-observability_default clickstack
docker compose up -d
docker compose up -d python-rag  # Start RAG with TruLens
```

### Scenario B: Python RAG Only (TruLens + OpenLLMetry Demo)

Best for: Focused LLM evaluation demo without LibreChat

```bash
# 1. Start ClickStack
docker run -d --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  docker.hyperdx.io/hyperdx/hyperdx-all-in-one

# 2. Get API key from http://localhost:8080 → Team Settings

# 3. Configure environment
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and CLICKSTACK_API_KEY

# 4. Connect and build
docker network create clickhouse-llm-observability_default 2>/dev/null || true
docker network connect clickhouse-llm-observability_default clickstack
docker compose build python-rag

# 5. Run the demo
docker compose up python-rag
```

### Scenario C: Add TruLens to Existing Setup

Best for: You already have LibreChat + ClickStack running

```bash
# 1. Connect ClickStack to your compose network
docker network connect clickhouse-llm-observability_default clickstack

# 2. Build and start Python RAG
docker compose build python-rag
docker compose up -d python-rag

# 3. Generate evaluation data
docker compose exec python-rag python /scripts/generate_load.py -n 10
```

---

## Access Points

| Service | URL | Description |
|---------|-----|-------------|
| **TruLens Dashboard** | http://localhost:8501 | LLM evaluation scores and details |
| **HyperDX** | http://localhost:8080 | Traces, logs, token usage, latency |
| **LibreChat** | http://localhost:3080 | Chat UI with Claude + MCP |

---

## What is TruLens?

[TruLens](https://www.trulens.org/) is an LLM evaluation framework that measures the quality of your AI application's outputs.

### How It Works in This Demo

```
User Question
     │
     ▼
┌─────────────────────────────────────────────────────┐
│              RAG Pipeline                           │
│  Question → Analysis Chain → Response Chain → Answer│
└─────────────────────────────────────────────────────┘
     │                                            │
     │ input                                      │ output
     ▼                                            ▼
┌─────────────────────────────────────────────────────┐
│              TruLens Evaluation                     │
│                                                     │
│  ┌─────────────────┐    ┌─────────────────┐        │
│  │ Answer Relevance│    │    Coherence    │        │
│  │ ───────────────│    │ ───────────────│        │
│  │ "Does the answer│    │ "Is the response│        │
│  │  address the    │    │  well-structured│        │
│  │  question?"     │    │  and logical?"  │        │
│  │                 │    │                 │        │
│  │ Score: 0.0-1.0  │    │ Score: 0.0-1.0  │        │
│  └─────────────────┘    └─────────────────┘        │
│                                                     │
│  Evaluated by: Claude Haiku (cost-efficient)       │
└─────────────────────────────────────────────────────┘
     │
     ▼
TruLens Dashboard (http://localhost:8501)
```

### TruLens Dashboard Features

1. **Leaderboard** - Compare app versions by average scores
2. **Evaluations** - See individual feedback with chain-of-thought reasoning
3. **Records** - Browse each query with full input/output/scores

### Extending TruLens Evaluations

Edit `python-rag/trulens_config.py` to add more feedback functions:

```python
# Available evaluations from Langchain provider:
provider.relevance_with_cot_reasons      # Answer relevance
provider.coherence_with_cot_reasons      # Response coherence
provider.groundedness_measure_with_cot_reasons  # Grounded in context
provider.conciseness_with_cot_reasons    # Response brevity
provider.helpfulness_with_cot_reasons    # User helpfulness
```

---

## What is OpenLLMetry?

[OpenLLMetry](https://github.com/traceloop/openllmetry) automatically instruments LLM frameworks (LangChain, OpenAI, Anthropic) to capture:

- **Prompts** - Full text sent to the model
- **Completions** - Model's response
- **Token counts** - Input/output/total tokens
- **Latency** - Time per operation
- **Model info** - Which model was called

### Captured Span Attributes

```
gen_ai.system = "anthropic"
gen_ai.request.model = "claude-sonnet-4-20250514"
gen_ai.prompt.0.content = "You are a data analyst..."
gen_ai.completion.0.content = "Based on the UK property data..."
gen_ai.usage.prompt_tokens = 1234
gen_ai.usage.completion_tokens = 567
gen_ai.usage.total_tokens = 1801
```

### Viewing in HyperDX

1. Go to http://localhost:8080
2. **Search** → **Traces** tab
3. Filter: `ServiceName = python-rag-demo`
4. Click any trace to see span tree with attributes

---

## Generating Test Data

### Run Demo Queries

```bash
# Automatic demo (3 questions, then starts TruLens dashboard)
docker compose up python-rag

# Or generate specific number of queries
docker compose exec python-rag python /scripts/generate_load.py -n 10
```

### Interactive Mode

```bash
docker compose exec -it python-rag python -c "
import sys; sys.path.insert(0, '/app')
from main import create_app

pipeline, tru_app, _ = create_app()
print('Interactive mode. Type quit to exit.\n')

while True:
    q = input('Question: ').strip()
    if q.lower() in ('quit', 'exit', 'q'):
        break
    if not q:
        continue
    with tru_app:
        response = pipeline.query(q)
    print(f'\nAnswer: {response}\n')
"
```

---

## SQL Analytics (HyperDX)

Use these queries in HyperDX SQL editor or connect directly to ClickHouse:

### Token Usage by Service
```sql
SELECT
    ServiceName,
    count() AS requests,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.prompt_tokens'])) AS input_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.completion_tokens'])) AS output_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 1 HOUR
  AND SpanAttributes['gen_ai.system'] != ''
GROUP BY ServiceName
```

### Cost Estimation (Claude Pricing)
```sql
SELECT
    toDate(Timestamp) AS date,
    count() AS requests,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.prompt_tokens'])) AS input_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.completion_tokens'])) AS output_tokens,
    -- Claude Sonnet: $3/1M input, $15/1M output
    round((input_tokens * 3.0 + output_tokens * 15.0) / 1000000, 4) AS estimated_cost_usd
FROM otel_traces
WHERE SpanAttributes['gen_ai.system'] = 'anthropic'
GROUP BY date
ORDER BY date DESC
```

See `queries/` directory for more examples.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | **Required.** Your Anthropic API key |
| `CLICKSTACK_API_KEY` | - | **Required.** From HyperDX Team Settings |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model for RAG pipeline |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model for TruLens evals (cheaper) |
| `TEMPERATURE` | `0.7` | LLM temperature |

---

## File Structure

```
├── python-rag/                  # Python RAG application
│   ├── main.py                  # Entry point - demo + dashboard
│   ├── instrumentation.py       # OpenTelemetry/OpenLLMetry setup
│   ├── rag_pipeline.py          # LangChain RAG pipeline
│   ├── trulens_config.py        # TruLens feedback functions
│   ├── mcp_client.py            # ClickHouse MCP client
│   └── requirements.txt         # Python dependencies
│
├── queries/                     # SQL analytics for HyperDX
│   ├── token_usage.sql
│   ├── cost_estimation.sql
│   ├── latency_analysis.sql
│   └── error_analysis.sql
│
├── scripts/                     # Utility scripts
│   ├── generate_load.py         # Generate test queries
│   ├── validate.py              # Validate deployment
│   └── setup.sh                 # Setup helper
│
├── docs/
│   └── QUICKSTART.md            # Detailed walkthrough
│
├── Dockerfile.rag               # Python RAG container
├── docker-compose.yaml          # All services
└── .env.example                 # Environment template
```

---

## Troubleshooting

### TruLens dashboard not loading (port 8501)?
```bash
docker logs python-rag 2>&1 | tail -20
# Should see "Dashboard started at http://localhost:8501"
```

### Traces not appearing in HyperDX?
```bash
# Check CLICKSTACK_API_KEY is set
docker compose exec python-rag env | grep CLICKSTACK

# Check network connectivity
docker compose exec python-rag curl -s http://clickstack:4318
```

### Container keeps exiting?
```bash
# Check for errors
docker logs python-rag

# Rebuild from scratch
docker compose build python-rag --no-cache
```

### TruLens evaluations not running?
```bash
# Check ANTHROPIC_API_KEY is set (TruLens uses it for evals)
docker compose exec python-rag env | grep ANTHROPIC
```

---

## Stopping Everything

```bash
# Stop services
docker compose down

# Stop ClickStack
docker stop clickstack && docker rm clickstack

# Clean up volumes
docker compose down -v
rm -rf data-node meili_data logs mcp-logs uploads images
```
