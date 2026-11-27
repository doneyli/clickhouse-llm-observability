# Vector RAG — The naive-RAG baseline, fully traced, with ChromaDB retrieval and a Langfuse-managed generation prompt shipped by label

A textbook single-shot RAG pipeline: embed the question, pull the three nearest chunks from
an in-memory ChromaDB collection, and have Claude answer from them. It runs as a one-shot
container from the root stack and traces every answer to Langfuse through one LangChain
callback handler. The generation prompt lives in Langfuse and is fetched by the
`production` label, with an identical inline copy as the fallback.

There is no company in this demo. The corpus is ten short articles on ClickHouse and LLM
observability, written for the demo and kept in `documents.py`. It is deliberately the
naive baseline, the shape most teams' first RAG takes, and it sets up the self-correcting
[`agentic-rag`](../agentic-rag/) demo.

**The question it answers:** what does the minimum Langfuse integration buy a RAG pipeline,
and where does naive retrieve-and-stuff stop being enough?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Evaluate · Deploy |
| **Stack** | Python · LangChain · ChromaDB · FastAPI · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 10–12 min · short path 5 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (10–12 minutes, 5-minute short path)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Tracing with one LangChain callback handler | One `vector-rag` trace per question: the chain, its `ChatAnthropic` generation, tokens and cost (`langfuse_config.py`, `main.py`) |
| Prompt management | `vector-rag-generation`, served by the `production` label, linked to every generation, inline fallback when unseeded (`rag_pipeline.py`) |
| Shipping a prompt change without a deploy | Edit the prompt in Langfuse, move `production`, re-run: the next traces link the new version (script Act 2) |
| Deterministic evaluators at ingest | `output-present`, `structure-clean`, `response-length`, `credential-leak`, `leak-type` on every generation (script Act 3) |
| The limits of naive RAG | An out-of-corpus question in interactive mode: top-k always returns k chunks, and nothing grades them (script Act 4) |

## Who it's for

Teams starting on RAG, and anyone who needs the fundamentals before the advanced demos:
what a traced RAG answer looks like, how a managed prompt ships, what free mechanical
checks catch. Pick it as the opening for [`agentic-rag`](../agentic-rag/) or on its own for
a short slot. If the audience is past "what is RAG", go straight to `agentic-rag`, which
adds typed retrieval spans, graded retrieval and retries.

## What runs where

```
 you ── docker compose --profile demo run --rm vector-rag python main.py
          │  one-shot container `vector-rag` (Python 3.11, built from this folder, mounted at /app)
          │
          ├─ HuggingFaceEmbeddings all-MiniLM-L6-v2, CPU (cached in the `vector-rag-models` volume)
          ├─ ChromaDB in-process, collection `rag_demo`: 10 documents → 500-char chunks, rebuilt every run
          ├─ retrieve: top-3 by similarity, joined with `---`  (outside the traced chain)
          └─ ChatAnthropic `claude-sonnet-4-6` ── prompt `vector-rag-generation` (label production)
          │
          ▼  CallbackHandler → LANGFUSE_INTERNAL_URL (default http://langfuse-web:3000)
 Langfuse project `LLM Observability Demo`  (self-hosted UI on http://localhost:3001)
   trace `vector-rag` per question · tags `vector-rag`, `demo` · no session id
   scores from the code evaluators: output-present, structure-clean, response-length, credential-leak, leak-type
```

`run` publishes no ports. The service's default command is a FastAPI server
(`server.py`, `uvicorn` on container port 8000, published on `VECTOR_RAG_PORT`, default
8003), which only runs if you start the service with `up`. The script does not use it; the
[`query-router`](../query-router/) demo calls it at `http://vector-rag:8000` for its
`docs_simple` tier. Called on its own, `POST /query` writes the same `vector-rag` trace;
called with a `trace_context`, it nests a `vector-rag-handler` agent observation under the
caller's trace.

## Set up from scratch

Prerequisites: Docker, an Anthropic API key, and the root stack set up with `./setup.sh`.
Run these from the repo root:

```bash
./setup.sh                          # the root stack, its .env and the code evaluators (idempotent)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
set -a; . ./.env; set +a            # the prompt seeder reads its keys from the environment, not from .env
python scripts/seed-app-prompts.py  # idempotent: a new version only when the text differs
docker compose --profile demo build vector-rag
docker compose --profile demo run --rm vector-rag python main.py   # ten questions, ten traces; the first run downloads the embedding model
python demos/vector-rag/scripts/verify_demo.py                     # should print READY
```

The root `.env` is the only source of `LANGFUSE_*`. The container reads the keys from it
as an `env_file`, so a shell export cannot redirect its traces, and `verify_demo.py` drops
shell-exported `LANGFUSE_*` before it loads `.env`. `seed-app-prompts.py` does read the
shell, which is why the block above unsets the keys and then loads `.env`. `./setup.sh`
does not run `seed-app-prompts.py`; without it the app runs on the inline fallback and its
generations carry no prompt link.

The ten-question run makes ten Claude calls. Its time and cost have not been measured yet.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | Seed the prompt and run the batch, as in the block above | The first run downloads the embedding model; do it now, not live |
| 30 minutes before | `python demos/vector-rag/scripts/verify_demo.py --links` | Prints READY and the link each act opens |
| 5 minutes before | Open the links it printed; log in as `demo@example.com` / `demodemo1!` | Act 1 needs the trace, Act 2 the `vector-rag-generation` prompt |

## Run it

```bash
docker compose --profile demo run --rm vector-rag python main.py                 # the ten questions in main.py
docker compose --profile demo run --rm vector-rag python main.py --interactive   # ask your own; quit with "quit"
```

**What you see in Langfuse:** one `vector-rag` trace per question, tagged `vector-rag` and
`demo`. The CallbackHandler records the LangChain chain (metadata `purpose:
rag_generation`): the prompt template, a `ChatAnthropic` generation and the output parser.
The generation's input holds the three retrieved chunks above the question; it shows the
tokens, cost, latency and model, and links the `vector-rag-generation` version that
produced it. The code evaluators add their scores shortly after the trace lands.
Retrieval is not its own span.

## Evaluation

| What | Command | Notes |
|---|---|---|
| `response-structure-check`: `output-present`, `structure-clean`, `response-length` | `./scripts/seed-code-evaluators.sh` | Deterministic TypeScript run by Langfuse on every GENERATION of `text-to-sql` and `vector-rag` traces; no model cost. `./setup.sh` seeds it |
| `credential-leak-guard`: `credential-leak`, `leak-type` | `./scripts/seed-code-evaluators.sh` | Runs on every GENERATION in the project |
| LLM-as-a-judge | none scoped to `vector-rag` | The stack's managed judges target other traces. Add one scoped to `vector-rag` in the UI for relevance or hallucination |

There is no dataset or experiment for this demo. See
[`docs/CODE_EVALUATORS.md`](../../docs/CODE_EVALUATORS.md) for the evaluators.

## Configuration

All from the root `.env`; `.env.example` at the repo root lists them.

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | blank | Key for the generation call |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Generation model (`RAGConfig.model_name`); the prompt's own `config` is not read |
| `TEMPERATURE` | `0.7` | Generation temperature |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project. Without both, the app runs untraced on the inline prompt |
| `LANGFUSE_INTERNAL_URL` | `http://langfuse-web:3000` | Where the container sends traces; compose sets both `LANGFUSE_BASE_URL` and `LANGFUSE_HOST` to it. Cloud mode sets it to the cloud URL |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where `verify_demo.py` reads (it falls back to `LANGFUSE_HOST`) |
| `LANGFUSE_HOST` | `http://localhost:3001` | Where `seed-app-prompts.py` writes; `setup.sh` derives it from `LANGFUSE_BASE_URL` |
| `VECTOR_RAG_PORT` | `8003` | Host port of the API server, when it is started with `up` |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/vector-rag/
├── demo.yaml               manifest: profile, coverage, commands (feeds the index)
├── README.md               this file
├── DEMO_SCRIPT.md          the talk track, act by act
├── Dockerfile              image for the root compose `vector-rag` service (`demo` profile)
├── main.py                 the CLI: ten demo questions, or --interactive
├── server.py               FastAPI /health and /query, used by the query-router demo
├── rag_pipeline.py         chunk, embed, index, retrieve top-3, generate; managed prompt with fallback
├── documents.py            the ten-article corpus
├── langfuse_config.py      on/off switch, CallbackHandler, trace name and tags, prompt fetch
├── scripts/
│   └── verify_demo.py      readiness check: READY or NOT READY
├── tests/                  no services, no model calls, no heavy dependencies
├── requirements.txt        app dependencies (LangChain, ChromaDB, sentence-transformers, torch via it)
├── requirements-dev.txt    test dependencies: pytest only
└── pytest.ini
```

The seed lives at the repo root: `scripts/seed-app-prompts.py` seeds this demo's prompt
together with the `text-to-sql` prompts.

## Tests

```bash
cd demos/vector-rag
pip install -r requirements-dev.txt
python -m pytest -q                    # no services, no model calls, under a second
```

They cover the corpus and its metadata, the inline fallback matching the seeded prompt
text, prompt choice (managed, missing, unusable), chunking and indexing, retrieval and the
chain plumbing, the Langfuse on/off switch and trace naming, and `verify_demo.py`'s checks
on both the v2 and the v1 observations path. LangChain, ChromaDB and the SDK are replaced
by small fakes, so nothing heavier than pytest is installed.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `keys map to project '…' (want 'LLM Observability Demo')` | The root `.env` holds another project's keys. Fix `.env`; shell exports are ignored by the check |
| `keys authenticate …: HTTP 401` | Wrong key pair, or `LANGFUSE_BASE_URL` points at another host |
| `LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set (source .env first)` | The prompt seeder reads the shell. `set -a; . ./.env; set +a`, then seed again |
| `Langfuse get_prompt('vector-rag-generation') unavailable, using local fallback` | The prompt is not seeded, or Langfuse is down. Seed it; the run still answers, unlinked |
| `latest run's generation is linked …` fails | That run used the fallback. Seed the prompt, then run again |
| `latest run's scores: none` | The code evaluators are not seeded (`./scripts/seed-code-evaluators.sh`), or the worker is stuck: `docker compose restart langfuse-worker` |
| The first run sits at `Loading embedding model` | It is downloading `all-MiniLM-L6-v2` into the `vector-rag-models` volume. Later runs reuse it |
| `curl localhost:8003` is refused | `run` publishes no ports and `setup.sh` does not start the service. `docker compose --profile demo up -d vector-rag`, if you need the API |

## Known limits

- Retrieval runs outside the instrumented chain, so it is not a span: you see the retrieved
  chunks in the generation's input, not a timed or scored retrieval step.
- Nothing grades retrieval or groundedness, and nothing retries. Top-k always returns three
  chunks, even for a question the corpus cannot answer.
- Traces carry no session id. `langfuse_session()` exists but `main.py` does not use it,
  and `server.py` echoes `session_id` without attaching it.
- ChromaDB is in-memory and re-indexed on every run, over ten short documents. It shows the
  shape, not retrieval quality or scale.
- The app takes its model from `ANTHROPIC_MODEL`; the model in the managed prompt's
  `config` is not used.
- The seeded prompt and the inline fallback are kept in sync by hand. A test fails when
  they drift.
- No dataset, experiment or CI gate. The script's minutes have not been measured in a
  rehearsal against this README yet.
