# Demos

Seven independently runnable demos, each building on the same ClickHouse-backed
Langfuse stack. The container demos share the platform at the repo root
(`docker-compose.yaml`, `setup.sh`, `scripts/`, `evaluators/`, `mcp-*/`); the
standalone demos bring their own toolchain. For how they map to the AI
Engineering loop, see [`../AI_ENGINEERING_LOOP.md`](../AI_ENGINEERING_LOOP.md).

| Demo | What it shows | Best loop steps | Run |
|------|---------------|-----------------|-----|
| **[text-to-sql](text-to-sql/)** | NL → SQL with a generate→critique→refine loop grounded in real EXPLAIN/execution evidence (LangChain) | Trace · Evaluate · Experiment · Deploy | `docker compose --profile demo run --rm text-to-sql python main.py` (legacy) · `-e REFINE_MODE=1 … python main.py --refine` (Pattern #5) · client script: [`DEMO_SCRIPT.md`](text-to-sql/DEMO_SCRIPT.md) |
| **[vector-rag](vector-rag/)** | RAG over ChromaDB (LangChain) | Trace · Evaluate · Deploy | `docker compose --profile demo run --rm vector-rag python main.py` · client script: [`DEMO_SCRIPT.md`](vector-rag/DEMO_SCRIPT.md) |
| **[agentic-rag](agentic-rag/)** | Self-correcting RAG on ClickHouse-native vectors (LangGraph) | Trace · Experiment · Deploy | see [`DEMO_SCRIPT.md`](agentic-rag/DEMO_SCRIPT.md) (client script) or [`../docs/AGENTIC_RAG_DEMO_RUNBOOK.md`](../docs/AGENTIC_RAG_DEMO_RUNBOOK.md) (deep reference) |
| **[litellm-gateway](litellm-gateway/)** | LiteLLM AI gateway with centralized Langfuse OTLP tracing | Trace · Gateway | `./demos/litellm-gateway/run_demo.sh` · client script: [`DEMO_SCRIPT.md`](litellm-gateway/DEMO_SCRIPT.md) |
| **[real-estate](real-estate/)** | Self-contained agentic concierge — the whole loop end-to-end in one place | ALL 5 + Deploy | `cd demos/real-estate && ./run_demo.sh` then `./run_portal.sh` · client script: [`DEMO_SCRIPT.md`](real-estate/DEMO_SCRIPT.md) |
| **[brand-promo-multi-agent](brand-promo-multi-agent/)** | Multi-agent promo-planning assistant (LangGraph + CrewAI): synthetic history, online + offline evals, persona dashboards | Trace · Datasets · Experiment · Evaluate · Deploy | see [`DEMO_SCRIPT.md`](brand-promo-multi-agent/DEMO_SCRIPT.md) (client script) or [README](brand-promo-multi-agent/) |
| **[langfuse-rls](langfuse-rls/)** | Attribute-based row-level-security prototype over Langfuse traces (Next.js): trace governance / access control | Governance (adjacent to the loop) | `cd demos/langfuse-rls && npm install && npm run dev` · client script: [`DEMO_SCRIPT.md`](langfuse-rls/DEMO_SCRIPT.md) |

Each demo has its own README. **text-to-sql**, **vector-rag**, **agentic-rag**,
and **litellm-gateway** run as containers in the root `docker-compose.yaml`
(the `demo` profile). LiteLLM can use a dedicated Langfuse project through
local `LITELLM_LANGFUSE_*` credentials; see the
[gateway operations guide](../docs/LITELLM_GATEWAY_DEMO.md). **real-estate**
(Python, own `.venv` + `.env`),
**brand-promo-multi-agent** (Python, `uv`), and **langfuse-rls** (Next.js, `npm`)
are fully standalone: they point at the same local Langfuse
(http://localhost:3001) but manage their own dependencies and `.env`. Each
standalone demo ships an `.env.example`; never commit a real `.env`.
