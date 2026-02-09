# LLM Observability with ClickHouse

**A unified observability platform for AI and LLM applications, powered by ClickHouse.**

> **Stack already running?** [Jump to Quick Start](#quick-start) to generate traces in 2 minutes.
> **New to this demo?** Run `./setup.sh` — it's idempotent, handles everything, and takes ~5 minutes.

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

**The bottom line:** ClickHouse gives you the analytical power of a data warehouse with the real-time query performance needed for production debugging—using SQL you already know.

---

## The Value Proposition

With ClickHouse as your centralized observability backend (via Langfuse), you can:

1. **Instrument** - Automatically capture every LLM interaction via the Langfuse SDK
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
docker compose --profile langfuse ps

# Generate Text-to-SQL traces (3 demo queries)
docker compose run --rm text-to-sql python main.py

# Generate Vector RAG traces (3 demo queries)
docker compose run --rm vector-rag python main.py

# Run interactive mode (type your own questions)
docker compose run --rm text-to-sql python main.py --interactive
docker compose run --rm vector-rag python main.py --interactive
```

**View your traces:**
- **Langfuse**: http://localhost:3001 (LLM traces, evaluations, prompt playground)
  - Email: `demo@localhost` | Password: `demodemo1!`

**Time:** 2-3 minutes | **Outcome:** Fresh traces in Langfuse

---

### First Time Setup?

```bash
git clone https://github.com/doneyli/clickhouse-llm-observability.git
cd clickhouse-llm-observability
./setup.sh
```

The setup script is **idempotent** — safe to re-run at any time. It will:
- Create `.env` from template (only if missing)
- Prompt for your Anthropic API key (only if not set)
- Generate LibreChat secrets (only if missing)
- Auto-provision Langfuse with demo credentials (headless init)
- Start all services and wait for health checks

For more detail, see the [Guided User Journey](docs/USER_JOURNEY.md) or [Quickstart Guide](docs/QUICKSTART_GUIDE.md).

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
│     Langfuse SDK        Langfuse SDK       Langfuse (native)               │
│          │                   │                   │                          │
│          └───────────────────┼───────────────────┘                          │
│                              │                                              │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            LANGFUSE                                          │
│                            localhost:3001                                     │
│                                                                              │
│   • Real-time LLM tracing       • Prompt playground                         │
│   • Score visualization          • LLM-as-judge evaluation                  │
│   • Cost tracking                • Session management                       │
│                                                                              │
│   Backend: ClickHouse (OLAP) + PostgreSQL (metadata) + Redis (cache)        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Component Overview

| Component | Purpose | Data Store |
|-----------|---------|------------|
| **Langfuse** | LLM observability, tracing, evaluation | ClickHouse + PostgreSQL |
| **LibreChat** | Chat interface with native Langfuse tracing | MongoDB |
| **Text-to-SQL Demo** | Natural language queries against ClickHouse | Langfuse traces |
| **Vector RAG Demo** | RAG with embeddings and vector search | Langfuse traces |

---

## What You'll Build

This demo sets up a complete LLM observability pipeline:

1. **Demo LLM Applications**
   - Text-to-SQL: Natural language queries against ClickHouse's public demo database
   - Vector RAG: Retrieval-augmented generation with embeddings

2. **Langfuse Instrumentation**
   - Every LLM call captured with prompts, completions, token counts, and latency
   - Langfuse SDK CallbackHandler for LangChain integration

3. **Trace Visualization**
   - Search and filter traces by model, service, or content
   - View the complete request/response flow
   - Track costs and token usage

4. **Quality Evaluation**
   - Langfuse native LLM-as-a-Judge evaluators (Hallucination, Helpfulness, etc.)
   - Automatic evaluation on new traces—no manual triggers needed
   - Historical analysis of quality trends

---

## Setup

### One-Command Setup

```bash
git clone https://github.com/doneyli/clickhouse-llm-observability.git
cd clickhouse-llm-observability
./setup.sh
./scripts/seed-demo-data.sh    # Populate sample traces
```

**Time:** ~5 minutes | **Outcome:** Full demo running with sample traces

The setup script is idempotent — safe to re-run. It never overwrites existing config.

### Re-running Setup

Running `./setup.sh` again is always safe:
- Detects and reuses already-running services
- Preserves existing secrets and API keys
- Only generates missing values

### Langfuse Auto-Provisioned Credentials

Langfuse is auto-configured with a demo account on first boot (headless init):

| | |
|---|---|
| **URL** | http://localhost:3001 |
| **Email** | `demo@localhost` |
| **Password** | `demodemo1!` |

### Additional Guides

- **[Guided User Journey](docs/USER_JOURNEY.md)** — Hands-on walkthrough (~35 min)
- **[Quickstart Guide](docs/QUICKSTART_GUIDE.md)** — Step-by-step manual setup

---

## What You'll Accomplish

After completing the demo, you will have:

| Accomplishment | Benefit |
|----------------|---------|
| **Running observability stack** | Complete local environment for LLM monitoring |
| **Sample traces in ClickHouse** | Real data to explore and query |
| **Quality evaluation pipeline** | LLM-as-judge scoring on production traces |
| **Reusable patterns** | Code and configs you can adapt for your apps |

### Key Benefits

- **Visibility**: See exactly what your LLM apps are doing in production
- **Quality assurance**: Automated evaluation catches bad responses
- **Cost control**: Track token usage and optimize expensive operations
- **Debugging**: Full trace context when things go wrong
- **Compliance**: Audit trail of all LLM interactions

---

## Quick Commands Reference

### Setup & Lifecycle

```bash
# First-time setup (or re-run — it's idempotent)
./setup.sh

# Populate with sample data
./scripts/seed-demo-data.sh

# Check status and URLs
./setup.sh --status

# Stop all services (preserves data)
./setup.sh --cleanup

# Full reset (destroys all data)
./scripts/reset.sh
```

### Daily Use

```bash
# Start everything (if already set up)
./setup.sh

# Check which services are running
docker compose --profile langfuse ps

# View logs
docker compose logs -f [service-name]
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
```

---

## Service Reference

| Service | URL | Purpose |
|---------|-----|---------|
| **Langfuse** | http://localhost:3001 | LLM traces, evaluations, prompt playground |
| **LibreChat** | http://localhost:3080 | Chat UI for LLM interaction |
| **Text-to-SQL API** | http://localhost:8002 | Demo: Natural language → SQL (on-demand) |
| **Vector RAG API** | http://localhost:8003 | Demo: RAG with embeddings (on-demand) |

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key |
| `LANGFUSE_PUBLIC_KEY` | Auto | Pre-filled with demo keys (auto-provisioned) |
| `LANGFUSE_SECRET_KEY` | Auto | Pre-filled with demo keys (auto-provisioned) |
| `CREDS_KEY` | Auto | LibreChat encryption key (auto-generated) |
| `JWT_SECRET` | Auto | LibreChat JWT secret (auto-generated) |

See [`.env.example`](.env.example) for the full configuration reference.

---

## Project Structure

```
├── setup.sh                    # Idempotent setup script (safe to re-run)
├── docker-compose.yaml         # Service orchestration
├── .env.example                # Environment template
├── librechat.yaml              # LibreChat configuration
│
├── text-to-sql/                # Text-to-SQL demo app
│   ├── Dockerfile
│   ├── main.py
│   ├── langfuse_config.py
│   └── requirements.txt
│
├── vector-rag/                 # Vector RAG demo app
│   ├── Dockerfile
│   ├── main.py
│   ├── langfuse_config.py
│   └── requirements.txt
│
├── librechat/                  # LibreChat customizations
│   ├── Dockerfile.api
│   └── nginx.conf
│
├── mcp-clickhouse/             # ClickHouse MCP Server
│   └── Dockerfile
│
├── test-scenarios/             # Evaluation test scenarios
│   ├── Dockerfile
│   ├── export_test_scenarios.py
│   └── requirements.txt
│
├── docs/                       # Documentation
│   ├── QUICKSTART_GUIDE.md
│   ├── USER_JOURNEY.md
│   ├── EVALUATION_ARCHITECTURE.md
│   ├── EVALUATION_SCENARIOS.md
│   └── LANGFUSE_INTEGRATION.md
│
└── scripts/                    # Utility scripts
    ├── seed-demo-data.sh       # Populate demo with sample traces
    ├── reset.sh                # Full reset (destructive)
    └── validate-langfuse.sh    # Validate Langfuse integration
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

---

## Troubleshooting

### Traces not appearing in Langfuse?

```bash
# Check Langfuse is running
curl http://localhost:3001

# Verify API keys are set
grep LANGFUSE_PUBLIC_KEY .env

# Check Langfuse logs
docker compose --profile langfuse logs langfuse-web
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
| **Langfuse** | MIT | [github.com/langfuse/langfuse](https://github.com/langfuse/langfuse) |
| **LibreChat** | MIT | [github.com/danny-avila/LibreChat](https://github.com/danny-avila/LibreChat) |

All components are permissively licensed (MIT or Apache 2.0), allowing free use, modification, and distribution for both personal and commercial purposes.

---

## Learn More

**External Resources:**
- [Langfuse Documentation](https://langfuse.com/docs) - LLM observability platform
- [LibreChat + Langfuse Integration](https://langfuse.com/integrations/other/librechat) - Native tracing integration
- [ClickHouse Documentation](https://clickhouse.com/docs) - Real-time analytics database

---

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

---

*Built with ClickHouse, Langfuse, and open-source LLM tools.*
