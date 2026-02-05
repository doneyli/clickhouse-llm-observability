# LLM Observability with ClickHouse

**A unified observability platform for AI and LLM applications, powered by ClickHouse.**

> **Stack already running?** [Jump to Quick Start](#quick-start) to generate traces in 2 minutes.
> **New to this demo?** Follow the [**Guided User Journey**](docs/USER_JOURNEY.md) for setup and walkthrough (~35 min).

---

## The Challenge: Why LLM Applications Need Different Observability

Building LLM applications is easy. Knowing if they're working well in production is hard.

**The core problem:** Traditional monitoring tells you *if* your app responded, but not *if the response was good*. When your LLM confidently returns a wrong answer, your metrics show a successful request.

| Traditional Apps | LLM Applications |
|------------------|------------------|
| Deterministic outputs | Non-deterministic outputs |
| Errors are obvious (exceptions, 500s) | "Wrong" answers look like valid responses |
| Cost is predictable | Cost scales unpredictably with token usage |
| Debugging = stack traces | Debugging = understanding full prompt/completion context |

**You need to capture, store, and analyze every prompt/completion pair—plus quality evaluations that tell you whether the output was actually good.** This means:

- **Large payloads**: Full prompts and completions can be 10-100KB per request
- **High cardinality**: Every interaction is unique (user queries, context, responses)
- **Real-time queries**: Debugging a production issue means searching through thousands of traces *now*
- **Quality scoring**: Running LLM-as-judge evaluations on historical data

This is an analytics workload, not a transactional one. And that's where ClickHouse comes in.

---

## Why ClickHouse?

### What is ClickHouse?

[ClickHouse](https://clickhouse.com/) is an open-source columnar database designed for real-time analytics. Unlike row-based databases (PostgreSQL, MySQL) that excel at transactional workloads, ClickHouse is optimized for:
- Ingesting millions of events per second
- Running analytical queries across billions of rows in milliseconds
- Compressing data efficiently (often 10-20x smaller than row stores)

It's the database behind Cloudflare's analytics, Uber's logging infrastructure, and eBay's observability platform.

### Why ClickHouse for LLM Observability?

LLM observability data has specific characteristics that make ClickHouse an ideal fit:

| Characteristic | Why It Matters | How ClickHouse Helps |
|----------------|----------------|----------------------|
| **Large text payloads** | Prompts/completions are 10-100KB each | Columnar compression excels at repetitive text (system prompts, templates) |
| **Append-only writes** | Traces are written once, never updated | ClickHouse is optimized for append-only ingestion |
| **Analytical queries** | "Show me all slow responses for model X" | Sub-second queries across billions of rows |
| **Time-series patterns** | Debugging means querying recent data | Native time-series optimizations and partitioning |
| **High cardinality** | Every trace has unique content | Handles high-cardinality data without index bloat |

### How ClickHouse Compares to Alternatives

| Capability | ClickHouse | PostgreSQL | Elasticsearch | Specialized Observability (Datadog, etc.) |
|------------|------------|------------|---------------|-------------------------------------------|
| **Query language** | SQL | SQL | Query DSL | Proprietary |
| **Analytical query speed** | Sub-second on billions | Slows at millions | Good, but resource-heavy | Good |
| **Text compression** | Excellent (columnar) | Moderate | Good | Varies |
| **Self-hosted option** | ✓ Yes | ✓ Yes | ✓ Yes | Limited/None |
| **Native OpenTelemetry** | ✓ Built-in OTEL schema | Requires setup | Requires setup | ✓ Built-in |
| **Unified traces/metrics/logs** | ✓ Single backend | Separate systems | Logs only | ✓ Built-in |
| **Scale without ops burden** | High | Moderate | High ops overhead | N/A (managed) |

**The bottom line:** ClickHouse gives you the analytical power of a data warehouse with the real-time query performance needed for production debugging—using SQL you already know.

---

## The Value Proposition

With ClickHouse as your centralized observability backend, you can:

1. **Instrument** - Automatically capture every LLM interaction with OpenTelemetry
2. **Observe** - Search, filter, and visualize traces in real-time
3. **Monitor** - Track token usage, costs, and latency across all your LLM apps
4. **Evaluate** - Run LLM-as-judge quality assessments on production data

**One database. Complete visibility. Production-grade LLM observability.**

---

## Quick Start

### Already Have the Stack Running?

**Just want to generate traces?** Run the demo scripts directly:

```bash
# Check if services are running
docker compose ps

# Generate Text-to-SQL traces (3 demo queries)
docker compose run --rm text-to-sql python main.py

# Generate Vector RAG traces (3 demo queries)
docker compose run --rm vector-rag python main.py

# Run interactive mode (type your own questions)
docker compose run --rm text-to-sql python main.py --interactive
docker compose run --rm vector-rag python main.py --interactive
```

**View your traces:**
- **HyperDX**: http://localhost:8080 (Traces, logs, dashboards)
- **Langfuse**: http://localhost:3001 (LLM traces, evaluations, prompt playground)

**Time:** 2-3 minutes | **Outcome:** Fresh traces in your observability stack

---

### First Time Setup?

If you haven't set up the observability stack yet, choose one of these paths:

- **[One-Command Setup](#one-command-setup-recommended)** - Fastest way to get everything running (~10 min)
- **[Guided User Journey](docs/USER_JOURNEY.md)** - Hands-on walkthrough with explanations (~35 min)
- **[Quickstart Guide](docs/QUICKSTART_GUIDE.md)** - Step-by-step manual setup (~15-30 min)

---

## Architecture

This solution integrates multiple open-source tools—all powered by ClickHouse:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            YOUR LLM APPLICATIONS                              │
│                                                                              │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│   │ Text-to-SQL │     │ Vector RAG  │     │  LibreChat  │                   │
│   │    Demo     │     │    Demo     │     │   (Chat)    │                   │
│   └──────┬──────┘     └──────┬──────┘     └──────┬──────┘                   │
│          │                   │                   │                          │
│    OpenLLMetry         OpenLLMetry         Native integrations              │
│    ├─→ OTLP            ├─→ OTLP            ├─→ OTLP (@hyperdx/node-otel)    │
│    └─→ Langfuse SDK    └─→ Langfuse SDK    └─→ Langfuse (native)            │
│          │                   │                   │                          │
│          └───────────────────┼───────────────────┘                          │
│                              │                                              │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │
         ┌─────────────────────┴─────────────────────┐
         │ OTLP (gRPC/HTTP)                          │ Langfuse SDK
         ▼                                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            OBSERVABILITY BACKENDS                            │
│                                                                              │
│   ┌────────────────────────────────┐    ┌────────────────────────────────┐  │
│   │   HyperDX / ClickStack         │    │    Langfuse                    │  │
│   │   localhost:8080               │    │    localhost:3001              │  │
│   │                                │    │                                │  │
│   │   • All traces via OTLP        │    │   • Real-time LLM tracing      │  │
│   │   • gen_ai.* semantic attrs    │    │   • Score visualization        │  │
│   │   • Dashboards, alerts         │    │   • Prompt playground          │  │
│   │   • Unified logs/metrics       │    │   • LLM-as-judge evaluation    │  │
│   │                                │    │                                │  │
│   │   Backend: ClickHouse          │    │   Backend: ClickHouse          │  │
│   └────────────────────────────────┘    └────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Component Overview

| Component | Purpose | Data Store |
|-----------|---------|------------|
| **OpenLLMetry** | Auto-instruments LLM frameworks (LangChain, Anthropic SDK) | → ClickHouse |
| **HyperDX/ClickStack** | Trace visualization, search, dashboards | ClickHouse |
| **Langfuse** | LLM observability with native LibreChat integration | ClickHouse |
| **LibreChat** | Chat interface with native Langfuse + OTLP tracing | MongoDB |

---

## What You'll Build

This demo sets up a complete LLM observability pipeline:

1. **Demo LLM Applications**
   - Text-to-SQL: Natural language queries against ClickHouse's public demo database
   - Vector RAG: Retrieval-augmented generation with embeddings

2. **Automatic Instrumentation**
   - Every LLM call captured with prompts, completions, token counts, and latency
   - Zero code changes required (OpenLLMetry auto-instrumentation)

3. **Trace Visualization**
   - Search and filter traces by model, service, or content
   - View the complete request/response flow
   - Build custom dashboards

4. **Quality Evaluation**
   - Langfuse native LLM-as-a-Judge evaluators (Hallucination, Helpfulness, etc.)
   - Automatic evaluation on new traces—no manual triggers needed
   - Historical analysis of quality trends

---

## Setup Options

Choose your path based on your goals:

### One-Command Setup (Recommended)

**Best for:** Getting the demo running quickly with minimal effort.

```bash
git clone https://github.com/doneyli/clickhouse-llm-observability.git
cd clickhouse-llm-observability
./setup.sh
```

The setup script handles everything automatically:
- Prerequisites check (Docker, Docker Compose)
- ClickStack startup and API key configuration
- Environment setup with auto-generated secrets
- Building and starting all services
- Running the demo

**Time:** ~10 minutes | **Outcome:** Full demo running with sample traces

---

### Guided User Journey (Recommended for First-Timers)

**Best for:** Experiencing the full demo hands-on, from querying data to viewing traces to evaluating quality.

[**Start the User Journey →**](docs/USER_JOURNEY.md)

What you'll experience:
1. Launch the demo stack
2. Ask questions via the Text-to-SQL API
3. Chat interactively with LibreChat (using the ClickHouse SQL Playground tool)
4. Explore your traces in HyperDX
5. Run quality evaluations with Langfuse

**Time:** ~35 minutes | **Outcome:** Complete hands-on experience with all components

---

### Quickstart Guide

**Best for:** Users who want step-by-step control over the setup process.

[**Read the Quickstart Guide →**](docs/QUICKSTART_GUIDE.md)

What you'll do:
1. Start ClickStack manually
2. Configure environment variables
3. Build and start services individually
4. Generate traces and view them in HyperDX
5. Run quality evaluations

**Time:** 15-30 minutes | **Outcome:** Full understanding of each component

---

## What You'll Accomplish

After completing the demo, you will have:

| Accomplishment | Benefit |
|----------------|---------|
| **Running observability stack** | Complete local environment for LLM monitoring |
| **Sample traces in ClickHouse** | Real data to explore and query |
| **Quality evaluation pipeline** | LLM-as-judge scoring on production traces |
| **Custom dashboards** | Visualizations for token usage, latency, costs |
| **Reusable patterns** | Code and configs you can adapt for your apps |

### Key Benefits

- **Visibility**: See exactly what your LLM apps are doing in production
- **Quality assurance**: Automated evaluation catches bad responses
- **Cost control**: Track token usage and optimize expensive operations
- **Debugging**: Full trace context when things go wrong
- **Compliance**: Audit trail of all LLM interactions

---

## Quick Commands Reference

### First-Time Setup

```bash
# Clone and enter directory
git clone https://github.com/doneyli/clickhouse-llm-observability.git
cd clickhouse-llm-observability

# Interactive setup (handles Langfuse account creation)
./scripts/setup.sh

# Populate with sample data
./scripts/seed-demo-data.sh
```

### Daily Use

```bash
# Start everything (if already set up)
docker compose --profile langfuse up -d

# Check which services are running
docker compose ps

# View logs
docker compose logs -f [service-name]

# Stop everything (preserves data)
docker compose --profile langfuse down
```

### Running Demos

```bash
# Generate traces with demo queries
docker compose run --rm text-to-sql python main.py
docker compose run --rm vector-rag python main.py

# Interactive mode - type your own questions
docker compose run --rm text-to-sql python main.py --interactive
docker compose run --rm vector-rag python main.py --interactive
```

### Evaluation

Langfuse provides native LLM-as-a-Judge evaluators that run automatically on new traces.

```bash
# Run test scenarios to generate evaluation test data
docker compose --profile tools run --rm test-scenarios

# View evaluation results in Langfuse UI
# http://localhost:3001 → Evaluations → LLM-as-a-Judge
```

**Configure evaluators in Langfuse UI:**
1. Go to http://localhost:3001 → **Evaluations** → **LLM-as-a-Judge**
2. Click **+ New Evaluator**
3. Choose a template (Hallucination, Helpfulness, etc.)
4. Filter by tag `test-scenario` to evaluate test data

### Reset & Maintenance

```bash
# Full reset (deletes all data, requires re-setup)
./scripts/reset.sh

# Re-seed demo data
./scripts/seed-demo-data.sh

# Validate Langfuse configuration
./scripts/validate-langfuse.sh
```

---

## Service Reference

| Service | URL | Purpose |
|---------|-----|---------|
| **HyperDX** | http://localhost:8080 | Traces, logs, metrics, dashboards |
| **Langfuse** | http://localhost:3001 | LLM traces, evaluations, prompt playground |
| **Text-to-SQL API** | http://localhost:8002 | Demo: Natural language → SQL |
| **Vector RAG API** | http://localhost:8003 | Demo: RAG with embeddings |
| **LibreChat** | http://localhost:3080 | Chat UI for LLM interaction |

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key |
| `CLICKSTACK_API_KEY` | Yes | HyperDX ingestion key |
| `CREDS_KEY` | Yes | LibreChat encryption key |
| `JWT_SECRET` | Yes | LibreChat JWT secret |
| `LANGFUSE_PUBLIC_KEY` | No | Enable Langfuse dual instrumentation |
| `LANGFUSE_SECRET_KEY` | No | Enable Langfuse dual instrumentation |

See [`.env.example`](.env.example) for the full configuration reference.

---

## Project Structure

```
├── setup.sh                    # One-click setup script
├── docker-compose.yaml         # Service orchestration
├── .env.example                # Environment template
├── librechat.yaml              # LibreChat configuration
├── otel-file-collector.yaml    # OpenTelemetry collector config
│
├── text-to-sql/                # Text-to-SQL demo app
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
│
├── vector-rag/                 # Vector RAG demo app
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
│
│
├── librechat-exporter/         # MongoDB → OTEL exporter
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
│
├── librechat/                  # LibreChat customizations
│   ├── Dockerfile.api          # API with OTEL instrumentation
│   └── nginx.conf              # Nginx reverse proxy config
│
├── mcp-clickhouse/             # ClickHouse MCP Server
│   └── Dockerfile
│
├── test-scenarios/             # Evaluation test scenarios
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
│
├── docs/                       # Documentation
│   ├── QUICKSTART_GUIDE.md
│   ├── EVALUATION_ARCHITECTURE.md
│   ├── EVALUATION_SCENARIOS.md
│   ├── LANGFUSE_INTEGRATION.md
│   └── hyperdx-dashboard-api.md
│
└── scripts/                    # Utility scripts
    ├── setup.sh                # First-run setup with Langfuse config
    ├── seed-demo-data.sh       # Populate demo with sample traces
    ├── reset.sh                # Full reset (destructive)
    ├── validate-langfuse.sh    # Validate Langfuse integration
    └── create-hyperdx-dashboard-mongo.sh
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [User Journey](docs/USER_JOURNEY.md) | Hands-on walkthrough of the complete demo |
| [Quickstart Guide](docs/QUICKSTART_GUIDE.md) | Get running in 15-30 minutes |
| [Evaluation Architecture](docs/EVALUATION_ARCHITECTURE.md) | Production evaluation strategies |
| [Evaluation Scenarios](docs/EVALUATION_SCENARIOS.md) | Test failure modes |
| [Langfuse Integration](docs/LANGFUSE_INTEGRATION.md) | Langfuse observability platform |
| [Dashboard API](docs/hyperdx-dashboard-api.md) | Programmatic dashboard creation |

---

## Troubleshooting

### Traces not appearing in HyperDX?

```bash
# Check ClickStack is running
curl http://localhost:8080

# Verify API key is set
grep CLICKSTACK_API_KEY .env

# Check network connectivity
docker exec clickstack clickhouse-client --user api --password api \
  --query "SELECT COUNT(*) FROM otel_traces"
```

### Services won't start?

```bash
# Check logs for errors
docker compose logs --tail=50

# Verify Docker resources (need 8GB+ RAM)
docker system info | grep Memory
```

For more troubleshooting, see the [Quickstart Guide](docs/QUICKSTART_GUIDE.md#troubleshooting).

---

## License

This project is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.

### Third-Party Components

This demo integrates several open-source projects, each with their own licenses:

| Component | License | Link |
|-----------|---------|------|
| **ClickHouse** | Apache 2.0 | [github.com/ClickHouse/ClickHouse](https://github.com/ClickHouse/ClickHouse) |
| **HyperDX/ClickStack** | MIT | [github.com/hyperdxio/hyperdx](https://github.com/hyperdxio/hyperdx) |
| **OpenLLMetry** | Apache 2.0 | [github.com/traceloop/openllmetry](https://github.com/traceloop/openllmetry) |
| **Langfuse** | MIT | [github.com/langfuse/langfuse](https://github.com/langfuse/langfuse) |
| **LibreChat** | MIT | [github.com/danny-avila/LibreChat](https://github.com/danny-avila/LibreChat) |
| **OpenTelemetry** | Apache 2.0 | [opentelemetry.io](https://opentelemetry.io) |

All components are permissively licensed (MIT or Apache 2.0), allowing free use, modification, and distribution for both personal and commercial purposes.

---

## Learn More

**External Resources:**
- [OpenLLMetry Documentation](https://github.com/traceloop/openllmetry) - LLM auto-instrumentation
- [Langfuse Documentation](https://langfuse.com/docs) - LLM observability platform
- [LibreChat + Langfuse Integration](https://langfuse.com/integrations/other/librechat) - Native tracing integration
- [HyperDX Documentation](https://www.hyperdx.io/docs) - Observability platform
- [ClickHouse Documentation](https://clickhouse.com/docs) - Real-time analytics database

**Blog Post:**
- [LLM Observability with ClickStack and MCP](https://clickhouse.com/blog/llm-observability-clickstack-mcp) - Original reference implementation

---

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

---

*Built with ClickHouse, OpenTelemetry, and open-source LLM evaluation tools.*
