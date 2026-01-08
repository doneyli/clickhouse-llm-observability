# LLM Observability with ClickStack, OpenTelemetry, and MCP

A demo showing how to instrument LibreChat and ClickHouse MCP Server with OpenTelemetry, sending traces, logs, and metrics to ClickStack (HyperDX).

Based on: https://clickhouse.com/blog/llm-observability-clickstack-mcp

## Architecture

```
┌─────────────────┐     ┌──────────────────┐
│   LibreChat     │────▶│  ClickHouse MCP  │
│  (Port 3080)    │     │   (Port 8001)    │
└────────┬────────┘     └────────┬─────────┘
         │                       │
         │  OTel Traces          │  OTel Traces
         ▼                       ▼
┌─────────────────────────────────────────────┐
│           ClickStack / HyperDX              │
│  UI: 8080 | OTLP: 4317/4318                │
└─────────────────────────────────────────────┘
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
