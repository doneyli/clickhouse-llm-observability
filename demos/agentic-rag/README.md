# Agentic RAG — Self-correcting RAG (CRAG) on ClickHouse-native vector search with LangGraph, fully scored in Langfuse

A LangGraph agent that answers questions about ClickHouse, RAG and LLM observability from
a knowledge base stored in ClickHouse's native vector index. It routes each question, grades
its own retrieval, rewrites the query and retrieves again when the grade fails, then checks
that the answer is grounded. Every step is a typed Langfuse observation, and the grades are
Langfuse scores.

The same graph runs from the CLI, over HTTP and from a LibreChat agent, and each path
emits the same scored `agentic-rag` trace. The knowledge base is the vector-rag demo's ten
documents, so the two demos answer from the same corpus. No customer data is involved.

**The question it answers:** can a RAG pipeline notice its own bad retrieval and recover,
and can you see and measure that, without a separate vector database?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Experiment · Deploy |
| **Stack** | Python · LangGraph · LangChain · ClickHouse · MCP · FastAPI · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 20–25 min · short path 8–10 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 20–25 minutes, 8–10-minute short path)
- **Design record:** [docs/AGENTIC_RAG_ARCHITECTURE.md](../../docs/AGENTIC_RAG_ARCHITECTURE.md) (architecture), [docs/AGENTIC_RAG_DEMO_RUNBOOK.md](../../docs/AGENTIC_RAG_DEMO_RUNBOOK.md) (screen-by-screen runbook), [SESSION_GROUPING_SPEC.md](SESSION_GROUPING_SPEC.md) (LibreChat session grouping)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| ClickHouse native vector search | `agentic_rag.kb_chunks` with a `vector_similarity('hnsw', 'cosineDistance', 384)` index (`clickhouse_store.py`), queried with `ORDER BY cosineDistance(...)` |
| Agentic self-correction | `route → retrieve → grade → (rewrite → retrieve) → generate → reflect`, bounded to one rewrite and one regeneration (`graph.py`) |
| Agent Graph in Langfuse | Typed observations `agent`, `retriever`, `evaluator`, `tool`, `generation` plus the LangChain callback handler (`langfuse_config.py`) |
| Scores that steer the run | `retrieval_relevance` on each `grade-relevance` observation (so a self-correcting run has 0 then 1) and `groundedness` on the trace |
| Independent managed judges | `faithfulness`, `context-relevance`, `answer-relevance` on the `generate` observation (`scripts/seed-agentic-rag-evaluators.sh` at the repo root) |
| Prompt management | `agentic-rag-generation`, fetched by the `production` label and linked to each generation |
| Sessions | One session per CLI run; one per LibreChat conversation |
| Same loop, no code | LibreChat's **Agentic RAG Assistant** calls the `agentic_rag_answer` MCP tool, which runs this graph |

## Who it's for

Engineers building RAG who want retrieval quality they can measure, and ClickHouse users
weighing a separate vector database. Pick it over [vector-rag](../vector-rag/) when the
audience asks "what happens when retrieval is wrong?"; vector-rag is the single-shot
baseline on the same corpus. Pick [query-router](../query-router/) when the story is
routing between several demo backends, which includes this one.

## What runs where

```
 CLI: docker compose run agentic-rag python main.py ─┐
 HTTP: POST localhost:8006/query (server.py) ────────┤
 LibreChat :3080 ─ Agentic RAG Assistant              │
   └─ MCP rag-retriever ─ mcp-rag-retriever :8007 ────┘  agentic_rag_answer → POST agentic-rag:8000/query
                                                      ▼
                         graph.py  AgenticRAG.run()  (LangGraph, ChatAnthropic)
                            │ retrieve: embed (all-MiniLM-L6-v2) + ANN query
                            ▼
                         clickhouse-vectors :8125 (ClickHouse 26.3)  agentic_rag.kb_chunks
                            │ sql route: sql_tool.py → sql-clickhouse.clickhouse.com (public, read-only)
                            ▼  Langfuse SDK v4, flushed after every run
 Langfuse :3001 (self-hosted, server 3.221.1), project `LLM Observability Demo`
   trace `agentic-rag` · tags agentic-rag, demo
   session agentic-rag-<8 hex> per CLI run or per HTTP call without session_id;
           librechat-<conversation id> per LibreChat chat
   scores retrieval_relevance (per grade), groundedness (per run), + 3 managed judges
```

`docker-compose.yaml` defines `clickhouse-vectors`, `agentic-rag` (port `AGENTIC_RAG_PORT`,
default 8006) and `mcp-rag-retriever` (default 8007), all under the `demo` profile. The
agentic-rag container mounts `demos/vector-rag` at `/shared-corpus` for `ingest.py`. The
MCP server mounts this folder read-only for its raw `retrieve_kb` and `list_documents`
tools. Its `agentic_rag_answer` tool forwards the question to the agentic-rag service's
`/query` endpoint instead of running the graph itself (`mcp-rag-retriever/server.py`).
LibreChat sends `X-LibreChat-Conversation-Id` on every tool call (`librechat.yaml`), and
the tool turns it into the `librechat-<id>` session. `clickhouse-vectors` is separate from
Langfuse's own ClickHouse (25.8), as the [architecture doc](../../docs/AGENTIC_RAG_ARCHITECTURE.md#why-two-clickhouse-instances)
explains.

## Set up from scratch

Prerequisites: Docker, `jq`, an Anthropic API key, and the root stack set up with
`./setup.sh`. Run these from the repo root:

```bash
./setup.sh                          # the root stack and its .env (idempotent)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
docker compose --profile demo build agentic-rag mcp-rag-retriever
docker compose --profile demo up -d clickhouse-vectors agentic-rag mcp-rag-retriever
python demos/agentic-rag/scripts/seed_all.py      # idempotent, no model calls; ingest.py loads the embedding model
docker compose --profile demo run --rm agentic-rag python main.py   # 5 questions, 2-8 Claude calls each
python demos/agentic-rag/scripts/verify_demo.py   # should print READY; --short checks Acts 1-3 only
```

`seed_all.py` builds the vector index with `ingest.py`, reconciles the
`agentic-rag-generation` prompt, upserts the three managed judge rules and seeds the
LibreChat agents. Pass `--dry-run` to see what it would change, and `--skip-ingest` or
`--skip-librechat` to leave those parts alone. It exits 1 when any step fails, including
an agent left without its MCP tools. `./setup.sh` runs only the LibreChat agent seeding,
not the rest of this demo's seed.

The root `.env` is the only source of `LANGFUSE_*`. Compose resolves `${...}` from the
shell first, so unset shell-exported keys before running; `verify_demo.py` and
`seed_all.py` ignore them.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `python demos/agentic-rag/scripts/seed_all.py`, then `docker compose --profile demo run --rm agentic-rag python main.py` | The index, prompt and judges match the code, and fresh scored traces exist |
| 30 minutes before | Run Act 2's self-correction command from [DEMO_SCRIPT.md](DEMO_SCRIPT.md), wait a minute for the judges, then `python demos/agentic-rag/scripts/verify_demo.py --links` (add `--short` for the short path) | A pinned 0-then-1 trace, READY, and the link each act opens. By default every check the full run needs is required, Act 3b and Act 4 included; `--short` lets those two only warn |
| 5 minutes before | Open the links it printed, a terminal, LibreChat and the architecture diagram | Act 1 starts in the terminal; Act 3 needs the pinned trace |

## Run it

```bash
docker compose --profile demo run --rm agentic-rag python main.py                 # the 5 questions in main.py, one session
docker compose --profile demo run --rm agentic-rag python main.py --interactive   # your own questions, one session
curl -s localhost:8006/query -H 'content-type: application/json' \
  -d '{"question":"What vector databases exist and how does ClickHouse compare?"}'  # one run over HTTP
```

Each run prints its route, step log and grounded flag. [DEMO_QUESTIONS.md](DEMO_QUESTIONS.md)
lists tested questions per route and the one that reliably triggers self-correction. In
LibreChat, pick the **Agentic RAG Assistant** agent and ask a knowledge question.

**What you see in Langfuse:** one `agentic-rag` trace per question. Knowledge-base runs
contain `route`, `retrieve`, `grade-relevance`, `generate` and `reflect-groundedness`,
plus a `rewrite-query` and a second `retrieve` / `grade-relevance` pair when the first
grade fails. The LangGraph handler draws them as an Agent Graph. `retrieval_relevance` sits
on each grade, `groundedness` on the trace, and the judges' scores on `generate`, which
also links the prompt version. Direct-route answers are named `generate-direct`, skip
reflection and carry no scores. The last question in `main.py` is one of these.

## Evaluation

| What | Command | Notes |
|---|---|---|
| In-graph grades: `retrieval_relevance`, `groundedness` | Any run | LLM calls inside the graph; they decide whether to rewrite or regenerate |
| Managed judges: `faithfulness`, `context-relevance`, `answer-relevance` | `./scripts/seed-agentic-rag-evaluators.sh` (run by `seed_all.py`) | Observation-level rules on `generate` with tag `agentic-rag`; score new traces only, about a minute after the run; judge model `LANGFUSE_EVAL_MODEL` |
| Self-grade vs judge comparison | Langfuse → Scores, or the trace | Pairs: `groundedness` ↔ `faithfulness`, `retrieval_relevance` ↔ `context-relevance` |

There is no dataset or offline experiment for this demo yet.

## Trace shape

- **Typed observations.** `lf.observe(name, as_type=...)` in `langfuse_config.py` wraps
  `start_as_current_observation`. The `route` and `rewrite-query` observations are
  `agent`, `retrieve` is a `retriever`, `grade-relevance` and `reflect-groundedness` are
  `evaluator`, `sql-tool` is a `tool` and `generate` is a `generation`. The
  ChatAnthropic calls inside them come from the callback handler.
- **No Langfuse keys, no tracing.** Every helper is a no-op without keys, so the agent
  still runs.
- **Behind the query-router.** When `/query` receives a `trace_context`, the graph runs
  under an `agentic-rag-handler` observation in the router's trace instead of opening an
  `agentic-rag` trace, and `groundedness` lands on the router's trace.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where the host-side scripts read and seed; the container uses `LANGFUSE_INTERNAL_URL` |
| `LANGFUSE_INTERNAL_URL` | `http://langfuse-web:3000` | Where the container sends traces (compose sets both URL variables from it) |
| `DEPLOY_MODE` | `self-hosted` | `cloud` skips the Postgres judge seeding and its check |
| `ANTHROPIC_API_KEY` | blank | Required for every graph node |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model for route, grade, rewrite, SQL, generate and reflect; also the prompt's `config.model` |
| `TEMPERATURE` | `0.7` | Model temperature |
| `LLM_TIMEOUT`, `LLM_MAX_RETRIES` | `45`, `2` | Per-call timeout in seconds and retries |
| `GEN_PROMPT_NAME` | `agentic-rag-generation` | Langfuse prompt the `generate` node fetches; a local template is the fallback |
| `EMBED_MODEL` | `all-MiniLM-L6-v2` | Embedding model (384 dimensions, matching the index) |
| `LANGFUSE_EVAL_MODEL` | `claude-haiku-4-5-20251001` | Default eval model the judge seeder sets when the project has none |
| `AGENTIC_RAG_PORT`, `MCP_RAG_RETRIEVER_PORT`, `CLICKHOUSE_VECTORS_HTTP_PORT` | `8006`, `8007`, `8125` | Host ports; the verify and seed scripts read them too |
| `CH_VECTORS_HOST`, `CH_VECTORS_PORT`, `CH_VECTORS_DB`, `CH_VECTORS_USER`, `CH_VECTORS_PASSWORD` | set in compose (`clickhouse-vectors`, `8123`, `agentic_rag`, `agentic`, `agentic123`) | The vector store connection |
| `PUBLIC_CH_HOST`, `PUBLIC_CH_PORT`, `PUBLIC_CH_USER`, `PUBLIC_CH_PASSWORD` | `sql-clickhouse.clickhouse.com`, `443`, `demo`, blank | Target of the `sql` route |
| `LANGFUSE_POSTGRES_CONTAINER` | `langfuse-postgres` | Container the judge seeder writes to and `verify_demo.py` reads from |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/agentic-rag/
├── demo.yaml               manifest: profile, coverage, commands (feeds the index)
├── README.md               this file
├── DEMO_SCRIPT.md          the talk track, act by act
├── DEMO_QUESTIONS.md       tested questions per route, and the self-correction trigger
├── SESSION_GROUPING_SPEC.md  design record for LibreChat session grouping (implemented)
├── Dockerfile              image for the root compose `demo` profile (uvicorn server:app)
├── graph.py                the CRAG LangGraph: nodes, edges, scores, prompt fetch
├── clickhouse_store.py     schema with the HNSW index, ANN and keyword search
├── embeddings.py           all-MiniLM-L6-v2, L2-normalised
├── sql_tool.py             read-only SELECT against the public ClickHouse datasets
├── langfuse_config.py      typed observations, scores, prompt fetch, flush (SDK v4)
├── ingest.py               chunks the vector-rag corpus into kb_chunks (truncate + insert)
├── main.py                 CLI: the 5 demo questions, or --interactive
├── server.py               FastAPI: GET /health, POST /query
├── scripts/
│   ├── seed_all.py         the one seed entry point (idempotent, reconciles drift)
│   └── verify_demo.py      readiness check: READY or NOT READY
├── tests/                  no services, no model calls
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

## Tests

```bash
cd demos/agentic-rag
pip install -r requirements-dev.txt   # the runtime stack too, incl. sentence-transformers; minutes the first time
python -m pytest -q                   # no services, no model calls, a few seconds
```

They cover the graph's paths with the model, embeddings and store mocked (rewrite and
regeneration stop at their caps), the SQL tool's SELECT-only guard, and both scripts end to
end against `tests/fake_langfuse.py`: Langfuse v2 and v1 reads with field projection, cursor
pages and strict parameters, ClickHouse, the services and Postgres, plus hostile shell keys,
partial `.env` files and staged read and write failures.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `keys map to project '…' (want 'LLM Observability Demo')` or traces "missing" | Shell-exported keys for another project. `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL` and re-run |
| `LANGFUSE_BASE_URL and the service ports are well-formed` fails, or `env file … not readable` | A malformed value in the root `.env` (a bad host or port, no `http://`), or a file you cannot read. Both scripts stop before any request; fix `.env` and re-run |
| `agentic_rag.kb_chunks holds 0 chunk(s)` or the table is missing | The index was never built or the volume was wiped: `python demos/agentic-rag/scripts/seed_all.py` |
| `a scored 'agentic-rag' run` fails | No knowledge-base run in 7 days. Run `main.py`; a direct-route question alone does not count |
| Empty Graph tab | The trace is still ingesting. Wait about 20 seconds and refresh |
| `judge rules not readable from Postgres` | `verify_demo.py` reads the rules with `docker exec langfuse-postgres psql`. Start the `langfuse` profile and make sure your user can run `docker exec` |
| No judge scores | Rules seeded after the run (they score new traces only), or BLOCKED because the project has no default eval model. Re-run `./setup.sh` and `seed_all.py`, then ask again |
| LibreChat agent answers without calling `agentic_rag_answer`, or prints raw tool XML | `mcp-rag-retriever` or `agentic-rag` was down when the agent was seeded. Start both and re-run `./scripts/seed-librechat-agents.sh` |
| `agentic_rag_answer` returns "agentic-rag service unreachable" | `docker compose --profile demo up -d agentic-rag` |
| Traces accepted but never appear | The Langfuse worker's Redis connections went stale: `docker compose restart langfuse-worker` |

## Known limits

- The grades and the reflection are LLM calls, so a given question does not self-correct
  every time. Q3 in [DEMO_QUESTIONS.md](DEMO_QUESTIONS.md) is the designed case; pre-run it.
- The corpus is ten short documents. It proves the loop and the index path, not recall or
  latency at scale.
- The in-graph grades and the managed judges are both model judgements. There is no
  human-labelled dataset, no offline experiment and no CI gate for this demo.
- The judge rules are Postgres upserts coupled to the `langfuse:3` schema, and they score
  only traces created after they exist. On Langfuse Cloud you create them in the UI.
- In LibreChat, number questions go to the ClickHouse playground tools, not the graph's
  `sql` route; exercise that route from the CLI or `/query`.
- HTTP calls without a `session_id` each open a new session.
- `verify_demo.py` confirms LibreChat and the MCP server are up, but not that the agent has
  the `agentic_rag_answer` tool bound; that needs a LibreChat login. On the 3.221.1 server
  it reads observations through the v1 list endpoint, because Observations v2 returns 404
  there.
