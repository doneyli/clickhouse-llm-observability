# Text-to-SQL — Prompt chaining with gate checks over ClickHouse via MCP, with an opt-in generate-critique-refine loop grounded in EXPLAIN evidence

A natural-language data assistant over ClickHouse's public playground
(`sql.clickhouse.com`), built as a LangChain chain and traced to Langfuse. A question
goes through `analyze → [gate 1] → retrieve context → respond → [gate 2]`. Gate 1 is a
deterministic check that the analysis names a catalog database; Gate 2 combines a
fail-closed SQL-policy check with a Haiku grounding grader. Each gate retries once, then
Gate 1 aborts and Gate 2 escalates. `--refine` swaps the catalog lookup for a
generate → critique → refine loop whose critic sees a real `EXPLAIN` and one bounded,
read-only query.

There is no company persona. The data is the public, read-only ClickHouse playground:
real public datasets (UK property prices, NYC taxi, Stack Overflow, GitHub and others),
not synthetic data.

**The question it answers:** how do you stop an LLM that writes SQL from shipping a bad
answer, and see which step went wrong when it does?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Experiment · Evaluate · Deploy |
| **Stack** | Python · LangChain · MCP · ClickHouse · FastAPI · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 20 min · short path 6 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 20 minutes, 6-minute short path)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Tracing a multi-step LangChain chain with Langfuse SDK v4 | One `text-to-sql` trace per question: the root span, two generations, the `retrieve-context` MCP span (`sql_pipeline.py`) |
| Gate checks between chain steps | `gate-database-selection` and `gate-response-quality` spans, verdict in the span output (`gates.py`) |
| Bounded retry and two fail routings | The same span name per attempt, `metadata.attempt`; `gate-aborted` / `gate-escalated` boolean scores |
| Deterministic code evaluators at ingest | `gate-pass` on every gate span; `sql-risk`, `sql-read-only`, `credential-leak`, `structure-clean` on generations |
| Prompt management | Five prompts fetched by label, with local fallbacks, seeded by `scripts/seed_all.py` |
| Evaluator-optimizer loop | `--refine`: `generate-sql → gather-evidence → critique-sql` per iteration; `converged`, `iterations_to_accept`, `sql_quality_delta` scores |
| Experiments | The analysis prompt `production` vs `candidate` with the gates fixed; the refine loop's generator and critic variants |

## Who it's for

Data and platform teams being asked for "questions in plain English over our warehouse",
and anyone who needs to see guardrails *inside* an LLM pipeline rather than after it. It
is the repo's prompt-chaining demo. Pick `vector-rag` for retrieval over documents,
`agentic-rag` for a self-correcting graph, and `slow-query-tuner` for an agent that
changes SQL against a live ClickHouse.

## What runs where

```
 you ── docker compose --profile demo run --rm text-to-sql python main.py
          │  container `text-to-sql` (Python 3.11), the root .env, demos/text-to-sql mounted at /app
          ├── Anthropic API: claude-sonnet-4-6 (analysis, response, refine loop), claude-haiku-4-5 (gate grader)
          ├── mcp-clickhouse  (MCP over SSE; mcp-clickhouse:8000/sse inside, localhost:8001 on the host)
          │      └── sql-clickhouse.clickhouse.com   catalog for the retrieve-context step
          ├── --refine only: clickhouse-connect ── sql-clickhouse.clickhouse.com:443  EXPLAIN + one bounded SELECT
          ▼  one Langfuse client, flushed before exit
 Langfuse (self-hosted, langfuse-web:3000 inside, localhost:3001 on the host) · project `LLM Observability Demo`
   trace `text-to-sql` · no session or user · tags text-to-sql, demo (+ refine-loop, fault:<name>)
```

The compose service's own command is `uvicorn server:app`, which serves `POST /query` on
`localhost:8002` (`TEXT_TO_SQL_PORT`) for the `query-router` demo. `docker compose run
... python main.py` replaces that command, so this demo needs nothing on port 8002.

## Set up from scratch

Prerequisites: Docker and the root stack, set up with `./setup.sh` (it needs an
`ANTHROPIC_API_KEY`). Run these from the repo root:

```bash
./setup.sh                          # the root stack and its .env (idempotent)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
python demos/text-to-sql/scripts/seed_all.py       # idempotent: created / updated / unchanged per item
docker compose --profile demo run --rm text-to-sql python main.py   # 10 questions, one trace each
python demos/text-to-sql/scripts/verify_demo.py     # should print READY (read-only, no model calls)
```

The root `.env` is the only source of `LANGFUSE_*`. Compose resolves `${...}` from the
shell first, so unset shell-exported keys before running; `verify_demo.py` and
`seed_all.py` ignore them. `./setup.sh` runs `scripts/seed-code-evaluators.sh` but not
the rest of this demo's seed, so run `seed_all.py` yourself. It also seeds the
`vector-rag-generation` prompt, because the root prompt seeder covers both apps. The seed
and verify scripts are standard library only; nothing needs installing on the host.

`verify_demo.py` prints a verdict for the full presentation (Acts 1–5) and, on the line
above it, whether the short path (Opening, Acts 1–2, Close) is ready on its own. Datasets
count only ACTIVE items that match the seeded definition.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `python demos/text-to-sql/scripts/seed_all.py`, then `docker compose --profile demo run --rm text-to-sql python main.py` | Seeded prompts, evaluators and datasets match the code, and fresh traces exist |
| The day before, for Act 3 | `docker compose --profile demo run --rm -e REFINE_MODE=1 text-to-sql python main.py --refine` | Fresh refine-loop traces; needs egress to `sql-clickhouse.clickhouse.com:443` |
| Once | Create the `gate-pass` Monitor in the UI (the JSON is in the script's pre-flight) | Act 2 shows the gate-fail rate |
| 30 minutes before | `python demos/text-to-sql/scripts/verify_demo.py --links` | Prints READY and the link each act opens |
| 5 minutes before | Open the links it printed, plus the Prompts tab and a terminal | Act 1 opens the latest trace |

## Run it

```bash
docker compose --profile demo run --rm text-to-sql python main.py                  # 10 questions, one per public dataset
docker compose --profile demo run --rm text-to-sql python main.py --interactive    # type your own questions
docker compose --profile demo run --rm -e DEMO_FAULT=vague-analysis text-to-sql python main.py --interactive    # Gate 1 fails, retries, aborts
docker compose --profile demo run --rm -e DEMO_FAULT=destructive-sql text-to-sql python main.py --interactive   # Gate 2 fails closed, escalates
docker compose --profile demo run --rm -e REFINE_MODE=1 text-to-sql python main.py --refine                     # 5 refine-loop questions, executes bounded SQL
docker compose --profile demo run --rm -e REFINE_MODE=1 text-to-sql python main.py --refine --interactive --fault wrong-column
```

`--fault` takes `vague-analysis`, `destructive-sql` or `wrong-column` (the last one only
with `--refine`); `DEMO_FAULT` sets the first two as well. The terminal prints each gate
verdict, one line per attempt.

**What you see in Langfuse:** one `text-to-sql` trace per question, tagged `text-to-sql`
and `demo`. Under the root span: the analysis generation (`metadata.purpose =
query_analysis`), the `gate-database-selection` span with `output.verdict`, the
`retrieve-context` span around the MCP call, the response generation, and the
`gate-response-quality` span with the Haiku grader's generation nested in it. Each
generation links the prompt version that produced it. About 30 seconds after ingest the
code evaluators add `gate-pass` to each gate span and `sql-risk`, `sql-read-only`,
`credential-leak` and `structure-clean` to each generation. With `--refine`, a
`sql-refine-loop` span replaces `retrieve-context` and the trace carries `converged`,
`iterations_to_accept` and, after more than one iteration, `sql_quality_delta`.

## Evaluation

| What | Command | Notes |
|---|---|---|
| Code evaluators: `chain-gate-check`, `sql-safety-guard`, `credential-leak-guard`, `response-structure-check` | `./scripts/seed-code-evaluators.sh` (run by `seed_all.py` and `./setup.sh`) | Deterministic TypeScript in `evaluators/`, no model calls; self-hosted only, written straight to Postgres |
| Per-step dataset `text-to-sql-analysis-step` (8 items) | `python demos/text-to-sql/scripts/seed_all.py` | Tests the analysis step alone against Gate 1's contract |
| Experiment: analysis prompt `production` vs `candidate` | `docker compose --profile demo run --rm text-to-sql python scripts/run_experiment.py` | The full gated chain per item and variant: 16 runs, up to six model calls each (three steps, one retry each); metric `gate_pass_analysis`. Cost and time not yet measured |
| Refine datasets `text-to-sql/critic-accuracy` (10) and `text-to-sql/converged-sql` (6) | `python demos/text-to-sql/scripts/seed_all.py` | The critic in isolation, and golden refined SQL |
| Experiments A (generator) and B (critic rubric) | `docker compose --profile demo run --rm text-to-sql python scripts/run_refine_experiment.py --run all` | Up to 3 iterations per item per arm; executes bounded SQL. Cost and time not yet measured |
| Managed LLM-as-a-judge evaluators (Act 6) | `./scripts/seed-llm-judge-evaluators.sh` (run by `./setup.sh`), then `docker compose --profile tools run --rm test-scenarios` | Scores the 40 test scenarios, not live `text-to-sql` traffic |

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where the host-side scripts read and seed; the container uses `LANGFUSE_INTERNAL_URL` |
| `LANGFUSE_INTERNAL_URL` | `http://langfuse-web:3000` | Langfuse URL inside the container (compose sets both `LANGFUSE_BASE_URL` and `LANGFUSE_HOST` from it) |
| `ANTHROPIC_API_KEY` | blank | Model key; required |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Analysis, response and refine-loop model |
| `TEMPERATURE` | `0.7` | Temperature for the analysis and response steps |
| `GATE_MODEL` | `claude-haiku-4-5` | Gate 2's grounding grader (temperature 0) |
| `LLM_TIMEOUT_S`, `LLM_MAX_RETRIES` | `120`, `2` | Per-call timeout and retries for the chain's model calls |
| `MCP_SERVER_URL` | `http://mcp-clickhouse:8000/sse` | The MCP server the `retrieve-context` step calls |
| `DEMO_FAULT` | blank | `vague-analysis` or `destructive-sql`: force a gate failure |
| `REFINE_MODE` | `0` | `1` turns the refine loop on (`--refine` sets it) |
| `REFINE_MAX_ITERATIONS`, `REFINE_ACCEPT_THRESHOLD` | `3`, `0.9` | Loop budget and the critic score it accepts at |
| `LLM_TIMEOUT` | `45` | Per-call timeout inside the refine loop |
| `PUBLIC_CH_HOST`, `PUBLIC_CH_PORT`, `PUBLIC_CH_USER`, `PUBLIC_CH_PASSWORD` | `sql-clickhouse.clickhouse.com`, `443`, `demo`, blank | Where the refine loop runs `EXPLAIN` and the bounded query |
| `TEXT_TO_SQL_PORT` | `8002` | Host port of `server.py`, used by `query-router` only |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to; set it in cloud mode |

## Layout

```
demos/text-to-sql/
├── demo.yaml               manifest: profile, coverage, commands (feeds the index)
├── README.md               this file
├── DEMO_SCRIPT.md          the talk track, act by act
├── Dockerfile              image for the root compose `demo` profile
├── main.py                 CLI: the 10-question batch, --interactive, --refine, --fault
├── server.py               FastAPI POST /query for query-router (not used by this demo's acts)
├── sql_pipeline.py         the gated chain: analysis → gate 1 → context → response → gate 2
├── gates.py                the two gates, pure functions
├── sql_refine_loop.py      generate → gather-evidence → critique, bounded
├── sql_evidence.py         EXPLAIN + one bounded read-only query on the public playground
├── mcp_client.py           client for mcp-clickhouse
├── langfuse_config.py      Langfuse SDK v4 setup: root span, spans, gates, scores
├── scripts/
│   ├── seed_all.py         the one seed entry point (idempotent, reconciles drift)
│   ├── verify_demo.py      readiness check: READY or NOT READY
│   ├── seed_step_dataset.py, seed_refine_datasets.py   dataset seeders seed_all.py wraps
│   ├── dataset_sync.py     the one rule for "this item is in place", used by seed and verify
│   └── run_experiment.py, run_refine_experiment.py     the experiments
├── tests/                  no services, no model calls; fake_langfuse.py is the fake API
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

## Tests

```bash
cd demos/text-to-sql
pip install -r requirements.txt pytest
python -m pytest -q                    # no services, no model calls, under a second
```

They cover both gates and the chain's retry and routing, the refine loop and its
evidence checks (fake ClickHouse client, scripted model), and the seed and verify scripts
against an HTTP-level fake Langfuse (`tests/fake_langfuse.py`): the real client builds
every request, and the fake rejects unknown parameters, projects observation field groups
and pages by cursor, including empty pages. The cases include unchanged reseeding (no
writes), type, text, config and label drift, archived and edited items, partial and total
write failures, failed reads, wrong or empty projects, a missing or partial `.env` under
hostile shell exports, the Observations v1 fallback and `--link-latest`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `keys map to project '…' (want 'LLM Observability Demo')` | The keys belong to another project. Put this project's keys in the root `.env`; shell exports are ignored |
| `keys authenticate …: HTTP 401` | Wrong key pair, or `LANGFUSE_BASE_URL` points at another host |
| `a 'text-to-sql' run in the last 7 days` fails | Nothing has run yet: `docker compose --profile demo run --rm text-to-sql python main.py` |
| `latest run's scores: …; missing gate-pass` | Code evaluators score about 30 s after ingest. Still missing: `./scripts/seed-code-evaluators.sh`, then a fresh run |
| `mcp-clickhouse not reachable on localhost:8001` | `docker compose --profile langfuse up -d`; the service has no profile and starts with any `up` |
| `retrieve-context` shows `[MCP unavailable: …]` | The chain still answers from the catalog in its prompt. Check `docker compose logs mcp-clickhouse` |
| `dataset …: 0/8 seeded items ACTIVE and unchanged` | Items were archived or edited: `python demos/text-to-sql/scripts/seed_all.py` restores them in place |
| `… still differ after scripts/seed-app-prompts.py` | The root prompt seeder did not write that field; the prompt is left as it was and the run exits 1 |
| Refine traces never converge | No egress to `sql-clickhouse.clickhouse.com:443`; the loop records the connection error as evidence |
| Traces land in another project | A shell-exported key won over the `.env`: `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL` and run again |

## Known limits

- The default path never executes SQL. It reasons over the catalog and drafts SQL in its
  answers; only `--refine` runs `EXPLAIN` and one bounded, read-only, `LIMIT`-ed query.
- Act 3 needs network egress to `sql-clickhouse.clickhouse.com:443`. Without it the loop
  still traces but never converges.
- Traces carry no session or user id; the batch does not set them.
- The routing outcome is a boolean score (`gate-aborted`, `gate-escalated`), not a tag:
  SDK v4 tags are fixed when the trace starts.
- Gate 2's grounding half fails open: a grader reply that does not parse passes the
  response, marked `verdict_source: parse-error`.
- The code evaluators are self-hosted only. In cloud mode the seeder prints UI steps, and
  `verify_demo.py` reports the missing `gate-pass` and `sql-risk` scores until they exist.
- The Monitors are a one-time UI step: the pinned server has no stable Monitors API.
- Earlier versions of `seed_step_dataset.py` appended every item on every run, so an
  older project can hold duplicate step items. Reseeding leaves them alone (they are
  user data to it); archive them in the UI if they matter.
- The managed judges in Act 6 score the test scenarios, not live `text-to-sql` traffic.
- Experiment cost and time are not measured yet.
