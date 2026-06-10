# LLM Observability Demo with ClickHouse

> **Deploying?** Follow [AGENTS.md](AGENTS.md) — the non-interactive deploy runbook
> with verification steps. If the user says "deploy this demo", use that file.

## Architecture

LLM apps (Text-to-SQL, Vector RAG, LibreChat) instrument with the Langfuse SDK. Langfuse stores traces in ClickHouse (OLAP backend). The demo supports two deployment modes:

- **self-hosted** (default): Full Docker stack (~12 containers including Langfuse, PostgreSQL, Redis, MinIO, ClickHouse)
- **cloud**: Langfuse Cloud + only ~5 local containers (set `DEPLOY_MODE=cloud` in `.env`)

## Key Commands

```bash
./setup.sh                              # Idempotent setup (also provisions Langfuse LLM connection + 5 LibreChat agents)
ANTHROPIC_API_KEY=sk-... ./setup.sh     # Non-interactive (CI / coding agents)
./setup.sh --seed                       # Setup + seed demo traces
./setup.sh --status                     # Service status + demo readiness checklist
./setup.sh --cleanup                    # Stop containers (preserves data)
./scripts/seed-demo-data.sh             # Populate sample traces
./scripts/seed-demo-data.sh --quick     # Skip test scenarios
./scripts/seed-demo-data.sh --datasets  # Also seed evaluation datasets
./scripts/reset.sh                      # Full destructive reset
./scripts/validate-langfuse.sh          # Validate Langfuse integration
./scripts/seed-librechat-agents.sh      # Create LibreChat agents with MCP tools
./scripts/langfuse-cli.sh traces list   # Langfuse CLI (requires Node.js 18+)
```

## Dataset & Import Scripts

```bash
# Seed evaluation datasets (coding quality + security)
python scripts/seed-datasets.py                    # Create all datasets
python scripts/seed-datasets.py --dataset quality  # Only quality dataset
python scripts/seed-datasets.py --dry-run          # Preview without creating

# Import traces from an external Langfuse instance
SOURCE_LANGFUSE_PUBLIC_KEY=<pk> SOURCE_LANGFUSE_SECRET_KEY=<sk> \
  python scripts/import-external-traces.py --limit 30 --scrub --add-tag claude-code-demo
```

## Running the Dashboard

```bash
docker compose --profile langfuse --profile dashboard up -d   # Start with dashboard
# Open http://localhost:8005
```

## Running Demo Apps

```bash
docker compose run --rm text-to-sql python main.py              # 3 demo queries
docker compose run --rm text-to-sql python main.py --interactive # Interactive mode
docker compose run --rm vector-rag python main.py               # 3 RAG queries
docker compose --profile tools run --rm test-scenarios           # 40 test scenarios
```

## Code Conventions

- **Langfuse SDK**: v3 patterns — `langfuse.trace()`, `trace.span()`, `trace.generation()`. See `text-to-sql/langfuse_config.py` for setup.
- **Docker profiles**: `langfuse` (Langfuse stack), `demo` (text-to-sql, vector-rag), `tools` (test-scenarios), `dashboard` (LLM Observatory)
- **Environment**: `.env` file sourced by setup.sh. Never commit `.env`. Template is `.env.example`.
- **LANGFUSE_INTERNAL_URL**: Docker-internal Langfuse URL. Unset in self-hosted (falls back to `http://langfuse-web:3000`), set to cloud URL in cloud mode.
- **Scripts**: All scripts `cd` to project root and `source .env`. Check `DEPLOY_MODE` before assuming local services.

## Service URLs (self-hosted defaults)

| Service | URL |
|---------|-----|
| LibreChat | http://localhost:3080 |
| Langfuse | http://localhost:3001 (demo@example.com / demodemo1!) |
| Text-to-SQL API | http://localhost:8002 |
| Vector RAG API | http://localhost:8003 |
| LLM Observatory | http://localhost:8005 |

## Project Layout

```
setup.sh                    # Idempotent setup script
docker-compose.yaml         # Service orchestration (profiles: langfuse, demo, tools)
.env.example                # Environment template (DEPLOY_MODE, keys, ports)
text-to-sql/                # Text-to-SQL demo (Python, Langfuse SDK)
vector-rag/                 # Vector RAG demo (Python, Langfuse SDK, ChromaDB)
librechat/                  # LibreChat customizations (Dockerfile.api, nginx.conf)
test-scenarios/             # 40 synthetic traces for evaluation testing
dashboard/                  # LLM Observatory analytics dashboard (FastAPI + Alpine.js)
mcp-clickhouse/             # ClickHouse MCP Server
scripts/                    # Utility scripts (seed, reset, validate, CLI, import, datasets)
docs/                       # Documentation (incl. LANGFUSE_DEMO_RUNBOOK.md)
```
