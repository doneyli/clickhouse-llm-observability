# LLM Observability with ClickHouse

**A unified observability platform for AI and LLM applications, powered by ClickHouse.**

> **Stack already running?** [Jump to Quick Start](#quick-start) to generate traces in 2 minutes.
> **New to this demo?** Follow the [**Guided User Journey**](docs/USER_JOURNEY.md) for setup and walkthrough (~35 min).

---

## Why ClickHouse for LLM Observability?

Traditional application monitoring tells you *what happened*—request counts, error rates, latency percentiles. But LLM applications are fundamentally different:

| Traditional Apps | LLM Applications |
|------------------|------------------|
| Deterministic outputs | Non-deterministic outputs |
| Errors are obvious | "Wrong" answers look like valid responses |
| Cost is predictable | Cost scales with token usage |
| Debugging = stack traces | Debugging = understanding prompts & completions |

**LLM observability requires capturing, storing, and analyzing every prompt/completion pair—along with quality evaluations that tell you whether the output was actually good.**

### Why ClickHouse?

ClickHouse is the ideal backend for LLM observability because:

- **Columnar storage** - Efficient compression for repetitive LLM data (prompts often share structure)
- **Real-time analytics** - Sub-second queries over billions of traces
- **SQL interface** - Familiar query language for custom dashboards and analysis
- **Cost-effective** - 10-100x cheaper than traditional observability vendors at scale
- **Unified backend** - One database for traces, logs, metrics, and LLM quality scores

### The Value Proposition

With ClickHouse as your centralized observability platform, you can:

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
- **TruLens Dashboard**: http://localhost:8501 (Quality scores and evaluations)
- **Langfuse**: http://localhost:3001 (Alternative trace viewer)

**Time:** 2-3 minutes | **Outcome:** Fresh traces in your observability stack

---

### First Time Setup?

If you haven't set up the observability stack yet, choose one of these paths:

- **[One-Command Setup](#one-command-setup-recommended)** - Fastest way to get everything running (~10 min)
- **[Guided User Journey](docs/USER_JOURNEY.md)** - Hands-on walkthrough with explanations (~35 min)
- **[Quickstart Guide](docs/QUICKSTART_GUIDE.md)** - Step-by-step manual setup (~15-30 min)
- **[Tutorial](docs/TUTORIAL.md)** - Deep dive into concepts and implementation (~1-2 hours)

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
│          └───────────────────┼───────────────────┘                          │
│                              │                                              │
│                              ▼                                              │
│              ┌───────────────────────────────┐                              │
│              │   OpenTelemetry + OpenLLMetry │  ◄── Automatic LLM tracing   │
│              └───────────────┬───────────────┘                              │
│                              │                                              │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │ OTLP (gRPC/HTTP)
                               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            CLICKHOUSE BACKEND                                │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         ClickHouse                                   │   │
│   │                    (Unified Data Backend)                            │   │
│   │                                                                      │   │
│   │  otel_traces         langfuse_*          trulens.sqlite             │   │
│   │  ├─ gen_ai.prompt    ├─ traces           ├─ app_records             │   │
│   │  ├─ gen_ai.completion├─ scores           └─ feedback_results        │   │
│   │  ├─ gen_ai.usage.*   └─ observations                                │   │
│   │  └─ Duration, Model                                                 │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   ┌────────────────┐    ┌────────────────┐    ┌────────────────┐           │
│   │   HyperDX/     │    │    Langfuse    │    │    TruLens     │           │
│   │   ClickStack   │    │   (Optional)   │    │   Dashboard    │           │
│   │ localhost:8080 │    │ localhost:3001 │    │ localhost:8501 │           │
│   │                │    │                │    │                │           │
│   │ • Trace search │    │ • LLM traces   │    │ • Quality      │           │
│   │ • Dashboards   │    │ • Score viz    │    │   evaluations  │           │
│   │ • Alerts       │    │ • Playground   │    │ • Judge reason │           │
│   └────────────────┘    └────────────────┘    └────────────────┘           │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          QUALITY EVALUATION                                   │
│                                                                              │
│   ┌─────────────────┐                    ┌─────────────────┐                │
│   │ trace-evaluator │                    │langfuse-evaluator│               │
│   │   (TruLens)     │                    │  (LLM-as-judge) │                │
│   │                 │                    │                 │                │
│   │ Async evaluation│                    │ Async evaluation│                │
│   │ from ClickHouse │                    │ from Langfuse   │                │
│   │ traces          │                    │ traces          │                │
│   └─────────────────┘                    └─────────────────┘                │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Component Overview

| Component | Purpose | Data Store |
|-----------|---------|------------|
| **OpenLLMetry** | Auto-instruments LLM frameworks (LangChain, Anthropic SDK) | → ClickHouse |
| **HyperDX/ClickStack** | Trace visualization, search, dashboards | ClickHouse |
| **TruLens** | LLM-as-judge quality evaluation | SQLite (local) |
| **Langfuse** (optional) | Alternative evaluation platform with rich UI | ClickHouse |
| **LibreChat** | Chat interface for testing LLM apps | MongoDB |

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
   - LLM-as-judge scoring for relevance and coherence
   - Async evaluation—doesn't slow down your production apps
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
5. Run quality evaluations with TruLens

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

### Tutorial

**Best for:** Learning how LLM observability works and how to apply it to your own applications.

[**Read the Tutorial →**](docs/TUTORIAL.md)

What you'll learn:
1. LLM observability concepts and architecture
2. How OpenTelemetry captures LLM interactions
3. Building instrumented LLM applications
4. Implementing LLM-as-judge evaluation
5. Creating dashboards and alerts
6. Production deployment patterns

**Time:** 1-2 hours | **Outcome:** Deep understanding + ability to implement in your own apps

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

### Running Demos

```bash
# Generate traces with demo queries
docker compose run --rm text-to-sql python main.py
docker compose run --rm vector-rag python main.py

# Interactive mode - type your own questions
docker compose run --rm text-to-sql python main.py --interactive
docker compose run --rm vector-rag python main.py --interactive
```

### Setup & Management

```bash
# One-click setup (first time)
./setup.sh

# Show status and URLs
./setup.sh --status

# Check which services are running
docker compose ps

# View logs
docker compose logs -f [service-name]

# Stop everything
./setup.sh --cleanup
```

### Evaluation

```bash
# Run trace evaluation on recent traces
docker compose run --rm trace-evaluator --service text-to-sql-demo --hours 1

# List all services with LLM traces
docker compose run --rm trace-evaluator --list-services
```

---

## Service Reference

| Service | URL | Purpose |
|---------|-----|---------|
| **HyperDX** | http://localhost:8080 | Traces, logs, metrics, dashboards |
| **TruLens Dashboard** | http://localhost:8501 | Quality scores and judge reasoning |
| **Text-to-SQL API** | http://localhost:8002 | Demo: Natural language → SQL |
| **Vector RAG API** | http://localhost:8003 | Demo: RAG with embeddings |
| **LibreChat** | http://localhost:3080 | Chat UI for LLM interaction |
| **Langfuse** | http://localhost:3001 | Alternative evaluation platform (optional) |

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
├── trace-evaluator/            # Async TruLens evaluation
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
│
├── trulens-dashboard/          # TruLens dashboard service
│   └── Dockerfile
│
├── langfuse-evaluator/         # Async Langfuse evaluation
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
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
│   ├── TUTORIAL.md
│   ├── EVALUATION_ARCHITECTURE.md
│   ├── EVALUATION_SCENARIOS.md
│   ├── LANGFUSE_INTEGRATION.md
│   └── hyperdx-dashboard-api.md
│
└── scripts/                    # Utility scripts
    ├── validate.py
    ├── generate_load.py
    └── create-hyperdx-dashboard-mongo.sh
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [User Journey](docs/USER_JOURNEY.md) | Hands-on walkthrough of the complete demo |
| [Quickstart Guide](docs/QUICKSTART_GUIDE.md) | Get running in 15-30 minutes |
| [Tutorial](docs/TUTORIAL.md) | Learn LLM observability step-by-step |
| [Evaluation Architecture](docs/EVALUATION_ARCHITECTURE.md) | Production evaluation strategies |
| [Evaluation Scenarios](docs/EVALUATION_SCENARIOS.md) | Test failure modes |
| [Langfuse Integration](docs/LANGFUSE_INTEGRATION.md) | Alternative evaluation platform |
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

### TruLens dashboard empty?

```bash
# Run evaluations to populate the dashboard
docker compose run --rm trace-evaluator --service text-to-sql-demo --hours 24
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
| **TruLens** | MIT | [github.com/truera/trulens](https://github.com/truera/trulens) |
| **Langfuse** | MIT | [github.com/langfuse/langfuse](https://github.com/langfuse/langfuse) |
| **LibreChat** | MIT | [github.com/danny-avila/LibreChat](https://github.com/danny-avila/LibreChat) |
| **OpenTelemetry** | Apache 2.0 | [opentelemetry.io](https://opentelemetry.io) |

All components are permissively licensed (MIT or Apache 2.0), allowing free use, modification, and distribution for both personal and commercial purposes.

---

## Learn More

**External Resources:**
- [OpenLLMetry Documentation](https://github.com/traceloop/openllmetry) - LLM auto-instrumentation
- [TruLens Documentation](https://www.trulens.org/) - LLM evaluation framework
- [Langfuse Documentation](https://langfuse.com/docs) - LLM observability platform
- [HyperDX Documentation](https://www.hyperdx.io/docs) - Observability platform
- [ClickHouse Documentation](https://clickhouse.com/docs) - Real-time analytics database

**Blog Post:**
- [LLM Observability with ClickStack and MCP](https://clickhouse.com/blog/llm-observability-clickstack-mcp) - Original reference implementation

---

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

---

*Built with ClickHouse, OpenTelemetry, and open-source LLM evaluation tools.*
