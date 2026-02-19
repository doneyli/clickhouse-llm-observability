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

## Deployment Modes

This demo supports two deployment modes. **Cloud is recommended** for quick demos — it needs fewer containers and no local Langfuse stack.

| | Cloud | Self-Hosted |
|---|---|---|
| **Langfuse** | [Langfuse Cloud](https://cloud.langfuse.com) (free tier) | Docker (~7 containers) |
| **ClickHouse (Langfuse backend)** | Managed by Langfuse Cloud | Docker (local) |
| **Local containers** | ~5 (LibreChat, MongoDB, Meilisearch, Nginx, MCP) | ~12 (all of the above + Langfuse stack) |
| **Setup** | Set `DEPLOY_MODE=cloud` + Langfuse Cloud API keys | Default — just run `./setup.sh` |
| **Best for** | Quick demos, workshops, low-resource machines | Fully offline, air-gapped, or custom Langfuse config |

**Cloud quick start:**
```bash
# 1. Sign up at https://cloud.langfuse.com (free)
# 2. Create a project and copy your API keys from Settings > API Keys
# 3. Configure:
cp .env.example .env
# Edit .env: set DEPLOY_MODE=cloud, paste your keys
./setup.sh
```

**Self-hosted quick start** (default):
```bash
./setup.sh    # Everything runs locally
```

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
  - Email: `demo@example.com` | Password: `demodemo1!`

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

### Components

| Component | What It Does | Langfuse Trace Name | Filter Tag |
|-----------|-------------|--------------------:|----------:|
| **Text-to-SQL** | Converts natural language to SQL and runs it against ClickHouse's public demo database (UK property, GitHub events, OpenSky flights) | `text-to-sql` | `text-to-sql` |
| **Vector RAG** | Retrieval-augmented generation — embeds documents with sentence-transformers, stores in ChromaDB, retrieves context for LLM answers | `vector-rag` | `vector-rag` |
| **LibreChat** | Full chat UI with native Langfuse tracing — use it like ChatGPT, every conversation is traced | `AgentRun` | `librechat` |
| **Test Scenarios** | Pre-crafted prompt/response pairs that intentionally fail in different ways — used to demo Langfuse evaluators | per-scenario name | `test-scenario` |
| **Langfuse** | The observability platform — stores traces in ClickHouse, provides UI for search, cost tracking, and LLM-as-a-Judge evaluation | — | — |

### Trace Tagging

Every trace source is tagged so you can filter in Langfuse:

| Tag | What It Captures |
|-----|-----------------|
| `text-to-sql` | Text-to-SQL demo queries |
| `vector-rag` | Vector RAG demo queries |
| `librechat` | LibreChat conversations |
| `demo` | All demo queries (text-to-sql + vector-rag) |
| `test-scenario` | All test scenario traces |

### Test Scenarios

40 synthetic traces (10 per category) designed to demonstrate evaluation failure modes:

| IDs | Category | Tests For | Examples |
|-----|----------|-----------|---------|
| 1-10 | **Low Relevance** | Relevance | Asks about pricing/backup/security but answers about unrelated features |
| 11-20 | **Low Coherence** | Correctness | Contradicts itself on DB choice, partitioning, replication, compression |
| 21-30 | **Hallucination** | Hallucination | Fabricated history, fake SQL syntax, fake benchmarks, fake acquisitions |
| 31-40 | **Control** | Baseline | Accurate answers about MergeTree, partitioning, replication, compression |

Filter by `test-scenario` in Langfuse, then configure LLM-as-a-Judge evaluators to auto-score them. Use `--list` to see all scenarios: `docker compose --profile tools run --rm --entrypoint python3 test-scenarios export_test_scenarios.py --list`

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
| **Email** | `demo@example.com` |
| **Password** | `demodemo1!` |

### Additional Guides

- **[Guided User Journey](docs/USER_JOURNEY.md)** — Hands-on walkthrough (~35 min)
- **[Quickstart Guide](docs/QUICKSTART_GUIDE.md)** — Step-by-step manual setup

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

### Langfuse CLI

```bash
# List recent traces (requires Node.js 18+)
./scripts/langfuse-cli.sh traces list --limit 5

# Get a specific trace
./scripts/langfuse-cli.sh traces get <trace-id>

# List prompts, datasets, scores
./scripts/langfuse-cli.sh prompts list
./scripts/langfuse-cli.sh datasets list
./scripts/langfuse-cli.sh scores list
```

See [Langfuse CLI docs](docs/LANGFUSE_CLI.md) for more.

### Running Demos

```bash
# Generate traces (tagged automatically)
docker compose run --rm text-to-sql python main.py     # → traces tagged "text-to-sql"
docker compose run --rm vector-rag python main.py      # → traces tagged "vector-rag"

# Interactive mode
docker compose run --rm text-to-sql python main.py --interactive
docker compose run --rm vector-rag python main.py --interactive

# Export test scenarios for evaluation testing
docker compose --profile tools run --rm test-scenarios  # → traces tagged "test-scenario"

# Seed everything at once
./scripts/seed-demo-data.sh
```

### Evaluation

**Step 1: Export test scenarios**

```bash
docker compose --profile tools run --rm test-scenarios
```

**Step 2: Create evaluators in Langfuse**

Go to http://localhost:3001 → **Evaluations** → **LLM-as-a-Judge** → **+ New Evaluator** and create these three:

| Evaluator | Template to Select | What It Catches |
|-----------|--------------------|-----------------|
| **Hallucination** | Hallucination | Fabricated facts stated confidently |
| **Relevance** | Relevance | Answers that ignore the actual question |
| **Correctness** | Correctness | Contradictory or logically inconsistent answers |

For each evaluator, set the filter to tag: `test-scenario`.

**Ground truth:** Each test scenario includes a `ground_truth` field stored in the trace metadata. The Correctness evaluator template references `{{expected_output}}` — for online evaluations this is populated from dataset items. For this demo, the Correctness evaluator still works well without it by comparing the output against the input query. To use ground truth with full accuracy, create a [Langfuse Dataset](https://langfuse.com/docs/datasets/overview) and run evaluations via experiments.

**Step 3: Verify expected results**

After evaluators run, the 40 test scenarios should score by category:

| Category (IDs) | Hallucination | Relevance | Correctness | Pattern |
|----------------|:---:|:---:|:---:|-----|
| **Low Relevance** (1-10) | Pass | Fail | Pass | Coherent and factual, but answers the wrong question |
| **Low Coherence** (11-20) | Pass | Pass | Fail | On-topic but contradicts itself repeatedly |
| **Hallucination** (21-30) | Fail | Pass | Pass | Relevant and well-structured, but entirely made up |
| **Control** (31-40) | Pass | Pass | Pass | Baseline — accurate, relevant, consistent |

### Reset & Maintenance

```bash
# Full reset (deletes all data, requires re-setup)
./scripts/reset.sh

# Re-seed demo data
./scripts/seed-demo-data.sh
```

---

## Coding Agent Support

This project is set up for AI coding agents (Claude Code, Cursor):

- **`CLAUDE.md`** at the project root provides architecture, commands, and conventions that Claude Code reads automatically
- **Langfuse Skills** teach agents about Langfuse SDK patterns, observability, and prompt management

```bash
# Install Langfuse skills (optional)
npx skills add langfuse/skills --skill "langfuse"
```

See [Langfuse Skills docs](docs/LANGFUSE_SKILLS.md) for details.

---

## Service Reference

| Service | URL | Purpose | Langfuse Tag |
|---------|-----|---------|:---:|
| **Langfuse** | http://localhost:3001 | Traces, evaluations, prompt playground (`demo@example.com` / `demodemo1!`) | — |
| **LibreChat** | http://localhost:3080 | Chat UI — register with any email/password | `librechat` |
| **Text-to-SQL** | http://localhost:8002 | Natural language → SQL against ClickHouse demo data | `text-to-sql` |
| **Vector RAG** | http://localhost:8003 | RAG with embeddings + ChromaDB | `vector-rag` |

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
├── .env.example                # Environment template (DEPLOY_MODE, keys, ports)
├── librechat.yaml              # LibreChat configuration
├── CLAUDE.md                   # Project context for Claude Code
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
│   ├── entrypoint.sh           # Patches LibreChat to add Langfuse tags
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
│   ├── LANGFUSE_INTEGRATION.md
│   ├── LANGFUSE_CLI.md
│   └── LANGFUSE_SKILLS.md
│
└── scripts/                    # Utility scripts
    ├── seed-demo-data.sh       # Populate demo with sample traces
    ├── reset.sh                # Full reset (destructive)
    ├── validate-langfuse.sh    # Validate Langfuse integration
    └── langfuse-cli.sh         # Langfuse CLI wrapper
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
| [Langfuse CLI](docs/LANGFUSE_CLI.md) | Terminal access to traces, prompts, scores |
| [Langfuse Skills](docs/LANGFUSE_SKILLS.md) | AI coding agent integration |

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
