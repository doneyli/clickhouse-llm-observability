# Integrate Langfuse as Alternative LLM Evaluation Platform

## Summary

Add Langfuse as an alternative LLM observability and evaluation platform alongside TruLens. This demonstrates the power of ClickHouse as a unified observability backend by showing the same LLM conversations evaluated in both platforms, both storing data in ClickHouse.

## Motivation

- **Showcase ClickHouse versatility**: Langfuse natively uses ClickHouse for OLAP workloads, demonstrating ClickHouse as the ideal backend for LLM observability
- **Offer platform comparison**: Users can compare TruLens vs Langfuse evaluation approaches side-by-side
- **Production readiness**: Langfuse is widely adopted in production LLM deployments

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    UPDATED ARCHITECTURE WITH LANGFUSE                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│   │ Text-to-SQL │     │ Vector RAG  │     │  LibreChat  │                   │
│   │    Demo     │     │    Demo     │     │   (Chat)    │                   │
│   └──────┬──────┘     └──────┬──────┘     └──────┬──────┘                   │
│          │                   │                   │                          │
│          │ Dual Instrumentation                  │                          │
│          │ • OpenLLMetry → ClickStack            │ librechat-exporter       │
│          │ • Langfuse SDK → Langfuse             │                          │
│          │                   │                   │                          │
│          └───────────┬───────┴───────────────────┘                          │
│                      │                                                       │
│          ┌───────────┴───────────┐                                          │
│          │                       │                                          │
│          ▼                       ▼                                          │
│   ┌─────────────────┐     ┌─────────────────┐                               │
│   │   ClickStack    │     │    Langfuse     │                               │
│   │   (HyperDX)     │     │    (Web UI)     │                               │
│   │ localhost:8080  │     │ localhost:3000  │                               │
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
│   │ • Relevance     │     │ • Custom Scores │                               │
│   │ • Coherence     │     │ • LLM-as-Judge  │                               │
│   └─────────────────┘     └─────────────────┘                               │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Technical Specification

### Langfuse Requirements

Based on [Langfuse self-hosting docs](https://langfuse.com/self-hosting):

| Component | Purpose | Notes |
|-----------|---------|-------|
| **langfuse-web** | Web UI and API | Port 3000 |
| **langfuse-worker** | Async event processing | Background jobs |
| **PostgreSQL** | Transactional metadata | New container (required - ClickHouse can't replace OLTP workloads) |
| **Redis** | Caching and queuing | New container (required - ClickHouse can't provide in-memory caching) |
| **MinIO** | S3-compatible blob storage | New container (required for event persistence) |
| **ClickHouse** | OLAP storage for traces | **Reuse ClickStack's ClickHouse** |

> **Note**: PostgreSQL, Redis, and MinIO are lightweight Alpine-based containers. The heavy component (ClickHouse) is shared with ClickStack.

### Optional Profile Design

All Langfuse services will be placed under a `langfuse` profile, making them **opt-in**:

```bash
# Default demo (TruLens only)
docker compose up -d text-to-sql vector-rag trulens-dashboard

# With Langfuse enabled
docker compose --profile langfuse up -d
```

This keeps the base demo simple while allowing Langfuse to be enabled when needed for comparison.

### ClickHouse Integration

Langfuse can connect to an external ClickHouse instance. Key environment variables:

```yaml
# Langfuse ClickHouse Configuration
CLICKHOUSE_URL: http://clickstack:8123          # HTTP endpoint
CLICKHOUSE_MIGRATION_URL: clickhouse://clickstack:9000  # TCP for migrations
CLICKHOUSE_USER: api
CLICKHOUSE_PASSWORD: api
CLICKHOUSE_DB: langfuse                         # Separate database from HyperDX
CLICKHOUSE_CLUSTER_ENABLED: "false"             # Single-node ClickStack
```

**Important**: Langfuse creates its own tables in ClickHouse. It does NOT share tables with HyperDX. Both systems can coexist in the same ClickHouse instance using different databases.

### Dual Instrumentation Strategy

The demo apps will send traces to BOTH platforms:

```python
# text-to-sql/instrumentation.py (updated)

# Existing OpenLLMetry for ClickStack/HyperDX
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
LangchainInstrumentor().instrument(tracer_provider=tracer_provider)

# NEW: Langfuse callback for Langfuse
from langfuse.langchain import CallbackHandler as LangfuseHandler

langfuse_handler = LangfuseHandler(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000")
)

# Usage in chain invocation:
chain.invoke(input, config={"callbacks": [langfuse_handler]})
```

### Evaluation Parity

Both TruLens and Langfuse will evaluate the same metrics:

| Metric | TruLens Implementation | Langfuse Implementation |
|--------|----------------------|------------------------|
| **Answer Relevance** | `provider.relevance_with_cot_reasons` | Custom score via SDK |
| **Coherence** | `provider.coherence_with_cot_reasons` | Custom score via SDK |

Langfuse scoring example:
```python
from langfuse import Langfuse

langfuse = Langfuse()

# After getting TruLens-style evaluation result
langfuse.score(
    trace_id=trace_id,
    name="relevance",
    value=0.95,
    data_type="NUMERIC",
    comment="Answer directly addresses the question"
)
```

## Implementation Plan

### Phase 1: Infrastructure Setup

#### 1.1 Add Langfuse services to docker-compose.yaml

```yaml
# =============================================================================
# Langfuse - LLM Observability Platform (Alternative to TruLens)
# =============================================================================

# PostgreSQL for Langfuse metadata
langfuse-postgres:
  image: postgres:16-alpine
  container_name: langfuse-postgres
  restart: unless-stopped
  profiles:
    - langfuse
  environment:
    POSTGRES_USER: langfuse
    POSTGRES_PASSWORD: langfuse
    POSTGRES_DB: langfuse
  volumes:
    - langfuse-postgres-data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U langfuse"]
    interval: 5s
    timeout: 5s
    retries: 5

# Redis for Langfuse caching/queuing
langfuse-redis:
  image: redis:7-alpine
  container_name: langfuse-redis
  restart: unless-stopped
  profiles:
    - langfuse
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 5s
    retries: 5

# MinIO for S3-compatible blob storage
langfuse-minio:
  image: minio/minio:latest
  container_name: langfuse-minio
  restart: unless-stopped
  profiles:
    - langfuse
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: langfuse
    MINIO_ROOT_PASSWORD: langfuse123
  volumes:
    - langfuse-minio-data:/data
  healthcheck:
    test: ["CMD", "mc", "ready", "local"]
    interval: 5s
    timeout: 5s
    retries: 5

# MinIO bucket initialization
langfuse-minio-init:
  image: minio/mc:latest
  container_name: langfuse-minio-init
  profiles:
    - langfuse
  depends_on:
    langfuse-minio:
      condition: service_healthy
  entrypoint: >
    /bin/sh -c "
    mc alias set minio http://langfuse-minio:9000 langfuse langfuse123;
    mc mb minio/langfuse-events --ignore-existing;
    mc mb minio/langfuse-media --ignore-existing;
    exit 0;
    "

# Langfuse Worker (background processing)
langfuse-worker:
  image: langfuse/langfuse:latest
  container_name: langfuse-worker
  restart: unless-stopped
  profiles:
    - langfuse
  depends_on:
    langfuse-postgres:
      condition: service_healthy
    langfuse-redis:
      condition: service_healthy
    langfuse-minio-init:
      condition: service_completed_successfully
  environment:
    - NODE_ENV=production
    - DATABASE_URL=postgresql://langfuse:langfuse@langfuse-postgres:5432/langfuse
    - REDIS_CONNECTION_STRING=redis://langfuse-redis:6379
    - CLICKHOUSE_URL=http://clickstack:8123
    - CLICKHOUSE_MIGRATION_URL=clickhouse://clickstack:9000
    - CLICKHOUSE_USER=api
    - CLICKHOUSE_PASSWORD=api
    - CLICKHOUSE_DB=langfuse
    - CLICKHOUSE_CLUSTER_ENABLED=false
    - LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse-events
    - LANGFUSE_S3_MEDIA_UPLOAD_BUCKET=langfuse-media
    - LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=http://langfuse-minio:9000
    - LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT=http://langfuse-minio:9000
    - LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=langfuse
    - LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=langfuse123
    - LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID=langfuse
    - LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=langfuse123
    - LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true
    - LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE=true
    - NEXTAUTH_SECRET=your-nextauth-secret-min-32-chars-here
    - SALT=your-salt-min-32-characters-here-too
    - ENCRYPTION_KEY=0000000000000000000000000000000000000000000000000000000000000000
  extra_hosts:
    - "host.docker.internal:host-gateway"

# Langfuse Web (UI and API)
langfuse-web:
  image: langfuse/langfuse:latest
  container_name: langfuse-web
  restart: unless-stopped
  profiles:
    - langfuse
  ports:
    - "3000:3000"
  depends_on:
    langfuse-worker:
      condition: service_started
  environment:
    - NODE_ENV=production
    - DATABASE_URL=postgresql://langfuse:langfuse@langfuse-postgres:5432/langfuse
    - REDIS_CONNECTION_STRING=redis://langfuse-redis:6379
    - CLICKHOUSE_URL=http://clickstack:8123
    - CLICKHOUSE_MIGRATION_URL=clickhouse://clickstack:9000
    - CLICKHOUSE_USER=api
    - CLICKHOUSE_PASSWORD=api
    - CLICKHOUSE_DB=langfuse
    - CLICKHOUSE_CLUSTER_ENABLED=false
    - LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse-events
    - LANGFUSE_S3_MEDIA_UPLOAD_BUCKET=langfuse-media
    - LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=http://langfuse-minio:9000
    - LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT=http://langfuse-minio:9000
    - LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=langfuse
    - LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=langfuse123
    - LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID=langfuse
    - LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=langfuse123
    - LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true
    - LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE=true
    - NEXTAUTH_URL=http://localhost:3000
    - NEXTAUTH_SECRET=your-nextauth-secret-min-32-chars-here
    - SALT=your-salt-min-32-characters-here-too
    - ENCRYPTION_KEY=0000000000000000000000000000000000000000000000000000000000000000
  extra_hosts:
    - "host.docker.internal:host-gateway"

# Add to volumes section:
volumes:
  langfuse-postgres-data:
  langfuse-minio-data:
```

#### 1.2 Create Langfuse database in ClickStack

Before starting Langfuse, create the database in ClickStack's ClickHouse:

```bash
docker exec clickstack clickhouse-client --user api --password api \
  --query "CREATE DATABASE IF NOT EXISTS langfuse"
```

### Phase 2: Update Demo Apps for Dual Instrumentation

#### 2.1 Update requirements.txt

Add to `text-to-sql/requirements.txt` and `vector-rag/requirements.txt`:

```
langfuse>=2.0.0
```

#### 2.2 Create langfuse_config.py

Create `text-to-sql/langfuse_config.py`:

```python
"""Langfuse Integration Configuration"""

import os
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

def get_langfuse_client():
    """Get Langfuse client for direct API access."""
    return Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    )

def get_langfuse_handler(
    user_id: str = None,
    session_id: str = None,
    tags: list = None
):
    """Get Langfuse callback handler for LangChain."""
    handler = CallbackHandler(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    )

    # Set optional metadata
    if user_id:
        handler.user_id = user_id
    if session_id:
        handler.session_id = session_id
    if tags:
        handler.tags = tags

    return handler

def score_trace(
    trace_id: str,
    relevance_score: float = None,
    coherence_score: float = None,
    comment: str = None
):
    """Add evaluation scores to a Langfuse trace."""
    langfuse = get_langfuse_client()

    if relevance_score is not None:
        langfuse.score(
            trace_id=trace_id,
            name="relevance",
            value=relevance_score,
            data_type="NUMERIC",
            comment=comment
        )

    if coherence_score is not None:
        langfuse.score(
            trace_id=trace_id,
            name="coherence",
            value=coherence_score,
            data_type="NUMERIC",
            comment=comment
        )

    langfuse.flush()
```

#### 2.3 Update main.py to use dual instrumentation

Update `text-to-sql/main.py`:

```python
# Add imports
from langfuse_config import get_langfuse_handler

# In query endpoint, add Langfuse callback
@app.post("/query")
async def query(request: QueryRequest):
    langfuse_handler = get_langfuse_handler(
        tags=["text-to-sql-demo"]
    )

    # Pass handler to chain invocation
    result = pipeline.query(
        request.question,
        callbacks=[langfuse_handler]  # Sends to Langfuse
    )

    # OpenLLMetry automatically sends to ClickStack
    return {"answer": result}
```

#### 2.4 Update docker-compose environment variables

Add to `text-to-sql` and `vector-rag` services:

```yaml
environment:
  # Existing vars...
  - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
  - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
  - LANGFUSE_HOST=http://langfuse-web:3000
```

### Phase 3: Create Langfuse Evaluator Service

Create a service that mirrors `trace-evaluator` but uses Langfuse's scoring API.

#### 3.1 Create langfuse-evaluator/main.py

```python
"""
Langfuse Evaluator - Async quality evaluation using Langfuse

Queries traces from Langfuse and runs LLM-as-judge evaluations,
storing scores back in Langfuse.
"""

import os
import argparse
from langfuse import Langfuse
from langchain_anthropic import ChatAnthropic
from trulens.providers.langchain import Langchain

def get_langfuse_traces(hours: int = 24, limit: int = 100):
    """Fetch recent traces from Langfuse."""
    langfuse = Langfuse()

    # Langfuse SDK provides trace listing
    traces = langfuse.get_traces(
        limit=limit,
        order_by="timestamp",
        order="desc"
    )

    return traces.data

def evaluate_trace(trace, provider):
    """Run TruLens-style evaluation on a Langfuse trace."""
    langfuse = Langfuse()

    # Extract input/output from trace
    input_text = trace.input
    output_text = trace.output

    if not input_text or not output_text:
        return None

    # Run evaluations (same logic as TruLens)
    relevance = provider.relevance_with_cot_reasons(input_text, output_text)
    coherence = provider.coherence_with_cot_reasons(output_text)

    # Store scores in Langfuse
    langfuse.score(
        trace_id=trace.id,
        name="relevance",
        value=relevance[0],  # Score value
        data_type="NUMERIC",
        comment=relevance[1].get("reason", "")  # CoT reasoning
    )

    langfuse.score(
        trace_id=trace.id,
        name="coherence",
        value=coherence[0],
        data_type="NUMERIC",
        comment=coherence[1].get("reason", "")
    )

    langfuse.flush()

    return {
        "trace_id": trace.id,
        "relevance": relevance[0],
        "coherence": coherence[0]
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    # Initialize provider (same as TruLens)
    model = os.getenv("TRULENS_MODEL", "claude-3-5-haiku-20241022")
    llm = ChatAnthropic(model=model, temperature=0.0)
    provider = Langchain(chain=llm)

    traces = get_langfuse_traces(hours=args.hours, limit=args.limit)

    for trace in traces:
        result = evaluate_trace(trace, provider)
        if result:
            print(f"Evaluated {result['trace_id']}: "
                  f"relevance={result['relevance']:.2f}, "
                  f"coherence={result['coherence']:.2f}")

if __name__ == "__main__":
    main()
```

### Phase 4: Update Documentation

#### 4.1 Update README.md

Add new section:

```markdown
## Langfuse Integration (Alternative Evaluation Platform)

This demo supports both TruLens and Langfuse for LLM evaluation, both using ClickHouse as the backend.

### Starting Langfuse

```bash
# 1. Create Langfuse database in ClickStack
docker exec clickstack clickhouse-client --user api --password api \
  --query "CREATE DATABASE IF NOT EXISTS langfuse"

# 2. Start Langfuse services
docker compose up -d langfuse-postgres langfuse-redis langfuse-minio langfuse-web

# 3. Wait for Langfuse to be ready
until curl -s http://localhost:3000 > /dev/null; do sleep 2; done

# 4. Get API keys from Langfuse UI
# Open http://localhost:3000 → Sign up → Project Settings → API Keys

# 5. Add to .env
echo "LANGFUSE_PUBLIC_KEY=pk-lf-..." >> .env
echo "LANGFUSE_SECRET_KEY=sk-lf-..." >> .env
```

### Viewing Evaluations

| Platform | URL | What You'll See |
|----------|-----|-----------------|
| **TruLens** | http://localhost:8501 | Relevance, Coherence scores + judge reasoning |
| **Langfuse** | http://localhost:3000 | Traces, scores, LLM calls visualization |
| **HyperDX** | http://localhost:8080 | Raw OTEL traces, gen_ai.* attributes |

### Service Comparison

| Feature | TruLens | Langfuse |
|---------|---------|----------|
| Storage Backend | SQLite (local) | ClickHouse (shared) |
| Trace Visualization | Basic table | Rich timeline |
| Score Types | Pre-built feedbacks | Custom scores |
| Dashboard | Streamlit | Native web UI |
| Production Use | Evaluation-focused | Full observability |
```

#### 4.2 Update .env.example

```bash
# Langfuse Configuration (optional - for dual evaluation demo)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://localhost:3000
```

### Phase 5: Testing and Validation

#### 5.1 Validation script

Create `scripts/validate-langfuse.sh`:

```bash
#!/bin/bash

echo "Validating Langfuse integration..."

# Check services
echo "1. Checking Langfuse services..."
curl -s http://localhost:3000/api/public/health | jq .

# Check ClickHouse database
echo "2. Checking Langfuse tables in ClickHouse..."
docker exec clickstack clickhouse-client --user api --password api \
  --query "SHOW TABLES FROM langfuse"

# Check traces exist
echo "3. Checking for traces..."
curl -s -H "Authorization: Bearer $LANGFUSE_SECRET_KEY" \
  http://localhost:3000/api/public/traces | jq '.data | length'

echo "Validation complete!"
```

## Files to Create/Modify

### New Files
- [ ] `text-to-sql/langfuse_config.py` - Langfuse SDK wrapper
- [ ] `vector-rag/langfuse_config.py` - Langfuse SDK wrapper
- [ ] `langfuse-evaluator/main.py` - Async evaluator for Langfuse
- [ ] `langfuse-evaluator/requirements.txt` - Dependencies
- [ ] `Dockerfile.langfuse-evaluator` - Container definition
- [ ] `scripts/validate-langfuse.sh` - Validation script
- [ ] `docs/LANGFUSE_INTEGRATION.md` - Detailed integration guide

### Modified Files
- [ ] `docker-compose.yaml` - Add Langfuse services
- [ ] `.env.example` - Add Langfuse env vars
- [ ] `text-to-sql/requirements.txt` - Add langfuse package
- [ ] `vector-rag/requirements.txt` - Add langfuse package
- [ ] `text-to-sql/main.py` - Add dual instrumentation
- [ ] `vector-rag/main.py` - Add dual instrumentation
- [ ] `README.md` - Document Langfuse integration

## Demo Flow (Updated)

1. **Phase 1**: Start ClickStack (unchanged)
2. **Phase 2**: Start Langfuse stack (new)
3. **Phase 3**: Run demo apps (now with dual instrumentation)
4. **Phase 4**: Generate traffic (unchanged)
5. **Phase 5**: View in HyperDX (operational traces)
6. **Phase 6**: View in TruLens (quality scores)
7. **Phase 7**: View in Langfuse (traces + scores + visualization) **NEW**

## Resource Requirements

Additional containers for Langfuse:
- PostgreSQL: ~256MB RAM
- Redis: ~64MB RAM
- MinIO: ~128MB RAM
- langfuse-web: ~512MB RAM
- langfuse-worker: ~512MB RAM

**Total additional**: ~1.5GB RAM

## Acceptance Criteria

- [ ] Langfuse services start successfully with ClickStack's ClickHouse
- [ ] Demo apps send traces to both HyperDX and Langfuse
- [ ] Same evaluation metrics (relevance, coherence) available in both platforms
- [ ] Scores visible in Langfuse UI alongside traces
- [ ] Documentation updated with side-by-side comparison
- [ ] Validation script passes

## References

- [Langfuse Self-Hosting](https://langfuse.com/self-hosting)
- [Langfuse Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose)
- [Langfuse ClickHouse Configuration](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse)
- [Langfuse Python SDK](https://langfuse.com/docs/sdk/python)
- [Langfuse LangChain Integration](https://langfuse.com/docs/integrations/langchain/tracing)
