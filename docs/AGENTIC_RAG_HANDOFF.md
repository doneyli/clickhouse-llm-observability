# Agentic RAG — Handoff / Continue Here

Single source of truth to resume work on the Agentic RAG demo. Last updated 2026-06-02.

## TL;DR state
- **Branch:** `feature/agentic-rag-clickhouse` (pushed). **PR:** [#16](https://github.com/doneyli/clickhouse-llm-observability/pull/16) (open, for review).
- **Open issue:** [#15](https://github.com/doneyli/clickhouse-llm-observability/issues/15) — intermittent demo hang on a stalled LLM call.
- **Commits on branch:** `041b5a2` (feature) → `b2152c3` (tests + reflect-loop fix).
- The demo is **built and validated end-to-end**. Tests pass (15). Nothing is mid-flight or broken.

## What's done
- **ClickHouse-native vectors** (`clickhouse-vectors` service, image `clickhouse/clickhouse-server:26.3`): `agentic_rag.kb_chunks` with `vector_similarity('hnsw','cosineDistance',384)` (EXPLAIN-verified used). Separate from Langfuse's CH 25.8.
- **LangGraph CRAG agent** (`agentic-rag/`): route → retrieve → grade → rewrite/re-retrieve → sql_tool → generate → reflect. FastAPI :8006 + CLI (`main.py`, 5 demo questions).
- **MCP retriever** (`mcp-rag-retriever/`): `retrieve_kb` + `list_documents` over SSE; LibreChat **"Agentic RAG Assistant"** agent bound to it (seeded).
- **Langfuse**: typed observations → Agent Graph; scores `retrieval_relevance` (span) + `groundedness` (trace); prompt management (`agentic-rag-generation` v1+v2, generate node pulls `label=production`, `promptVersion` linked to traces).
- **Docs**: `AGENTIC_RAG_ARCHITECTURE.md` (+ SVG/PNG), `AGENTIC_RAG_DEMO_RUNBOOK.md` (25-min script), this handoff.
- **Tests**: `agentic-rag/tests/` — 15 passing, offline (no CH/Anthropic).

## What's left (pick up here)
1. **Review/merge PR #16.**
2. **Issue #15 — robust hang fix:** add a hard per-node wall-clock cap (e.g. `asyncio.wait_for`/`concurrent.futures`) independent of the SDK timeout; circuit-breaker/fail-fast; then capture the full 5-question transcript. Partial mitigation already in (`default_request_timeout` + `max_retries` in `graph.py::_llm`, env `LLM_TIMEOUT`/`LLM_MAX_RETRIES`).
3. **Phase 5 — Evaluation + demo surface (not started):**
   - Wire Langfuse datasets into a `run_experiment` comparing **naive `vector-rag` vs `agentic-rag`**.
   - Online LLM-as-judge for faithfulness/context-relevance on production traces.
   - Dashboard panel (LLM Observatory) for retriever/tool spans + the two scores.
   - New runbook act for the comparison.
4. **Optional:** folder-based ingest so the demo can index *your own* docs (currently `ingest.py` reads the fixed `vector-rag/documents.py` corpus). User confirmed "demo corpus is fine" for now.

## How to resume (commands)
```bash
git checkout feature/agentic-rag-clickhouse

# IMPORTANT: clear leaked Langfuse keys from your shell (they override .env -> 401s)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Bring up the stack (both profiles so nothing is missing)
docker compose --profile langfuse --profile demo up -d

# Build the vector index (idempotent) + seed the managed prompt + LibreChat agent
docker compose --profile demo run --rm agentic-rag python ingest.py
python scripts/seed-langfuse-prompt.py
./scripts/seed-librechat-agents.sh

# Run the demo (CLI) — pass keys explicitly to be safe
docker compose --profile demo run --rm \
  -e LANGFUSE_PUBLIC_KEY=pk-lf-1234567890 -e LANGFUSE_SECRET_KEY=sk-lf-1234567890 \
  agentic-rag python main.py

# Tests
docker compose --profile demo run --rm agentic-rag \
  sh -c "pip install -r requirements-dev.txt && pytest"
```

## Service URLs / ports
| Service | URL / port | Notes |
|---|---|---|
| Agentic RAG API | http://localhost:8006 | `/health`, `POST /query` |
| mcp-rag-retriever | :8007 (SSE :8000 internal) | `retrieve_kb`, `list_documents` |
| clickhouse-vectors | host :8125 → 8123 | user `agentic` / pw `agentic123`, db `agentic_rag` |
| LibreChat | http://localhost:3080 | agent: "Agentic RAG Assistant" |
| Langfuse | http://localhost:3001 | demo@example.com / demodemo1! · keys pk-lf-1234567890 / sk-lf-1234567890 |

## Gotchas (will bite you otherwise)
- **Shell Langfuse keys override `.env`** in docker compose → 401 on trace export. `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY` first, or pass `-e ...` explicitly. (Affects all demo apps.)
- **Two ClickHouse instances on purpose**: Langfuse's is 25.8 (untouched); RAG vectors use a dedicated 26.3.
- **Port 8124 was taken** by another project → vectors use **8125**.
- **LibreChat is v0.8.6** (rebuilt from `librechat-dev:latest`). `langfuse-prompts` needs top-level `requiresOAuth: false` in `librechat.yaml` (not nested under `oauth:`).
- **librechat-api currently shows `unhealthy`** in `docker ps` — likely a healthcheck flap (MCP tools were loading fine). Verify with the MCP tools API / a chat before demoing; restart `docker compose restart api` if needed.
- **Intentionally uncommitted:** `text-to-sql/sql_pipeline.py` (pre-existing) and `.claude/settings.local.json` (local). Decide before merge.

## Key files
```
agentic-rag/graph.py            # CRAG StateGraph, nodes, edges, _llm timeout
agentic-rag/clickhouse_store.py # native vector search (HNSW cosineDistance)
agentic-rag/ingest.py           # chunk+embed vector-rag corpus -> ClickHouse
agentic-rag/langfuse_config.py  # typed observations, scores, get_prompt
agentic-rag/tests/              # 15 offline tests
mcp-rag-retriever/server.py     # MCP tool exposing retrieve_kb
scripts/seed-langfuse-prompt.py # creates agentic-rag-generation v1+v2(production)
scripts/seed-librechat-agents.sh# + "Agentic RAG Assistant" agent
docs/AGENTIC_RAG_ARCHITECTURE.md / *.svg / *.png
docs/AGENTIC_RAG_DEMO_RUNBOOK.md
```
