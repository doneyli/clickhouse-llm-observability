# LLM Observability with ClickStack, TruLens, and OpenLLMetry

A comprehensive demo for LLM observability showing:
- **OpenTelemetry/OpenLLMetry** - Trace prompts, completions, token usage, latency
- **TruLens** - Evaluate LLM output quality (relevance, groundedness, coherence)
- **ClickStack/HyperDX** - Unified observability UI with ClickHouse backend

Based on: https://clickhouse.com/blog/llm-observability-clickstack-mcp

---

## Two Demo Applications

This repo includes **two distinct demo applications** to showcase different LLM patterns:

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

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────────────┐
│   LibreChat     │────▶│  ClickHouse MCP  │◀────│     Text-to-SQL Demo        │
│  (Port 3080)    │     │   (Port 8001)    │     │      (Port 8002)            │
│                 │     │                  │     │  NL → SQL → ClickHouse      │
│  Chat UI with   │     │  SQL Playground  │     │  TruLens: 8501              │
│  Claude + MCP   │     │  35+ datasets    │     └─────────────────────────────┘
└────────┬────────┘     └────────┬─────────┘
         │                       │               ┌─────────────────────────────┐
         │                       │               │      Vector RAG Demo        │
         │                       │               │      (Port 8003)            │
         │                       │               │  Embeddings → ChromaDB      │
         │                       │               │  → Semantic Search → LLM    │
         │                       │               │  TruLens: 8502              │
         │                       │               └─────────────┬───────────────┘
         │ OTel Traces           │ OTel Traces                 │ OTel + TruLens
         ▼                       ▼                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         ClickStack / HyperDX                                 │
│                      UI: 8080 | OTLP: 4317/4318                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • Trace visualization    • Token analytics    • Latency percentiles        │
│  • Log aggregation        • Cost estimation    • Error tracking             │
└──────────────────────────────────────────────────────────────────────────────┘
```

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

### Step 5: Run a Demo

**Option A: Text-to-SQL Demo** (queries ClickHouse structured data)
```bash
docker compose build text-to-sql
docker compose up text-to-sql
```

**Option B: Vector RAG Demo** (proper RAG with embeddings)
```bash
docker compose build vector-rag
docker compose up vector-rag
```

---

## Access Points

| Service | URL | Description |
|---------|-----|-------------|
| **HyperDX** | http://localhost:8080 | Traces, logs, token usage, latency |
| **Text-to-SQL TruLens** | http://localhost:8501 | Evaluation scores for Text-to-SQL |
| **Vector RAG TruLens** | http://localhost:8502 | Evaluation scores for Vector RAG |
| **LibreChat** | http://localhost:3080 | Chat UI with Claude + MCP |

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

All evaluations use **LLM-as-a-Judge** with Claude Haiku for cost efficiency.

---

## What is OpenLLMetry?

[OpenLLMetry](https://github.com/traceloop/openllmetry) automatically instruments LLM frameworks to capture:

- **Prompts** - Full text sent to the model
- **Completions** - Model's response
- **Token counts** - Input/output/total tokens
- **Latency** - Time per operation
- **Model info** - Which model was called

### Viewing in HyperDX

1. Go to http://localhost:8080
2. **Search** → **Traces** tab
3. Filter: `ServiceName = text-to-sql-demo` or `ServiceName = vector-rag-demo`
4. Click any trace to see span tree with attributes

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
    'Explain vector embeddings',
    'What is OpenTelemetry?',
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

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | **Required.** Your Anthropic API key |
| `CLICKSTACK_API_KEY` | - | **Required.** From HyperDX Team Settings |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model for generation |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model for TruLens evals (cheaper) |
| `TEMPERATURE` | `0.7` | LLM temperature |

---

## File Structure

```
├── text-to-sql/                # Text-to-SQL application
│   ├── main.py                 # Entry point
│   ├── instrumentation.py      # OpenTelemetry setup
│   ├── sql_pipeline.py         # LangChain SQL pipeline
│   ├── trulens_config.py       # TruLens feedback functions
│   ├── mcp_client.py           # ClickHouse MCP client
│   └── requirements.txt        # Python dependencies
│
├── vector-rag/                 # Vector RAG application
│   ├── main.py                 # Entry point
│   ├── instrumentation.py      # OpenTelemetry setup
│   ├── rag_pipeline.py         # RAG with ChromaDB
│   ├── trulens_config.py       # TruLens with groundedness
│   ├── documents.py            # Sample knowledge base
│   └── requirements.txt        # Python dependencies
│
├── queries/                    # SQL analytics for HyperDX
│   ├── token_usage.sql
│   ├── cost_estimation.sql
│   └── latency_analysis.sql
│
├── scripts/                    # Utility scripts
│   └── generate_load.py        # Generate test queries
│
├── Dockerfile.text-to-sql      # Text-to-SQL container
├── Dockerfile.vector-rag       # Vector RAG container
├── docker-compose.yaml         # All services
└── .env.example                # Environment template
```

---

## Troubleshooting

### TruLens dashboard not loading?
```bash
docker logs text-to-sql 2>&1 | tail -20
# or
docker logs vector-rag 2>&1 | tail -20
```

### Traces not appearing in HyperDX?
```bash
# Check API key is set
docker compose exec text-to-sql env | grep CLICKSTACK

# Check network connectivity
docker compose exec text-to-sql curl -s http://clickstack:4318
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
# Stop services
docker compose down

# Stop ClickStack
docker stop clickstack && docker rm clickstack

# Clean up volumes
docker compose down -v
rm -rf data-node meili_data logs mcp-logs uploads images
```
