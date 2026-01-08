# LLM Observability with ClickStack, OpenTelemetry, and MCP

A demo showing how to instrument LibreChat and ClickHouse MCP Server with OpenTelemetry, sending traces, logs, and metrics to ClickStack (HyperDX).

Based on: https://clickhouse.com/blog/llm-observability-clickstack-mcp

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   LibreChat     │────▶│  ClickHouse MCP  │◀────│   Python RAG    │
│  (Port 3080)    │     │   (Port 8001)    │     │  (Port 8002)    │
└────────┬────────┘     └────────┬─────────┘     └────────┬────────┘
         │                       │                        │
         │  OTel Traces          │  OTel Traces           │ OTel + TruLens
         ▼                       ▼                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      ClickStack / HyperDX                            │
│                   UI: 8080 | OTLP: 4317/4318                        │
└──────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Docker & Docker Compose
- Anthropic API Key (get one at https://console.anthropic.com/)

## Quick Start

### 1. Start ClickStack

```bash
docker run -p 8080:8080 -p 4317:4317 -p 4318:4318 docker.hyperdx.io/hyperdx/hyperdx-all-in-one
```

### 2. Get API Key

Open http://localhost:8080, register an account, then go to **Team Settings** to copy your **Ingestion API Key**.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and add your keys:
- `CLICKSTACK_API_KEY` - from step 2
- `ANTHROPIC_API_KEY` - your Anthropic API key

Generate the required secrets:
```bash
# Generate and add these to your .env file
echo "CREDS_KEY=$(openssl rand -hex 32)"
echo "CREDS_IV=$(openssl rand -hex 16)"
echo "JWT_SECRET=$(openssl rand -hex 32)"
echo "JWT_REFRESH_SECRET=$(openssl rand -hex 32)"
```

### 4. Start the Demo

```bash
docker compose up
```

### 5. Access Applications

| Service | URL |
|---------|-----|
| LibreChat | http://localhost:3080 |
| HyperDX (Observability) | http://localhost:8080 |

## Using the Demo

1. Open LibreChat at http://localhost:3080
2. Register an account
3. Select **Claude** as your model
4. Enable the **clickhouse-playground** MCP server
5. Ask questions about the data!

### Sample Prompts

- "What datasets are available?"
- "How does USD compare to GBP over time?"
- "Which contributor has made the most commits to the ClickHouse repository?"
- "Show me the top 10 most popular GitHub repos by stars"

## Viewing Traces

Open HyperDX at http://localhost:8080 to see:
- **Traces**: Full request traces across LibreChat and MCP Server
- **Logs**: Structured JSON logs from both services
- **Metrics**: Prometheus metrics from LibreChat

## Connecting to Your Own ClickHouse

Edit `.env` to change the ClickHouse connection:

```bash
CLICKHOUSE_HOST=your-instance.clickhouse.cloud
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your-password
```

## Stopping the Demo

```bash
docker compose down
```

To also remove data volumes:

```bash
docker compose down -v
rm -rf data-node meili_data logs mcp-logs uploads images
```

---

## Python RAG Demo with TruLens & OpenLLMetry

In addition to LibreChat, this repo includes a Python RAG application that demonstrates:
- **OpenLLMetry** - Automatic capture of prompts, completions, and token usage
- **TruLens** - LLM quality evaluation (relevance, coherence scores)

> **Detailed guide**: See [docs/QUICKSTART.md](docs/QUICKSTART.md) for full walkthrough

### Quick Start

```bash
# 1. Ensure ClickStack is running and connected
docker network connect clickhouse-llm-observability_default clickstack

# 2. Build and start the demo
docker compose build python-rag
docker compose up -d python-rag

# 3. Watch the demo run
docker logs -f python-rag
```

### Generate More Data

```bash
docker compose exec python-rag python /scripts/generate_load.py -n 10
```

### Access Points

| Service | URL | Description |
|---------|-----|-------------|
| TruLens Dashboard | http://localhost:8501 | LLM evaluation scores |
| HyperDX | http://localhost:8080 | Traces, tokens, latency |
| LibreChat | http://localhost:3080 | Chat UI with MCP |

### What Gets Captured

| Data | Tool | Where to See |
|------|------|--------------|
| Prompts & Completions | OpenLLMetry | HyperDX → Traces |
| Token Usage | OpenLLMetry | HyperDX → Span attributes |
| Answer Relevance | TruLens | TruLens Dashboard |
| Coherence Score | TruLens | TruLens Dashboard |

### Sample SQL Queries

See `queries/` directory for ClickHouse analytics:
- `token_usage.sql` - Token counts by service
- `cost_estimation.sql` - Estimated API costs
- `latency_analysis.sql` - P50/P95/P99 latencies
- `error_analysis.sql` - Error rates over time
