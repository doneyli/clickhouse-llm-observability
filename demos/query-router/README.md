# Query Router — front-door classification and dispatch with a confidence gate, fallback, human escalation and router-accuracy evals

A thin front door that **classifies** an incoming question and **dispatches** it over HTTP
to exactly one of three existing demos, each a specialized handler, plus an in-process
fallback with confidence gating and human escalation. Routing is the star: the router's
decision is a first-class, scorable Langfuse observation, not a log line. It is the
routing pattern (Pattern 2) of the agentic-pattern demos.

The questions are about ClickHouse public datasets and the docs corpus the handler demos
ship. There is no company in the story; the router history and the dataset are synthetic.
Routing also exists inside `agentic-rag` (`route_node`) and `brand-promo-multi-agent`
(`_classify_intent_node`), but those walkthroughs are about something else. This demo puts
the router's confidence, its misroute scores and a router experiment on the marquee.

**The question it answers:** when a classifier in front of your specialists gets it wrong,
or isn't sure, how do you see it, measure it and fix it?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Datasets · Experiment · Evaluate |
| **Stack** | Python · Anthropic SDK · FastAPI · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 15 min · short path 6 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 15 minutes, 6-minute short path)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Router decision as a scorable object | The `route-query` generation: `{route, confidence, rationale}` in output, `metadata.route`, linked to the managed `query-router-classifier` prompt, with a runtime `router_confidence` score (`router.py`) |
| Specialized handlers | `analytics_sql` → text-to-sql, `docs_simple` → vector-rag (the cheap tier), `docs_complex` → agentic-rag (the expensive self-correcting tier) (`handlers.py`) |
| Distributed trace nesting | One trace holds the router's spans and the chosen handler's own subtree, joined across processes with `trace_context` (Act 1) |
| Confidence gating and fallback | Low confidence, out of scope, malformed output, an unknown route or a handler that is down all divert to `fallback-handler` (Act 2) |
| Human escalation | An `escalate-to-human` event with a machine-readable `reason` (Act 2) |
| Misroute curation | `routing_correct` (BOOLEAN) recorded after the fact as a score, not a tag (Act 3) |
| Monitoring | A Router Ops dashboard over 14 days of seeded history: route drift, fallback rate, confidence, misroute rate (Act 4) |
| Router-accuracy loop | The `query-router-accuracy` dataset and an experiment that varies only the router, scored by a code evaluator and a categorical judge (Act 5) |

## Who it's for

Solutions architects showing routing (classification and dispatch) and the full AI
engineering loop on the cheapest surface there is, a one-call classifier. Pick it when the
customer asks "where does a classifier decide something in my stack with no trace of why?"
or "what happens when the classifier isn't sure?". For routing inside a single agent graph,
use `agentic-rag`; for parallel fan-out, use `support-triage-parallel`.

## What runs where

```
 you ── docker compose run query-router python main.py   (or POST :8008/query)
          │  server.run(): route-and-dispatch span
          ├─ classify()  ─ route-query generation (ROUTER_MODEL, claude-haiku-4-5)
          └─ dispatch()  ─ dispatch-<route> agent ── HTTP POST /query + trace_context
                 ├─ analytics_sql ─▶ text-to-sql  :8002  (MCP ClickHouse catalog)
                 ├─ docs_simple   ─▶ vector-rag   :8003  (single-shot RAG)
                 ├─ docs_complex  ─▶ agentic-rag  :8006  (CRAG graph, ClickHouse vectors)
                 └─ fallback      ─  in-process fallback-handler + escalate-to-human
          ▼
 Langfuse (self-hosted :3001) project `LLM Observability Demo`
```

| Process | Host port | In the compose network |
|---|---|---|
| `query-router` (FastAPI `/health`, `/query`) | `8008` (`QUERY_ROUTER_PORT`) | `query-router:8000` |
| `text-to-sql` | `8002` (`TEXT_TO_SQL_PORT`) | `http://text-to-sql:8000` |
| `vector-rag` | `8003` (`VECTOR_RAG_PORT`) | `http://vector-rag:8000` |
| `agentic-rag` | `8006` (`AGENTIC_RAG_PORT`) | `http://agentic-rag:8000` |

**What lands in Langfuse.** Every live run has a `route-and-dispatch` span with a
`route-query` generation under it, then either `dispatch-<route>` with the handler's
subtree nested beneath it, or `fallback-handler` and `escalate-to-human`. A fallback trace
is named `route-and-dispatch`. A dispatched trace usually ends up named after the handler
(`text-to-sql-handler`, `vector-rag-handler`, `agentic-rag-handler`), because the handler's
spans reach Langfuse first; `main.py` prints this at the end of a batch. Session:
`query-router-<8 hex>`, one per `main.py` invocation (or per `/query` call without a
`session_id`). Tags: `query-router`, `demo`. Score: `router_confidence` on `route-query`.

## Set up from scratch

Prerequisites: Docker, Python 3.9+ on the host, and the root stack set up with
`./setup.sh`. Run these from the repo root:

```bash
./setup.sh                                                   # the root stack and its .env (idempotent)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
docker compose --profile langfuse --profile demo up -d       # router + its three handlers
docker compose --profile demo run --rm agentic-rag python ingest.py   # one time: agentic-rag's vector index
python demos/query-router/scripts/seed_all.py                # idempotent: created / updated / unchanged per item
docker compose --profile demo run --rm query-router python main.py    # 10 questions, live model calls
python demos/query-router/scripts/verify_demo.py             # should print READY
```

The root `.env` is the only source of `LANGFUSE_*`. Compose resolves `${...}` from the
shell first, so unset shell-exported keys before running; `verify_demo.py` and
`seed_all.py` ignore them. `./setup.sh` seeds this demo through `ensure_query_router_seed`,
which calls the four root `seed-router-*` scripts directly rather than `seed_all.py`; the
prompt and dataset seeders append on every run, so prefer `seed_all.py` afterwards.

`seed_all.py` makes no model calls. It adds a prompt version only for a label whose text
or config drifted, creates or updates dataset items by question, seeds 300 backdated router
traces only when either week of history is thin, and re-applies the code evaluators and the
`route-plausibility` judge (Postgres upserts, self-hosted only; `--skip-evaluators` skips
them). `--dry-run` reports without writing.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `python demos/query-router/scripts/seed_all.py`, then the batch run | History is backdated from the day it was seeded and ages out; a fresh batch gives Act 1 a dispatched trace per route |
| The day before | Build the Router Ops dashboard (Act 4 of the script lists the four widgets) | It is built by hand, once per project |
| 30 minutes before | `python demos/query-router/scripts/verify_demo.py --links` | READY, and the link each act opens |
| 5 minutes before | Open the links; restart `vector-rag` if a rehearsal of Act 2 stopped it | Traces, the dashboard, the prompt |

## Run it

```bash
docker compose --profile demo run --rm query-router python main.py                 # 10 questions: every route + edge cases
docker compose --profile demo run --rm query-router python main.py --interactive   # type your own
curl -s localhost:8008/health | python3 -m json.tool                                # router + handler reachability
ROUTER_FAULT=sql-blindness docker compose --profile demo run --rm -e ROUTER_FAULT query-router \
  python main.py --interactive                                                      # Act 3: seeded misroute
docker compose --profile demo run --rm query-router \
  python scripts/score_misroute.py <trace_id> <route_query_observation_id> --expected analytics_sql
```

The batch and interactive runs make live model calls: the router on `ROUTER_MODEL`, the
handlers on `ANTHROPIC_MODEL`, the fallback on `ROUTER_FALLBACK_MODEL`. Their cost and time
have not been measured.

**What you see in Langfuse:** one trace per question. Dispatched questions sit in a
`*-handler` trace that holds `route-query` (with `router_confidence`), `dispatch-<route>`
and the handler's own spans and scores. Fallback questions sit in a `route-and-dispatch`
trace with `fallback-handler` and an `escalate-to-human` event whose input carries the
reason and the confidence.

## Evaluation

| What | Command | Notes |
|---|---|---|
| `query-router-accuracy` dataset (30 items) | `python demos/query-router/scripts/seed_all.py` | Question → expected route; misroutes pinned by `score_misroute.py --add-to-dataset` are kept |
| `route-match` code evaluator | `./scripts/seed-code-evaluators.sh` (also run by `seed_all.py`) | `evaluators/route-match.ts`, BOOLEAN, on experiment runs of the dataset |
| `route-plausibility` judge | `./scripts/seed-router-judge.sh` (also run by `seed_all.py`) | Categorical, observation-level on `route-query`, samples 25% of new traffic |
| `routing_correct` | `scripts/score_misroute.py` (in the container, as above) | BOOLEAN, post-hoc, on the `route-query` observation |
| Router experiment | `python scripts/run-router-experiment.py --sample 30` | 3 configs (prompt baseline/production × Haiku/Sonnet); classification only, 90 router calls; `--e2e` dispatches to live handlers |
| Gate | `python scripts/run-router-experiment.py --sample 30 --ci` | Exits 1 when any run's `avg_route_accuracy` is below `ROUTER_CI_THRESHOLD` (0.90); no workflow runs it |

The experiment imports `router.py`, so it needs this demo's requirements on the host
(`pip install -r demos/query-router/requirements.txt`, Python 3.10+ for Langfuse SDK v4).

## Route taxonomy

| Route | Handler | Specialization the router protects |
|---|---|---|
| `analytics_sql` | text-to-sql | Live numbers over ClickHouse public datasets |
| `docs_simple` | vector-rag | Cheap, fast single-shot doc lookup |
| `docs_complex` | agentic-rag | Multi-part, comparative or verification-worthy questions |
| `fallback` | in-process | Out of scope, below `ROUTER_CONFIDENCE_THRESHOLD`, malformed output, unknown route, or handler down |

## Graceful degradation

Everything runs without Langfuse keys (`langfuse_config.py` no-ops) and without the
managed prompt (`router.py` ships a local template). Without the prompt, though, the router
classifies much worse: `setup.sh` records a run where every question went out of scope. If
a handler is down, its route degrades to the fallback and an escalation instead of an
error; Act 2 shows this on purpose.

## Configuration

Set these in the root `.env`; the container gets all of it through `env_file`.

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project; tracing no-ops without them |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where the host-side scripts read and seed; the container uses `LANGFUSE_INTERNAL_URL` (default `http://langfuse-web:3000`) |
| `ANTHROPIC_API_KEY` | blank | Router, fallback and handler model calls |
| `ROUTER_MODEL` | `claude-haiku-4-5` | The classifier model; also written into the seeded prompt config |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.70` | Below this, a valid route still goes to the fallback (`low_confidence`) |
| `ROUTER_TEMPERATURE` | `0.0` | Classifier temperature |
| `ROUTER_FAULT` | blank | `sql-blindness` hides the `analytics_sql` route from the prompt (Act 3) |
| `ROUTER_PROMPT_NAME` | `query-router-classifier` | Managed prompt the router fetches by the `production` label |
| `ROUTER_FALLBACK_MODEL` | `claude-haiku-4-5` | Model for `fallback-handler` |
| `HANDLER_TIMEOUT` | `180` | Seconds before a handler call counts as unreachable |
| `TEXT_TO_SQL_URL`, `VECTOR_RAG_URL`, `AGENTIC_RAG_URL` | `http://<service>:8000` | The handler registry; compose sets them |
| `QUERY_ROUTER_PORT`, `TEXT_TO_SQL_PORT`, `VECTOR_RAG_PORT`, `AGENTIC_RAG_PORT` | `8008`, `8002`, `8003`, `8006` | Host ports; `verify_demo.py` checks `/health` on them |
| `ROUTER_DATASET_NAME` | `query-router-accuracy` | Dataset the seeders and the experiment use |
| `ROUTER_CI_THRESHOLD` | `0.90` | The experiment's `--ci` gate |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/query-router/
├── demo.yaml              manifest: profile, coverage, commands (feeds the index)
├── README.md              this file
├── DEMO_SCRIPT.md         the talk track, act by act
├── Dockerfile             image for the root compose `demo` profile (uvicorn server:app)
├── router.py              classify -> {route, confidence, rationale}; the confidence gate
├── handlers.py            route registry, HTTP dispatch, in-process fallback, escalation event
├── server.py              FastAPI /health and /query; run() = classify -> dispatch under one root span
├── main.py                CLI batch (10 questions) and --interactive
├── langfuse_config.py     observe(), scores, managed prompt, trace attributes; no-ops without keys
├── scripts/
│   ├── seed_all.py        the one seed entry point (idempotent, reconciles drift)
│   ├── verify_demo.py     readiness check: READY or NOT READY
│   └── score_misroute.py  post-hoc routing_correct score, optional dataset pin
├── tests/                 no services, no model calls
└── requirements.txt · requirements-dev.txt · pytest.ini
```

Root files it uses: `scripts/seed-router-prompt.py`, `scripts/seed-router-dataset.py`,
`scripts/seed-router-history.py`, `scripts/seed-router-judge.sh`,
`scripts/seed-code-evaluators.sh`, `scripts/run-router-experiment.py`,
`evaluators/route-match.ts`.

## Tests

```bash
cd demos/query-router
pip install -r requirements-dev.txt      # Python 3.10+ (Langfuse SDK v4)
python -m pytest -q                      # no services, no model calls, under a second
```

They cover the classifier's parsing and gating, dispatch, fallback and escalation, the
FastAPI endpoints (Anthropic and HTTP stubbed), `verify_demo.py`'s checks, exit code,
`.env` rule and Observations v2 → v1 fallback against a fake Langfuse, and `seed_all.py`'s
reconcile decisions for prompts, dataset items and history.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Every question goes to the fallback with `out_of_scope` | The managed prompt is missing, so the router uses its local template. Run `seed_all.py` and check `verify_demo.py` passes the prompt checks |
| `escalation_reason=handler_unreachable` | That handler is down or slow: `docker compose --profile demo up -d`, then `curl -s localhost:8008/health` |
| `agentic-rag` route answers nothing useful | Its vector index was never built: `docker compose --profile demo run --rm agentic-rag python ingest.py` |
| `keys map to project '…' (want 'LLM Observability Demo')` | The root `.env` holds another project's keys; shell exports are ignored |
| Filtering traces on `route-and-dispatch` shows only fallbacks | Dispatched traces are named after the handler; filter `*-handler`, or open the `--links` output |
| `router history: … (want 100+ each)` | History ages out of the 14-day window; run `seed_all.py` again |
| Act 4's Metrics API call returns 404 | `/api/public/v2/metrics` needs Langfuse server v4; the self-hosted stack runs 3.x |
| `route-plausibility` scores never appear | The judge scores only new traffic, 25% sampled, and needs a default eval model: run the batch again and check `./scripts/seed-router-judge.sh` output |
| `latest run has 0 observation(s)` | The run landed before its observations. Wait 30 seconds and verify again |

## Known limits

- `confidence` is self-reported by the model and calibrated only by the prompt. It is a
  useful gate, not ground truth; the dataset and experiment are the safety net.
- The router does not read the managed prompt's `config`: the model and temperature come
  from `ROUTER_MODEL` and `ROUTER_TEMPERATURE`. Moving the `production` label changes the
  prompt text only.
- The default experiment is classification only. It measures routing accuracy, not answer
  quality; `--e2e` dispatches to the live handlers for that.
- When agentic-rag runs as a handler, its `groundedness` score lands on the router's trace.
- The Router Ops dashboard is built by hand, and `verify_demo.py` does not check it. The
  Metrics API call in Act 4 needs a v4 server.
- The router history is synthetic: 300 traces with fixed route weights, a planted week-2
  drift and a misroute cluster, generated with no model calls.
- `./setup.sh` calls the appending seeders directly, so repeated setups leave duplicate
  prompt versions and dataset items. `seed_all.py` stops adding more but does not remove them.
- No CI workflow runs the `--ci` gate; it is a command the presenter runs.
- The cost and duration of a batch run have not been measured.
