# Demos

Independently runnable demos, all built on the same ClickHouse-backed Langfuse
platform. For how they map to the AI Engineering loop, see
[`../AI_ENGINEERING_LOOP.md`](../AI_ENGINEERING_LOOP.md). To add or change a demo,
follow [`CONTRIBUTING.md`](CONTRIBUTING.md).

## How the demos are organised

Each demo declares one profile in its `demo.yaml`:

- **Container demos** run as services in the root `docker-compose.yaml` under the
  `demo` profile and use the root `.env`. They share the platform at the repo root:
  `docker-compose.yaml`, `setup.sh`, `scripts/`, `evaluators/` and the `mcp-*/`
  servers. Their run commands are typed from the repo root.
- **Standalone demos** bring their own toolchain (`.venv`, `uv` or `npm`), their own
  `.env` copied from `.env.example`, and usually their own Langfuse project, on
  Langfuse Cloud or the local stack at http://localhost:3001. Their run commands
  are typed from the demo folder, which the Run column below includes.
- **Workshops** are kits that learners work through, led by a facilitator. Each
  ships a `WORKSHOP_GUIDE.md` in place of a demo script.

Every demo has a `README.md` with the same sections in the same order, a demo script
(or workshop guide) with the same skeleton, a readiness check, and tests that need no
services. Never commit a real `.env`; shell-exported `LANGFUSE_*` keys can send traces
to the wrong project, so `unset` them before running anything.

## Index

The tables below are generated from each demo's `demo.yaml` by
`python scripts/gen-demo-index.py`. Do not edit them by hand. CI fails when they are
out of date.

<!-- demo-index:start -->
### Container demos

| Demo | What it shows | Covers | Length | Run | Present |
|---|---|---|---|---|---|
| **[Agentic RAG](agentic-rag/)** | Self-correcting RAG (CRAG) on ClickHouse-native vector search with LangGraph, fully scored in Langfuse | Trace · Experiment · Deploy | 20–25 min · short path 8–10 min | `docker compose --profile demo run --rm agentic-rag python main.py` | [DEMO_SCRIPT.md](agentic-rag/DEMO_SCRIPT.md) |
| **[Cluster Health Investigator](cluster-health-investigator/)** | Orchestrator-workers agent that decides at runtime how many system-table analyses to run, then diagnoses the stack's own ClickHouse | Trace · Monitor · Datasets · Experiment · Evaluate | 20 min · short path 8 min | `docker compose --profile langfuse --profile demo run --rm cluster-health python main.py` | [DEMO_SCRIPT.md](cluster-health-investigator/DEMO_SCRIPT.md) |
| **[LiteLLM Gateway](litellm-gateway/)** | Centralized observability at the AI gateway, where a LiteLLM proxy exports every call to Langfuse over OTLP, with no SDK in the client | Trace · Gateway | 5 min | `./demos/litellm-gateway/run_demo.sh` | [DEMO_SCRIPT.md](litellm-gateway/DEMO_SCRIPT.md) |
| **[Query Router](query-router/)** | Front-door classification and dispatch with a confidence gate, fallback, human escalation and router-accuracy evals | Trace · Monitor · Datasets · Experiment · Evaluate | 15 min · short path 6 min | `docker compose --profile demo run --rm query-router python main.py` | [DEMO_SCRIPT.md](query-router/DEMO_SCRIPT.md) |
| **[Slow Query Tuner](slow-query-tuner/)** | Autonomous plan-act-observe agent that tunes a slow ClickHouse query and decides when it is fast enough, with spend caps and a kill switch | Trace · Monitor · Datasets · Experiment · Evaluate | 25 min · short path 12 min | `docker compose --profile demo run --rm slow-query-tuner python main.py --query q1` | [DEMO_SCRIPT.md](slow-query-tuner/DEMO_SCRIPT.md) |
| **[Support Triage Parallel](support-triage-parallel/)** | Parallel ticket triage with 4-branch sectioning and best-of-N SQL voting counted by ClickHouse | Trace · Monitor · Experiment · Evaluate | 15–20 min · short path 8 min | `docker compose --profile demo run --rm support-triage-parallel python main.py` | [DEMO_SCRIPT.md](support-triage-parallel/DEMO_SCRIPT.md) |
| **[Text-to-SQL](text-to-sql/)** | Prompt chaining with gate checks over ClickHouse via MCP, with an opt-in generate-critique-refine loop grounded in EXPLAIN evidence | Trace · Experiment · Evaluate · Deploy | 20 min · short path 6 min | `docker compose --profile demo run --rm text-to-sql python main.py` | [DEMO_SCRIPT.md](text-to-sql/DEMO_SCRIPT.md) |
| **[Vector RAG](vector-rag/)** | The naive-RAG baseline, fully traced, with ChromaDB retrieval and a Langfuse-managed generation prompt shipped by label | Trace · Evaluate · Deploy | 10–12 min · short path 5 min | `docker compose --profile demo run --rm vector-rag python main.py` | [DEMO_SCRIPT.md](vector-rag/DEMO_SCRIPT.md) |

### Standalone demos

| Demo | What it shows | Covers | Length | Run | Present |
|---|---|---|---|---|---|
| **[Analytics at Agentic Speed](analytics-agentic-speed/)** | An analytics agent that sweeps hundreds of what-if scenarios on ClickHouse Cloud, with the database's own latency on every traced query span | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy | 15 min · short path 8 min | `cd demos/analytics-agentic-speed && .venv/bin/python main.py --question q-padding` | [DEMO_SCRIPT.md](analytics-agentic-speed/DEMO_SCRIPT.md) |
| **[Brand Promo Multi-Agent](brand-promo-multi-agent/)** | A re-themeable promo-planning agent fleet on LangGraph and CrewAI with 50k synthetic traces, persona dashboards, online judges and a certification gate | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy | 25–30 min · short path 10–12 min | `cd demos/brand-promo-multi-agent && uv run scripts/run_live_demo.py play-all` | [DEMO_SCRIPT.md](brand-promo-multi-agent/DEMO_SCRIPT.md) |
| **[Grocery Assistant](grocery-assistant/)** | One shopping agent instrumented well and badly, plus which evaluators to build first, with deterministic checks before any judge | Trace · Monitor · Datasets · Experiment · Evaluate | 25–30 min · short path 12 min | `cd demos/grocery-assistant && npm run demo` | [DEMO_SCRIPT.md](grocery-assistant/DEMO_SCRIPT.md) |
| **[Langfuse Enterprise Operations](langfuse-enterprise-operations/)** | Real platform lifecycle automation from a local portal, covering projects, RBAC, key rotation, audit evidence and reviewed reset | Governance · Operations | 20–30 min | `cd demos/langfuse-enterprise-operations && ./scripts/run-portal.sh` | [DEMO_SCRIPT.md](langfuse-enterprise-operations/DEMO_SCRIPT.md) |
| **[Northwind Bank Assistant](bank-assistant/)** | A bilingual retail-bank assistant on LangGraph and MCP with guardrails, PII masking, judge calibration, prompt lifecycle and a CI quality gate | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy · Governance | 120 min | `cd demos/bank-assistant && ./scripts/run_portal.sh` | [DEMO_SCRIPT.md](bank-assistant/DEMO_SCRIPT.md) · [Evals-first 30-minute demo (30 min)](bank-assistant/DEMO_SCRIPT_EVALS_FIRST.md) |
| **[Northwind Life & Annuity claims](insurance-claims/)** | Death-claims agents checked against an SME answer key, with a prompt CI gate | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy · Governance | 50 min | `cd demos/insurance-claims && ./scripts/run_services.sh` | [DEMO_SCRIPT.md](insurance-claims/DEMO_SCRIPT.md) |
| **[Property Concierge](real-estate/)** | An agentic property concierge that walks the whole AI engineering loop, from tracing to prompt deploy by label and a GitHub CI gate | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy | 18–24 min · short path 6–8 min | `cd demos/real-estate && ./run_demo.sh` | [DEMO_SCRIPT.md](real-estate/DEMO_SCRIPT.md) |
| **[Trace Governance (RLS)](langfuse-rls/)** | Simulated attribute-based row-level security over Langfuse traces, with three personas seeing different slices of one project | Governance | 8–12 min | `cd demos/langfuse-rls && npm run dev` | [DEMO_SCRIPT.md](langfuse-rls/DEMO_SCRIPT.md) |

### Workshops

| Demo | What it shows | Covers | Length | Run | Present |
|---|---|---|---|---|---|
| **[Bank Assistant Workshop](bank-assistant-workshop/)** *(draft)* | Nine-module hands-on workshop that adds tracing, prompts, monitoring and experiments to a synthetic bank assistant | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy | 180 min | `cd demos/bank-assistant-workshop && .venv/bin/python workshop.py serve` | [WORKSHOP_GUIDE.md](bank-assistant-workshop/WORKSHOP_GUIDE.md) |
<!-- demo-index:end -->
