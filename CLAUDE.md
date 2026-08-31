# LLM Observability Demo with ClickHouse

> **Deploying?** Follow [AGENTS.md](AGENTS.md) — the non-interactive deploy runbook
> with verification steps. If the user says "deploy this demo", use that file.

## Project Skills

Lifecycle tasks have project skills in `.agents/skills/` (auto-discovered via
`.claude/skills/` symlinks) — prefer them over improvising:

- **deploy-demo** — deploy + verify the full stack (wraps AGENTS.md)
- **run-demo** — present/rehearse a demo: pre-flight, fresh traces, act-by-act guidance
- **troubleshoot** — triage order and recovery ladder for a broken stack
- **langfuse** — query Langfuse data via CLI, access Langfuse docs

For presenter-facing material, see [docs/SA_FIELD_GUIDE.md](docs/SA_FIELD_GUIDE.md)
(demo selection, talk track, objections) and [docs/USE_CASES.md](docs/USE_CASES.md)
(11 use cases with 2-minute demo paths). [docs/README.md](docs/README.md) indexes all
docs by persona.

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
./scripts/seed-code-evaluators.sh       # Provision code evaluators (deterministic TS evals; see docs/CODE_EVALUATORS.md)
./scripts/seed-llm-judge-evaluators.sh  # Provision observation-level LLM-as-a-Judge evaluators (upgrades legacy ones)
./scripts/seed-agentic-rag-evaluators.sh # Independent managed judges for agentic-rag (faithfulness/context-relevance/answer-relevance) — complements the in-graph self-grades
python scripts/seed-app-prompts.py      # Prompt management (Deploy node): seed text-to-sql + vector-rag prompts to Langfuse
# Prompt CI quality gate (Deploy node, real-estate demo) — .github/workflows/langfuse-prompt-ci.yml
cd demos/real-estate && ./.venv/bin/python scripts/prompt_gate.py --prompt-label first-draft   # exits 1: gate blocks a bad prompt
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
./demos/litellm-gateway/run_demo.sh                             # Gateway call + trace verification
docker compose --profile tools run --rm test-scenarios           # 40 test scenarios
```

## Code Conventions

- **Langfuse SDK**: **v4** (`langfuse>=4.7,<5.0`; `demos/real-estate` floors at `>=4.10` for multi-modal dataset items) — `start_as_current_observation(as_type=...)`, `propagate_attributes(...)`, `dataset.run_experiment(...)`. Put overall input/output on the **root observation** (`root.update(input=…, output=…)`): v4 is observations-first and derives a trace's input/output from it. Do **not** add `set_current_trace_io()` — it is deprecated and exists only for legacy *trace*-target LLM-as-a-judge rules. See `demos/text-to-sql/langfuse_config.py` for setup and [docs/LANGFUSE_V4_MIGRATION_SPEC.md](docs/LANGFUSE_V4_MIGRATION_SPEC.md) for the migration record. The self-hosted server stays on **v3** (`langfuse/langfuse:3.221.1`); the v4 SDK supports it (minimum server 3.63.0). Read-API caveat: the **v2** endpoints (`api.observations`, `api.metrics`, `GET /api/public/v2/observations`) do not exist on a v3 server, so self-hosted code must use `api.legacy.observations_v1` / `api.legacy.metrics_v1`; `GET /api/public/v3/scores` *is* available on v3.
- **Langfuse keys**: never rely on shell-exported `LANGFUSE_*`. `docker-compose.yaml` resolves `${LANGFUSE_PUBLIC_KEY:-}`, and **shell exports outrank `.env`** — a key for another project silently sends traces there while your queries read a different one, producing false 404s. Before running or verifying demos: `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL`, and confirm which project a key maps to (`GET /api/public/projects`) before trusting a 404.
- **Docker profiles**: `langfuse` (Langfuse stack), `demo` (app demos + LiteLLM gateway), `tools` (test-scenarios), `dashboard` (LLM Observatory)
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
| LiteLLM Gateway | http://localhost:4000 |
| LLM Observatory | http://localhost:8005 |

## Project Layout

```
setup.sh                    # Idempotent setup script
docker-compose.yaml         # Service orchestration (profiles: langfuse, demo, tools)
.env.example                # Environment template (DEPLOY_MODE, keys, ports)
demos/                      # The distinct LLM-app demos (see demos/README.md)
  text-to-sql/              #   Text-to-SQL demo (Python, LangChain, Langfuse SDK)
  vector-rag/               #   Vector RAG demo (Python, LangChain, ChromaDB)
  agentic-rag/              #   Self-correcting RAG on ClickHouse-native vectors (LangGraph)
  litellm-gateway/          #   LiteLLM proxy + Langfuse OTLP tracing MVP
  real-estate/              #   Standalone agentic concierge — the loop end-to-end (own venv/.env)
  grocery-assistant/        #   Retail-grocery assistant (TypeScript/Vercel AI SDK 7) — good vs
                            #   broken instrumentation + evaluator-selection teaching demo (own .env)
  brand-promo-multi-agent/  #   Standalone multi-agent promo assistant (LangGraph + CrewAI, uv)
  langfuse-rls/             #   Standalone trace RLS prototype (Next.js)
librechat/                  # LibreChat customizations (Dockerfile.api, entrypoint.sh, nginx.conf)
test-scenarios/             # 40 synthetic traces for evaluation testing
evaluators/                 # Langfuse code evaluators (TypeScript, seeded into Langfuse by setup)
dashboard/                  # LLM Observatory analytics dashboard (FastAPI + Alpine.js)
mcp-clickhouse/             # ClickHouse MCP Server
scripts/                    # Utility scripts (seed, reset, validate, CLI, import, datasets)
docs/                       # Documentation (see docs/README.md for the persona-based index)
.github/workflows/          # Prompt CI: eval a changed prompt version, block the deploy on a regression
.agents/skills/             # Project skills (deploy-demo, run-demo, troubleshoot, langfuse)
.claude/settings.json       # Shared Claude Code permissions for this repo's common commands
```
