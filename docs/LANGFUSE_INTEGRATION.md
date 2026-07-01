# Langfuse Integration Guide

This guide covers the Langfuse integration for LLM observability. Langfuse is the primary evaluation platform for this demo, using ClickHouse as the backend storage.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       LANGFUSE OBSERVABILITY ARCHITECTURE                     │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│   │ Text-to-SQL │     │ Vector RAG  │     │  LibreChat  │                   │
│   │    Demo     │     │    Demo     │     │             │                   │
│   └──────┬──────┘     └──────┬──────┘     └──────┬──────┘                   │
│          │                   │                   │                           │
│          │  Langfuse SDK (callbacks / native integration)                    │
│          │                   │                   │                           │
│          └───────────┬───────┴───────────────────┘                          │
│                      │                                                       │
│                      ▼                                                       │
│            ┌─────────────────┐                                              │
│            │    Langfuse     │                                              │
│            │    (Web UI)     │                                              │
│            │ localhost:3001  │                                              │
│            └────────┬────────┘                                              │
│                     │                                                       │
│                     ▼                                                       │
│            ┌───────────────────────┐                                        │
│            │      ClickHouse       │                                        │
│            │   (Analytics Backend) │                                        │
│            │                       │                                        │
│            │ • langfuse_* tables   │                                        │
│            └───────────────────────┘                                        │
│                                                                              │
│                        ┌─────────────────┐                                  │
│                        │ Langfuse Native │                                  │
│                        │ LLM-as-a-Judge  │                                  │
│                        │ Evaluators      │                                  │
│                        │                 │                                  │
│                        │ • Hallucination │                                  │
│                        │ • Helpfulness   │                                  │
│                        │ • Toxicity      │                                  │
│                        └─────────────────┘                                  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Infrastructure Components

Langfuse requires several supporting services:

| Component | Image | Purpose |
|-----------|-------|---------|
| `langfuse-postgres` | `postgres:16-alpine` | Transactional metadata storage |
| `langfuse-redis` | `redis:7-alpine` | Caching and job queuing |
| `langfuse-minio` | `minio/minio:latest` | S3-compatible blob storage |
| `langfuse-worker` | `langfuse/langfuse-worker:3` | Background event processing |
| `langfuse-web` | `langfuse/langfuse:latest` | Web UI and API (port 3001) |

**Important**: Langfuse uses ClickHouse for OLAP storage (analytics backend).

---

## Quick Start

### 1. Create Langfuse Database in ClickHouse

```bash
docker compose exec clickhouse clickhouse-client \
  --query "CREATE DATABASE IF NOT EXISTS langfuse"
```

### 2. Start Langfuse Services

```bash
docker compose --profile langfuse up -d
```

### 3. Wait for Langfuse to Initialize

```bash
until curl -s http://localhost:3001 > /dev/null 2>&1; do
  sleep 5
  echo "Waiting for Langfuse..."
done
echo "Langfuse ready!"
```

### 4. Create Account and Get API Keys

1. Open http://localhost:3001
2. Sign up for an account
3. Go to **Settings** → **API Keys**
4. Copy the public and secret keys

### 5. Configure Environment

Add to your `.env` file:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### 6. Restart Demo Apps

```bash
docker compose restart text-to-sql vector-rag
```

---

## Setting Up Native Evaluators (LLM-as-a-Judge)

Langfuse provides built-in LLM-as-a-Judge evaluators that run **automatically** on new data. This eliminates the need for a separate evaluation service.

> **In this demo, setup is automatic (self-hosted mode).** `./setup.sh` provisions
> three observation-level judges (Relevance, Correctness, Hallucination) over the
> test-scenario traffic plus an experiment judge, via
> `scripts/seed-llm-judge-evaluators.sh`, and seeds the default evaluation model.
> Langfuse now recommends **observation-level** evaluators for live data —
> trace-level ones are marked "Legacy" in the UI
> ([migration guide](https://langfuse.com/faq/all/llm-as-a-judge-migration)).
> For deterministic checks (regex, policies, formats) see
> [Code Evaluators](CODE_EVALUATORS.md), provisioned automatically as well.
>
> The UI steps below are for **cloud mode** or for adding your own custom evaluators.

### 1. Open Langfuse Evaluations

1. Go to http://localhost:3001
2. Navigate to **Evaluations** → **LLM-as-a-Judge**
3. Click **+ New Evaluator**

### 2. Choose an Evaluator Template

Langfuse provides several built-in templates:

| Template | Purpose |
|----------|---------|
| **Hallucination** | Detects fabricated or unsupported information |
| **Helpfulness** | Measures overall response quality and usefulness |
| **Context-Relevance** | Checks if the response appropriately uses provided context |
| **Toxicity** | Identifies harmful or inappropriate content |

You can also create custom evaluators with your own prompts.

### 3. Configure the Evaluator

When creating an evaluator, configure:

| Setting | Recommendation |
|---------|----------------|
| **Target** | **Observations** (recommended; trace-level is Legacy) |
| **Model** | Select your LLM provider (e.g., Claude, GPT-4) |
| **Sampling** | 100% for demo/development, lower (10-25%) for production |
| **Filters** | Observation type (e.g., `GENERATION`) plus trace name or tags (e.g., `test-scenario`) |

### 4. Save and Enable

Once saved, the evaluator runs automatically on new observations that match your filters; scores attach to the matching observation in the trace tree.

### 5. View Evaluation Results

1. Open http://localhost:3001
2. Navigate to **Traces**
3. Click on a trace to see evaluation scores
4. Scores appear in the **Scores** panel

### Filtering Test Scenarios

Test scenarios export traces with tags for easy filtering:
- Tag: `test-scenario` - All test scenario traces
- Tag: `hallucination-test` - Hallucination test cases
- Tag: `relevance-test` - Relevance test cases
- Tag: `coherence-test` - Coherence test cases

The auto-provisioned judges already filter on these tags (each judge watches its failure category plus `control` for contrast).

### Why Native Evaluators?

| Aspect | Custom Evaluator | Native Evaluators |
|--------|------------------|-------------------|
| Setup | Requires service deployment | UI configuration only |
| Execution | Manual trigger | Automatic on new traces |
| Templates | Custom prompts only | Built-in + custom |
| Maintenance | You maintain code | Langfuse maintains |
| Cost | Your API credits | Langfuse credits (or BYO key) |

**Note**: Langfuse still has no public API for creating evaluators ([GitHub Discussion #8241](https://github.com/orgs/langfuse/discussions/8241)). In self-hosted mode this demo provisions them headlessly anyway (`scripts/seed-llm-judge-evaluators.sh` and `scripts/seed-code-evaluators.sh` seed the same database rows the UI creates); in cloud mode use the UI.

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | - | Your Langfuse public key |
| `LANGFUSE_SECRET_KEY` | - | Your Langfuse secret key |
| `LANGFUSE_HOST` | `http://localhost:3001` | Langfuse API endpoint (host) |
| `LANGFUSE_PORT` | `3001` | Port for Langfuse web UI |

### Docker Compose Profile

All Langfuse services use the `langfuse` profile:

```bash
# Start only Langfuse
docker compose --profile langfuse up -d

# Stop only Langfuse
docker compose --profile langfuse down

# View Langfuse logs
docker compose --profile langfuse logs -f
```

---

## Troubleshooting

### Langfuse Web UI Not Starting

**Symptom**: `langfuse-web` keeps restarting.

**Check logs**:
```bash
docker logs langfuse-web
```

**Common issue**: "Region is missing" error.

**Solution**: Ensure S3 region configuration is set:
```yaml
environment:
  - LANGFUSE_S3_EVENT_UPLOAD_REGION=us-east-1
  - LANGFUSE_S3_MEDIA_UPLOAD_REGION=us-east-1
```

### Queue Not Processing (Traces Not Appearing)

**Symptom**: Events stuck in Redis queue, traces not visible in UI.

**Check queue**:
```bash
docker exec langfuse-redis redis-cli llen bull:ingestion-processing:wait
```

**Solution**: Ensure the worker uses the dedicated image:
```yaml
langfuse-worker:
  image: langfuse/langfuse-worker:3  # NOT langfuse/langfuse:latest
```

### No Traces in Langfuse

**Symptom**: Demo apps running but no traces appear.

**Check**:
1. Verify API keys are set in `.env`
2. Restart demo apps after setting keys
3. Check demo app logs for Langfuse errors:
   ```bash
   docker logs text-to-sql 2>&1 | grep -i langfuse
   ```

### Validation Script

Run the validation script to check your setup:

```bash
./scripts/validate-langfuse.sh
```

---

## Key Implementation Details

### SDK v3 Compatibility

Langfuse SDK v3 uses environment variables for authentication. The CallbackHandler doesn't accept constructor parameters:

```python
# Correct (SDK v3)
from langfuse.langchain import CallbackHandler
handler = CallbackHandler()  # Reads from environment

# Incorrect (old SDK)
handler = CallbackHandler(
    public_key="...",  # Not supported in v3
    secret_key="..."
)
```

### REST API Usage

The evaluator uses the REST API for trace fetching and score storage because SDK v3 doesn't expose these methods:

```python
import httpx

# Fetch traces
client = httpx.Client(
    base_url="http://localhost:3001",
    auth=(public_key, secret_key)
)
response = client.get("/api/public/traces?limit=100")
traces = response.json()["data"]

# Store scores
client.post("/api/public/scores", json={
    "traceId": trace_id,
    "name": "relevance",
    "value": 0.85,
    "dataType": "NUMERIC",
    "comment": "Answer addresses most of the question"
})
```

### Worker Image Requirement

Langfuse requires a dedicated worker image for async processing:

| Component | Image | Purpose |
|-----------|-------|---------|
| Web server | `langfuse/langfuse:latest` | UI and API |
| Worker | `langfuse/langfuse-worker:3` | Queue processing |

Using the web image for the worker will cause events to queue indefinitely.

---

## Langfuse Features

| Feature | Description |
|---------|-------------|
| **Storage** | ClickHouse (shared backend) |
| **Trace Visualization** | Rich timeline with spans |
| **Score Types** | Custom numeric scores |
| **Dashboard** | Native web UI |
| **Production Readiness** | Full observability platform |

### When to Use Langfuse

- You want rich trace visualization
- You need production-grade observability
- You want to share dashboards with teams
- You're already using ClickHouse
- You need native LibreChat integration

---

## Reading LibreChat Traces

LibreChat traces differ from the Python demo apps, and it helps to know why before you
present or debug them.

**How LibreChat instruments.** The Python demos (Text-to-SQL, Vector RAG, Agentic RAG)
call the Langfuse SDK directly and build clean, intentionally named spans. LibreChat
instead traces through its native `@librechat/agents` LangChain/LangGraph callbacks.
That produces a deeper, machine-named tree — `AgentRun → LangGraph → agent_<id> →
agent=agent_<id> → RunnableSequence → prompt / generation → RunnableLambda`. The
`Runnable*` spans and agent-ID node names are LangChain/LangGraph internals, not a bug.
The `librechat` tag is added via a small `sed` patch in `librechat/entrypoint.sh`.

**Trace richness tracks tool use.** This is the key thing to understand:

| Prompt | Result |
|--------|--------|
| Tool-using ("show me my slowest traces") | 45–60 observations: `TOOL` spans (`tool_batch`, `tools=agent_<id>`) + repeated reasoning loops. Rich. |
| Generic ("what is ClickHouse?", "hi") | ~10 observations: one `LibreChatAnthropic` generation in LangGraph scaffolding. Thin but complete. |

The trace itself is always complete (full I/O, token counts, cost); a thin trace just
reflects a one-shot answer with no tool calls. Demo with tool-triggering prompts.

**The graph view.** Langfuse renders the LangGraph node graph at the bottom of the
trace. A thin trace draws a trivial `__start__ → agent → __end__`; a tool-using trace
draws the agent↔tools loop. Node labels are LibreChat's internal agent IDs — opaque
but harmless.

**Title generation is disabled.** `librechat.yaml` sets `endpoints.all.titleConvo:
false`. With titling on, every chat also emits a separate `TitleRun` trace (LibreChat
naming the thread) that clutters the trace list. Disabling keeps every trace a real
`AgentRun`. To re-enable, set it to `true` and filter **Trace Name = AgentRun** in
Langfuse to hide the `TitleRun` noise.

## MCP Server Integration (LibreChat Agents)

LibreChat agents can interact with Langfuse prompts directly through the Langfuse MCP server.

### Available Tools

Once configured, agents have access to:

| Tool | Description |
|------|-------------|
| `getPrompt` | Fetch a prompt by name (with optional label/version) |
| `listPrompts` | List all prompts in the project |
| `createTextPrompt` | Create a text prompt with `{{variable}}` syntax |
| `createChatPrompt` | Create OpenAI-style chat prompts |
| `updatePromptLabels` | Manage labels (production, staging, etc.) |

### Setup

#### 1. Generate Auth Token

```bash
# Source your .env file
source .env

# Generate base64 token (use -n to avoid trailing newline)
echo -n "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64
```

#### 2. Add Token to `.env`

```bash
LANGFUSE_MCP_AUTH_TOKEN=<your-base64-token>
```

#### 3. Restart LibreChat API

```bash
docker compose restart api
```

### Testing the MCP Server

#### Test Endpoint Directly

```bash
TOKEN="<your-base64-token>"
curl -X POST "http://localhost:3001/api/public/mcp" \
  -H "Authorization: Basic ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

#### Verify in LibreChat UI

1. Open LibreChat at http://localhost:3080
2. Create a new Agent conversation
3. Check tools dropdown - "langfuse-prompts" should appear
4. Enable the tool and test with prompts like:
   - "List all prompts in Langfuse"
   - "Get the prompt named 'X' with the production label"
   - "Create a new text prompt called 'greeting' with content 'Hello {{name}}!'"

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Tools not appearing | Restart api service after config changes |
| Auth errors | Ensure token uses `-n` flag with echo (no trailing newline) |
| Connection refused | Verify Langfuse is running (`docker compose --profile langfuse up -d`) |
| "streamable-http not supported" | Update LibreChat to latest version (requires May 2025+ build) |

### Configuration Notes

- Uses `streamable-http` transport (not SSE)
- Connects to self-hosted Langfuse at `langfuse-web:3000` (Docker internal network)
- For Langfuse Cloud, use `https://cloud.langfuse.com/api/public/mcp`

---

## Files Reference

| File | Purpose |
|------|---------|
| `text-to-sql/langfuse_config.py` | Langfuse SDK wrapper |
| `vector-rag/langfuse_config.py` | Langfuse SDK wrapper |
| `test-scenarios/export_test_scenarios.py` | Test scenarios with evaluation tags |
| `scripts/validate-langfuse.sh` | Setup validation |

---

## Further Reading

- [Langfuse Documentation](https://langfuse.com/docs)
- [Langfuse Self-Hosting Guide](https://langfuse.com/self-hosting)
- [Langfuse Python SDK](https://langfuse.com/docs/sdk/python)
- [Langfuse REST API](https://api.reference.langfuse.com/)
- [LangChain Integration](https://langfuse.com/docs/integrations/langchain/tracing)
