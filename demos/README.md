# Demos

Four distinct, independently runnable demos, each an instrumented LLM app that
plugs into the same Langfuse stack. They share the platform at the repo root
(`docker-compose.yaml`, `setup.sh`, `scripts/`, `evaluators/`, `mcp-*/`). For how
they map to the AI Engineering loop, see [`../AI_ENGINEERING_LOOP.md`](../AI_ENGINEERING_LOOP.md).

| Demo | What it shows | Best loop steps | Run |
|------|---------------|-----------------|-----|
| **[text-to-sql](text-to-sql/)** | NL → SQL over ClickHouse via MCP (LangChain) | Trace · Deploy (managed prompts) | `docker compose --profile demo run --rm text-to-sql python main.py` |
| **[vector-rag](vector-rag/)** | RAG over ChromaDB (LangChain) | Trace · Evaluate · Deploy | `docker compose --profile demo run --rm vector-rag python main.py` |
| **[agentic-rag](agentic-rag/)** | Self-correcting RAG on ClickHouse-native vectors (LangGraph) | Trace · Experiment · Deploy | see [`../docs/AGENTIC_RAG_DEMO_RUNBOOK.md`](../docs/AGENTIC_RAG_DEMO_RUNBOOK.md) |
| **[real-estate](real-estate/)** | Self-contained agentic concierge — the whole loop end-to-end in one place | ALL 5 + Deploy | `cd demos/real-estate && ./run_demo.sh` then `./run_portal.sh` |

Each demo has its own README. **real-estate** is fully standalone (its own
`.venv` + `.env`); the other three run as containers in the root
`docker-compose.yaml` (the `demo` profile) and share the stack's Langfuse project.
Service names (`text-to-sql`, `vector-rag`, …) are unchanged — only the source
directories moved under `demos/`.
