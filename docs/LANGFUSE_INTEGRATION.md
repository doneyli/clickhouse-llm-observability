# Langfuse Integration Guide

This guide covers the Langfuse integration for LLM observability, providing an alternative evaluation platform alongside TruLens. Both platforms share ClickHouse as the backend storage.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    DUAL INSTRUMENTATION ARCHITECTURE                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────┐     ┌─────────────┐                                       │
│   │ Text-to-SQL │     │ Vector RAG  │                                       │
│   │    Demo     │     │    Demo     │                                       │
│   └──────┬──────┘     └──────┬──────┘                                       │
│          │                   │                                              │
│          │ Dual Instrumentation                                             │
│          │ • OpenLLMetry → ClickStack (automatic)                           │
│          │ • Langfuse SDK → Langfuse (via callbacks)                        │
│          │                   │                                              │
│          └───────────┬───────┘                                              │
│                      │                                                       │
│          ┌───────────┴───────────┐                                          │
│          │                       │                                          │
│          ▼                       ▼                                          │
│   ┌─────────────────┐     ┌─────────────────┐                               │
│   │   ClickStack    │     │    Langfuse     │                               │
│   │   (HyperDX)     │     │    (Web UI)     │                               │
│   │ localhost:8080  │     │ localhost:3001  │                               │
│   └────────┬────────┘     └────────┬────────┘                               │
│            │                       │                                        │
│            │                       │                                        │
│            └───────────┬───────────┘                                        │
│                        │                                                    │
│                        ▼                                                    │
│            ┌───────────────────────┐                                        │
│            │      ClickHouse       │                                        │
│            │   (Shared Backend)    │                                        │
│            │                       │                                        │
│            │ • otel_traces (HyperDX)                                        │
│            │ • langfuse_* (Langfuse tables)                                 │
│            └───────────────────────┘                                        │
│                                                                              │
│   ┌─────────────────┐     ┌─────────────────┐                               │
│   │ TruLens         │     │ Langfuse        │                               │
│   │ Dashboard       │     │ Evaluations     │                               │
│   │ localhost:8501  │     │ (in Langfuse UI)│                               │
│   │                 │     │                 │                               │
│   │ • Relevance     │     │ • Relevance     │                               │
│   │ • Coherence     │     │ • Coherence     │                               │
│   └─────────────────┘     └─────────────────┘                               │
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

**Important**: Langfuse reuses ClickStack's ClickHouse instance for OLAP storage.

---

## Quick Start

### 1. Create Langfuse Database in ClickHouse

```bash
docker exec clickstack clickhouse-client --user api --password api \
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

## Running LLM-as-Judge Evaluations

The `langfuse-evaluator` service runs the same evaluation metrics as TruLens (Relevance, Coherence) but stores results in Langfuse.

### List Available Traces

```bash
docker compose --profile langfuse run --rm langfuse-evaluator --list
```

### Evaluate Recent Traces

```bash
docker compose --profile langfuse run --rm langfuse-evaluator --hours 24 --limit 50
```

### Force Re-evaluate (Skip Cache)

```bash
docker compose --profile langfuse run --rm langfuse-evaluator --force
```

### View Results

1. Open http://localhost:3001
2. Navigate to **Traces**
3. Click on a trace to see evaluation scores
4. Scores appear in the **Scores** panel with:
   - `relevance` - How well the answer addresses the question (0.0-1.0)
   - `coherence` - How well-structured and logical the answer is (0.0-1.0)

---

## Evaluation Metrics

Both TruLens and Langfuse evaluators use the same LLM-as-judge approach:

### Relevance Score

Evaluates how well the answer addresses the question.

| Score | Meaning |
|-------|---------|
| 1.0 | Answer directly and completely addresses the question |
| 0.7-0.9 | Answer mostly addresses the question with minor gaps |
| 0.4-0.6 | Answer partially addresses the question |
| 0.1-0.3 | Answer barely relates to the question |
| 0.0 | Answer is completely off-topic |

### Coherence Score

Evaluates the logical structure and clarity of the answer.

| Score | Meaning |
|-------|---------|
| 1.0 | Well-structured, logically organized, easy to follow |
| 0.7-0.9 | Mostly coherent with minor issues |
| 0.4-0.6 | Some coherence issues but understandable |
| 0.1-0.3 | Difficult to follow, poorly organized |
| 0.0 | Completely incoherent or self-contradictory |

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | - | Your Langfuse public key |
| `LANGFUSE_SECRET_KEY` | - | Your Langfuse secret key |
| `LANGFUSE_HOST` | `http://localhost:3001` | Langfuse API endpoint (host) |
| `LANGFUSE_PORT` | `3001` | Port for Langfuse web UI |
| `TRULENS_MODEL` | `claude-3-5-haiku-20241022` | Model used for evaluations |

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

### Evaluator Can't Connect to Langfuse

**Symptom**: "Connection refused" errors from evaluator.

**Solution**: When running from Docker, use the internal hostname:
```yaml
environment:
  - LANGFUSE_HOST=http://langfuse-web:3000  # Internal Docker network
```

When running locally:
```bash
export LANGFUSE_HOST=http://localhost:3001  # Host machine port
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

## Platform Comparison

| Feature | TruLens | Langfuse |
|---------|---------|----------|
| **Storage** | SQLite (local file) | ClickHouse (shared) |
| **Trace Visualization** | Basic table view | Rich timeline with spans |
| **Score Types** | Pre-built feedbacks | Custom numeric scores |
| **Dashboard** | Streamlit app | Native web UI |
| **Production Readiness** | Evaluation-focused | Full observability |
| **Setup Complexity** | Simple (single container) | Complex (5 containers) |
| **Resource Usage** | ~256MB | ~1.5GB |

### When to Use Each

**Use TruLens when**:
- You need quick, simple evaluation
- Running locally for development
- You want pre-built evaluation metrics
- Resource-constrained environment

**Use Langfuse when**:
- You want rich trace visualization
- You need production-grade observability
- You want to share dashboards with teams
- You're already using ClickHouse

---

## Files Reference

| File | Purpose |
|------|---------|
| `langfuse-evaluator/main.py` | LLM-as-judge evaluator |
| `langfuse-evaluator/requirements.txt` | Python dependencies |
| `Dockerfile.langfuse-evaluator` | Container definition |
| `text-to-sql/langfuse_config.py` | Langfuse SDK wrapper |
| `vector-rag/langfuse_config.py` | Langfuse SDK wrapper |
| `scripts/validate-langfuse.sh` | Setup validation |

---

## Further Reading

- [Langfuse Documentation](https://langfuse.com/docs)
- [Langfuse Self-Hosting Guide](https://langfuse.com/self-hosting)
- [Langfuse Python SDK](https://langfuse.com/docs/sdk/python)
- [Langfuse REST API](https://api.reference.langfuse.com/)
- [LangChain Integration](https://langfuse.com/docs/integrations/langchain/tracing)
