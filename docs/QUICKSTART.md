# Python RAG Demo with TruLens & OpenLLMetry

A complete LLM observability demo showing how to instrument a RAG pipeline with:
- **OpenLLMetry**: Automatic tracing of prompts, completions, and token usage
- **TruLens**: LLM quality evaluation (relevance, coherence scores)
- **ClickStack/HyperDX**: Unified observability backend

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│                     Python RAG Application                      │
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│  │   Question  │───▶│  Analysis   │───▶│  Response   │        │
│  │   Input     │    │  Chain      │    │  Chain      │        │
│  └─────────────┘    │ (Claude)    │    │ (Claude)    │        │
│                     └──────┬──────┘    └──────┬──────┘        │
│                            │                  │                │
│                     ┌──────▼──────┐           │                │
│                     │ MCP Client  │           │                │
│                     │ (ClickHouse)│           │                │
│                     └─────────────┘           │                │
│                                               │                │
│  ════════════════════════════════════════════════════════════ │
│                    INSTRUMENTATION LAYER                       │
│  ════════════════════════════════════════════════════════════ │
│                                                                 │
│  ┌─────────────────────┐    ┌─────────────────────┐           │
│  │    OpenLLMetry      │    │      TruLens        │           │
│  │  ─────────────────  │    │  ─────────────────  │           │
│  │  • Prompts          │    │  • Relevance Score  │           │
│  │  • Completions      │    │  • Coherence Score  │           │
│  │  • Token counts     │    │  • Chain-of-thought │           │
│  │  • Latency          │    │    reasoning        │           │
│  └──────────┬──────────┘    └──────────┬──────────┘           │
└─────────────┼───────────────────────────┼──────────────────────┘
              │ OTLP                       │ SQLite + OTEL
              ▼                           ▼
┌─────────────────────────┐    ┌─────────────────────────┐
│   ClickStack / HyperDX  │    │   TruLens Dashboard     │
│   http://localhost:8080 │    │   http://localhost:8501 │
│   ─────────────────────  │    │   ─────────────────────  │
│   • Trace visualization │    │   • Eval leaderboard    │
│   • Token analytics     │    │   • Feedback details    │
│   • Latency metrics     │    │   • Record browser      │
│   • Error tracking      │    │   • App comparison      │
└─────────────────────────┘    └─────────────────────────┘
```

---

## How the Demo Works

### Step 1: Instrumentation Setup (`instrumentation.py`)

```python
# MUST be called before importing LangChain
from instrumentation import setup_instrumentation
setup_instrumentation()
```

This initializes:
- OpenTelemetry tracer with OTLP exporter → ClickStack
- LangChain auto-instrumentation via OpenLLMetry
- Captures all LLM calls automatically

### Step 2: RAG Pipeline (`rag_pipeline.py`)

The pipeline has two LangChain chains:

1. **Analysis Chain**: Identifies which ClickHouse database to query
   ```
   Question → Claude → "Use uk_price_paid database for London property data"
   ```

2. **Response Chain**: Generates the final answer
   ```
   Question + Analysis + Context → Claude → Final Answer
   ```

### Step 3: TruLens Wrapper (`trulens_config.py`)

Wraps the pipeline with evaluation:

```python
@instrument
def query(self, question: str) -> str:
    context = self.retrieve(question)      # Tracked
    return self.generate(question, context) # Tracked
```

**Feedback Functions**:
- **Answer Relevance**: Does the answer address the question? (0-1)
- **Coherence**: Is the response well-structured? (0-1)

Uses Claude Haiku for cost-efficient evaluation.

### Step 4: Execution (`main.py`)

```python
pipeline, tru_app, session = create_app()

with tru_app as recording:
    response = pipeline.query("What are the most expensive areas in London?")
# TruLens evaluates the response asynchronously
```

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Anthropic API key

### 1. Start ClickStack (Observability Backend)

```bash
docker run -d --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  docker.hyperdx.io/hyperdx/hyperdx-all-in-one

# Wait for ready
until curl -s http://localhost:8080 > /dev/null; do sleep 2; done
```

### 2. Get ClickStack API Key

1. Open http://localhost:8080
2. Register an account
3. Go to **Team Settings** → copy **Ingestion API Key**

### 3. Configure Environment

```bash
cp .env.example .env

# Edit .env and set:
# ANTHROPIC_API_KEY=sk-ant-...
# CLICKSTACK_API_KEY=<from step 2>
```

### 4. Connect Networks & Build

```bash
# Connect ClickStack to compose network
docker network connect clickhouse-llm-observability_default clickstack

# Build the RAG app
docker compose build python-rag
```

### 5. Run the Demo

```bash
# Start the demo (runs 3 questions, then starts TruLens dashboard)
docker compose up -d python-rag

# Watch the logs
docker logs -f python-rag
```

### 6. View Results

| UI | URL | What to See |
|----|-----|-------------|
| **TruLens Dashboard** | http://localhost:8501 | Evaluation scores, feedback details |
| **HyperDX** | http://localhost:8080 | Traces, token usage, latency |

---

## Generating More Data

Run additional queries to populate the dashboards:

```bash
docker compose exec python-rag python -c "
import sys; sys.path.insert(0, '/app')
from main import create_app

QUESTIONS = [
    'What are the most expensive areas for property in London?',
    'How has GitHub activity changed over the past year?',
    'What are the busiest airports based on flight data?',
    'What programming languages are most discussed on Stack Overflow?',
    'Which UK cities have the highest property price growth?',
]

pipeline, tru_app, _ = create_app()
for i, q in enumerate(QUESTIONS):
    print(f'[{i+1}/{len(QUESTIONS)}] {q[:50]}...')
    with tru_app:
        pipeline.query(q)
    print('  Done')
print('Complete!')
"
```

---

## What to Look For

### In TruLens Dashboard (http://localhost:8501)

1. **Leaderboard Tab**
   - See aggregated scores across all runs
   - Compare different app versions

2. **Evaluations Tab**
   - Individual feedback results
   - Chain-of-thought reasoning for each score

3. **Records Tab**
   - Browse each query/response pair
   - See input, output, and evaluation details

### In HyperDX (http://localhost:8080)

1. **Search → Traces**
   - Filter by `ServiceName = python-rag-demo`
   - Click a trace to see the full span tree

2. **Span Attributes** (click any span)
   - `gen_ai.prompt` - The prompt sent to Claude
   - `gen_ai.completion` - Claude's response
   - `gen_ai.usage.prompt_tokens` - Input tokens
   - `gen_ai.usage.completion_tokens` - Output tokens

3. **SQL Queries** (see `queries/` folder)
   - Token usage aggregation
   - Latency percentiles
   - Cost estimation

---

## File Structure

```
python-rag/
├── main.py              # Entry point - runs demo + dashboard
├── instrumentation.py   # OpenTelemetry + OpenLLMetry setup
├── rag_pipeline.py      # LangChain RAG pipeline
├── trulens_config.py    # TruLens feedback functions
├── mcp_client.py        # ClickHouse MCP client
└── run_dashboard.py     # TruLens dashboard starter

scripts/
├── generate_load.py     # Generate queries for dashboards
└── validate.py          # Validate deployment

queries/
├── token_usage.sql      # Token analytics
├── cost_estimation.sql  # Cost calculation
├── latency_analysis.sql # Performance metrics
└── error_analysis.sql   # Error tracking
```

---

## Configuration Options

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ANTHROPIC_API_KEY` | - | Required. Your Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model for RAG pipeline |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model for evaluations (cheaper) |
| `TEMPERATURE` | `0.7` | LLM temperature |
| `CLICKSTACK_API_KEY` | - | Required. From HyperDX Team Settings |

---

## Troubleshooting

**TruLens dashboard not loading?**
```bash
docker logs python-rag 2>&1 | grep -i error
```

**Traces not appearing in HyperDX?**
```bash
# Check API key is set
docker compose exec python-rag env | grep CLICKSTACK

# Check network connectivity
docker compose exec python-rag curl -I http://clickstack:4318
```

**Container keeps exiting?**
```bash
# Check logs for errors
docker logs python-rag

# Rebuild if needed
docker compose build python-rag --no-cache
```
