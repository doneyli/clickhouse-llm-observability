# LLM Observability with ClickStack, TruLens, and OpenLLMetry

A comprehensive demo for LLM observability showing how to monitor, trace, and evaluate LLM applications in production.

Based on: https://clickhouse.com/blog/llm-observability-clickstack-mcp

---

## Why LLM Observability?

Traditional application monitoring tracks requests, errors, and latency. But LLM applications have unique challenges:

| Challenge | Why It Matters |
|-----------|----------------|
| **Non-deterministic outputs** | Same prompt can produce different responses |
| **Quality is subjective** | "Good" output is hard to measure automatically |
| **Cost scales with usage** | Token usage directly impacts your bill |
| **Latency varies widely** | Response time depends on output length |
| **Debugging is hard** | What prompt led to that bad response? |

**LLM Observability** solves this by capturing:
1. **What happened** - Prompts, completions, tokens, latency (OpenLLMetry)
2. **How good was it** - Quality scores, relevance, accuracy (TruLens)

---

## Understanding the Observability Stack

This demo uses three complementary technologies:

### OpenLLMetry (Operational Observability)

[OpenLLMetry](https://github.com/traceloop/openllmetry) is an open-source project that auto-instruments LLM frameworks (LangChain, OpenAI, Anthropic, etc.) using OpenTelemetry.

**What it captures:**
```
┌─────────────────────────────────────────────────────────┐
│                   LLM API Call                          │
├─────────────────────────────────────────────────────────┤
│  gen_ai.prompt          → "What is ClickHouse?"         │
│  gen_ai.completion      → "ClickHouse is a fast..."     │
│  gen_ai.usage.input_tokens   → 12                       │
│  gen_ai.usage.output_tokens  → 156                      │
│  gen_ai.request.model   → "claude-sonnet-4-20250514"    │
│  duration_ms            → 1847                          │
└─────────────────────────────────────────────────────────┘
```

**Use cases:**
- Track token usage and costs
- Monitor latency percentiles (p50, p95, p99)
- Debug specific requests by viewing full prompts/completions
- Alert on errors or anomalies

### TruLens (Quality Evaluation)

[TruLens](https://www.trulens.org/) evaluates LLM output quality using "LLM-as-a-Judge" - a smaller, cheaper LLM scores your outputs.

**What it evaluates:**
```
┌─────────────────────────────────────────────────────────┐
│                   Quality Scores (0.0 - 1.0)            │
├─────────────────────────────────────────────────────────┤
│  Answer Relevance  → Does the answer address the question? │
│  Coherence         → Is the response well-structured?      │
│  Groundedness      → Is it supported by retrieved context? │
│  Context Relevance → Was the right context retrieved?      │
└─────────────────────────────────────────────────────────┘
```

**Use cases:**
- Catch quality regressions before users complain
- Compare model versions objectively
- Identify which types of questions perform poorly
- Validate RAG retrieval quality

### ClickStack/HyperDX (Unified Backend)

[ClickStack](https://github.com/hyperdxio/hyperdx) (powered by HyperDX) is a self-hosted observability platform with ClickHouse backend.

**What it provides:**
- Trace visualization with span trees
- Log aggregation and search
- Custom dashboards and alerts
- SQL-based analytics on observability data

---

## How They Work Together

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Your LLM Application                                  │
│                  (text-to-sql or vector-rag demo)                           │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  │ Every LLM call
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OpenLLMetry Auto-Instrumentation                     │
│                        (LangchainInstrumentor)                               │
│                                                                              │
│   Captures: prompts, completions, tokens, latency, model name               │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
          ┌────────────────────────┴────────────────────────┐
          │                                                 │
          ▼                                                 ▼
┌───────────────────────────┐                 ┌───────────────────────────┐
│   Main LLM Calls          │                 │   Judge LLM Calls         │
│   (Claude Sonnet)         │                 │   (Claude Haiku)          │
│                           │                 │                           │
│   Your app's responses    │                 │   TruLens evaluations     │
└───────────┬───────────────┘                 └───────────┬───────────────┘
            │                                             │
            │              OTLP Protocol                  │
            └──────────────────┬──────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ClickStack / HyperDX                                 │
│                        http://localhost:8080                                 │
│                                                                              │
│   • All traces (main LLM + judge LLM)    • Token analytics                  │
│   • Latency percentiles                   • Cost estimation                  │
│   • Error tracking                        • Custom dashboards                │
└─────────────────────────────────────────────────────────────────────────────┘

                               +

┌─────────────────────────────────────────────────────────────────────────────┐
│                         TruLens Dashboard                                    │
│                        http://localhost:8501                                 │
│                                                                              │
│   • Quality scores per record             • Judge reasoning (chain-of-thought)│
│   • App leaderboard                       • Feedback history                 │
│   • Regression detection                  • Export for analysis              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key insight:** TruLens judge calls are ALSO captured by OpenLLMetry. This means:
- HyperDX shows **all** LLM calls (both your app and the evaluations)
- You can filter by model to see just evaluation calls
- You get full cost/latency visibility into your evaluation pipeline

---

## Two Demo Applications

| Demo | Description | Use Case |
|------|-------------|----------|
| **Text-to-SQL** | Natural language → SQL queries against ClickHouse | Structured data Q&A |
| **Vector RAG** | Proper RAG with embeddings + vector similarity search | Unstructured document Q&A |

### Text-to-SQL Demo (`text-to-sql/`)
- Converts natural language questions to SQL queries
- Queries ClickHouse databases (UK property, GitHub, flights, etc.)
- Uses MCP (Model Context Protocol) for database access
- **Not RAG** - no vectors, no embeddings, no semantic retrieval

### Vector RAG Demo (`vector-rag/`)
- **Proper RAG architecture** with all components:
  - Document chunking and indexing
  - Vector embeddings (sentence-transformers)
  - ChromaDB vector store
  - Semantic similarity retrieval
  - LLM generation from retrieved context
- Includes **Groundedness** evaluation (is the answer supported by context?)

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Anthropic API key

### Step 1: Start ClickStack (Observability Backend)

```bash
docker run -d --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  docker.hyperdx.io/hyperdx/hyperdx-all-in-one

# Wait for ready
until curl -s http://localhost:8080 > /dev/null; do sleep 2; done
```

### Step 2: Get API Key

1. Open http://localhost:8080
2. Register an account
3. Go to **Team Settings** → copy **Ingestion API Key**

### Step 3: Configure Environment

```bash
cp .env.example .env
# Edit .env and set:
# ANTHROPIC_API_KEY=sk-ant-...
# CLICKSTACK_API_KEY=<from step 2>
```

### Step 4: Connect Networks

```bash
docker network create clickhouse-llm-observability_default 2>/dev/null || true
docker network connect clickhouse-llm-observability_default clickstack
```

### Step 5: Run the Demos

```bash
# Build and run both demos + unified TruLens dashboard
docker compose build text-to-sql vector-rag trulens-dashboard
docker compose up text-to-sql vector-rag trulens-dashboard
```

Or run individually:

```bash
# Text-to-SQL only
docker compose up text-to-sql trulens-dashboard

# Vector RAG only
docker compose up vector-rag trulens-dashboard
```

---

## Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| **HyperDX** | http://localhost:8080 | Traces, logs, token usage, latency |
| **TruLens Dashboard** | http://localhost:8501 | Quality scores, judge reasoning |
| **Text-to-SQL API** | http://localhost:8002 | Demo application |
| **Vector RAG API** | http://localhost:8003 | Demo application |

---

## Exploring the Data

### In HyperDX (Operational Data)

1. Go to http://localhost:8080 → **Search** → **Traces**
2. Filter by service: `ServiceName = text-to-sql-demo`
3. Click any trace to see:
   - Full prompt text (`gen_ai.prompt`)
   - Complete response (`gen_ai.completion`)
   - Token counts (`gen_ai.usage.*`)
   - Model used (`gen_ai.request.model`)
   - Latency breakdown

**Pro tip:** Filter `gen_ai.request.model = claude-3-5-haiku-20241022` to see only TruLens judge calls.

### In TruLens Dashboard (Quality Data)

1. Go to http://localhost:8501
2. **Leaderboard** tab → Compare apps by average scores
3. **Records** tab → View individual queries
4. Click a record → Click feedback badge → See judge's reasoning

---

## TruLens Evaluations

### Text-to-SQL Evaluations
| Metric | Description |
|--------|-------------|
| **Answer Relevance** | Does the answer address the question? |
| **Coherence** | Is the response well-structured? |

### Vector RAG Evaluations
| Metric | Description |
|--------|-------------|
| **Answer Relevance** | Does the answer address the question? |
| **Groundedness** | Is the answer supported by retrieved context? |
| **Context Relevance** | Is the retrieved context relevant to the question? |

### How LLM-as-a-Judge Works

TruLens uses a smaller, cheaper LLM (Claude Haiku by default) to evaluate outputs:

```
┌─────────────────────────────────────────────────────────────────┐
│  Your App's Output                                               │
│  ─────────────────                                               │
│  Question: "What is ClickHouse?"                                 │
│  Answer: "ClickHouse is a column-oriented database..."           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Judge LLM (Claude Haiku) Evaluation                             │
│  ───────────────────────────────────                             │
│  Prompt: "Rate how relevant this answer is to the question..."   │
│                                                                  │
│  Response:                                                       │
│  {                                                               │
│    "score": 0.92,                                                │
│    "reasoning": "The answer directly addresses what ClickHouse   │
│                  is and provides accurate technical details..."  │
│  }                                                               │
└─────────────────────────────────────────────────────────────────┘
```

### Viewing Judge Reasoning (Chain-of-Thought)

TruLens captures the judge's reasoning for each evaluation:

1. Open TruLens Dashboard at http://localhost:8501
2. Navigate to **Records** tab
3. Click on any record to expand it
4. Click on a **feedback badge** (e.g., "Answer Relevance: 0.85")
5. The judge's **chain-of-thought explanation** appears in the expanded view

### Where to Find Judge Model Info

| Location | What You'll See |
|----------|-----------------|
| **Config** | `TRULENS_MODEL` env var in docker-compose.yaml (default: `claude-3-5-haiku-20241022`) |
| **Code** | `trulens_config.py` - `ChatAnthropic(model=config.model)` |
| **HyperDX** | Filter traces by `gen_ai.request.model` to see all judge LLM calls |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | **Required.** Your Anthropic API key |
| `CLICKSTACK_API_KEY` | - | **Required.** From HyperDX Team Settings |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model for generation |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model for TruLens evals (cheaper) |
| `TEMPERATURE` | `0.7` | LLM temperature |

---

## Generating Test Data

### Text-to-SQL Demo
```bash
docker compose exec text-to-sql python /scripts/generate_load.py -n 10
```

### Vector RAG Demo
```bash
docker compose exec vector-rag python -c "
import sys; sys.path.insert(0, '/app')
from main import create_app

QUESTIONS = [
    'What is ClickHouse?',
    'How does RAG work?',
    'What is TruLens?',
]

pipeline, tru_app, _ = create_app()
for q in QUESTIONS:
    print(f'Query: {q}')
    with tru_app:
        pipeline.query(q)
    print('Done')
"
```

---

## File Structure

```
├── text-to-sql/                # Text-to-SQL application
│   ├── main.py                 # Entry point
│   ├── instrumentation.py      # OpenLLMetry setup
│   ├── sql_pipeline.py         # LangChain SQL pipeline
│   ├── trulens_config.py       # TruLens feedback functions
│   ├── mcp_client.py           # ClickHouse MCP client
│   └── requirements.txt
│
├── vector-rag/                 # Vector RAG application
│   ├── main.py                 # Entry point
│   ├── instrumentation.py      # OpenLLMetry setup
│   ├── rag_pipeline.py         # RAG with ChromaDB
│   ├── trulens_config.py       # TruLens with groundedness
│   ├── documents.py            # Sample knowledge base
│   └── requirements.txt
│
├── Dockerfile.text-to-sql
├── Dockerfile.vector-rag
├── Dockerfile.trulens-dashboard
├── docker-compose.yaml
└── .env.example
```

---

## Troubleshooting

### Traces not appearing in HyperDX?
```bash
# Check API key is set
docker compose exec text-to-sql env | grep CLICKSTACK

# Check network connectivity
docker compose exec text-to-sql curl -s http://clickstack:4318
```

### TruLens dashboard empty?
```bash
# Check shared database has data
docker compose exec trulens-dashboard ls -la /trulens-data/

# Check app logs
docker logs text-to-sql 2>&1 | tail -20
```

### Container keeps exiting?
```bash
# Check logs for errors
docker logs text-to-sql

# Rebuild from scratch
docker compose build text-to-sql --no-cache
```

---

## Stopping Everything

```bash
# Stop demo services
docker compose down

# Stop ClickStack
docker stop clickstack && docker rm clickstack

# Clean up volumes (removes TruLens data)
docker compose down -v
```

---

## Learn More

- [OpenLLMetry Documentation](https://github.com/traceloop/openllmetry)
- [TruLens Documentation](https://www.trulens.org/docs/)
- [HyperDX/ClickStack](https://github.com/hyperdxio/hyperdx)
- [OpenTelemetry](https://opentelemetry.io/)
- [ClickHouse Blog: LLM Observability](https://clickhouse.com/blog/llm-observability-clickstack-mcp)
