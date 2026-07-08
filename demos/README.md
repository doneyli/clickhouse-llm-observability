# Demos

Six independently runnable demos, each building on the same ClickHouse-backed
Langfuse stack. The container demos share the platform at the repo root
(`docker-compose.yaml`, `setup.sh`, `scripts/`, `evaluators/`, `mcp-*/`); the
standalone demos bring their own toolchain. For how they map to the AI
Engineering loop, see [`../AI_ENGINEERING_LOOP.md`](../AI_ENGINEERING_LOOP.md).

| Demo | What it shows | Best loop steps | Run |
|------|---------------|-----------------|-----|
| **[text-to-sql](text-to-sql/)** | NL → SQL over ClickHouse via MCP (LangChain) | Trace · Deploy (managed prompts) | `docker compose --profile demo run --rm text-to-sql python main.py` |
| **[vector-rag](vector-rag/)** | RAG over ChromaDB (LangChain) | Trace · Evaluate · Deploy | `docker compose --profile demo run --rm vector-rag python main.py` |
| **[agentic-rag](agentic-rag/)** | Self-correcting RAG on ClickHouse-native vectors (LangGraph) | Trace · Experiment · Deploy | see [`../docs/AGENTIC_RAG_DEMO_RUNBOOK.md`](../docs/AGENTIC_RAG_DEMO_RUNBOOK.md) |
| **[real-estate](real-estate/)** | Self-contained agentic concierge — the whole loop end-to-end in one place | ALL 5 + Deploy | `cd demos/real-estate && ./run_demo.sh` then `./run_portal.sh` |
| **[brand-promo-multi-agent](brand-promo-multi-agent/)** | Multi-agent promo-planning assistant (LangGraph + CrewAI): synthetic history, online + offline evals, persona dashboards | Trace · Datasets · Experiment · Evaluate · Deploy | see [README](brand-promo-multi-agent/) |
| **[langfuse-rls](langfuse-rls/)** | Attribute-based row-level-security prototype over Langfuse traces (Next.js): trace governance / access control | Governance (adjacent to the loop) | `cd demos/langfuse-rls && npm install && npm run dev` |

Each demo has its own README. **text-to-sql**, **vector-rag**, and **agentic-rag**
run as containers in the root `docker-compose.yaml` (the `demo` profile) and share
the stack's Langfuse project (service names are unchanged; only the source
directories live under `demos/`). **real-estate** (Python, own `.venv` + `.env`),
**brand-promo-multi-agent** (Python, `uv`), and **langfuse-rls** (Next.js, `npm`)
are fully standalone: they point at the same local Langfuse
(http://localhost:3001) but manage their own dependencies and `.env`. Each
standalone demo ships an `.env.example`; never commit a real `.env`.
