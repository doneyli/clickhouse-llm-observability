# Analytics at Agentic Speed — an agent that tests hundreds of what-ifs on ClickHouse before it answers

An analytics agent answers what-if questions by **sweeping hundreds of scenarios**
against a ClickHouse Cloud table of US flights (231.6M rows), fully traced to
**Langfuse**. Every query span shows the time **ClickHouse itself reports**, next to the
round trip, rows read and the query id.

The demo makes two claims, and the trace shows both:

1. **ClickHouse drops out of the agent's latency budget.** Hundreds of query spans are
   slivers next to the LLM calls, and `db_time_share` is a few percent of the run.
2. **That speed changes what an agent can do.** Left alone, it tests hundreds of
   scenarios per question and refines on its own, which no analyst would do by hand.
   Each extra scenario costs milliseconds, so **breadth becomes a knob you can afford
   to turn up**. Experiment A measures exactly that. Both breadths got the same 2 rounds.
   On 15 questions with exact SQL ground truth, 10 scenarios per round scored **40%** and
   300 per round scored **80%**. The wide run took 11 s more wall time and cost $0.026
   more per question.

> **The core idea:** the LLM writes the plan once; ClickHouse runs it hundreds of times.
> LLM calls scale with *rounds* (3–6 per question). Database work scales with
> *scenarios*, and that's the cheap part.

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (~15 min, 8-min short path)
- **Design record:** [SPEC.md](SPEC.md)
- **Measured numbers, with the command behind each:** [docs/MEASUREMENTS.md](docs/MEASUREMENTS.md)
- **Prompt CI gate:** [cicd/README.md](cicd/README.md)

## What runs where

```
 you (main.py, or the live view at :8099)
        │
        ▼
 agent process (Python 3.11, asyncio) ── Claude Opus 5.5: plan + analyse (structured outputs)
        │  HTTPS, typed {name:Type} params, 32 queries in flight, read-only user
        ▼
 ClickHouse Cloud service ── flights.ontime, 231.6M rows, ORDER BY (Origin, Year, Month, …)

 one Langfuse client ──► Langfuse Cloud project `analytics-agentic-speed`
                         traces · prompts (by label) · dataset · dashboard · experiments
```

## How it works

```
question ─► profile (5 queries) ─► plan (LLM: ONE SQL template + a lever grid)
        ─► round: compile grid → sweep N queries, 32 in flight → analyse (LLM)
             └─ refine? → next round with a finer grid (the agent decides)
        ─► verify: re-run the winning scenario, compare the number the answer states
```

- **One query per scenario.** Each scenario is a different predicate: a threshold, a
  window, a filter. A single `GROUP BY` can't always express that kind of what-if. It
  also makes every scenario its own span with its own timing.
- **Measure, don't search.** The template measures one point; the code expands the grid.
  Lookups may list candidates but not measure outcomes. Both rules are enforced in
  `explorer/scenarios.py`, not just asked for in the prompt.
- **Typed server-side parameters.** The template uses `{origin:String}` and
  `{buffer:UInt16}`. Values bind through ClickHouse query parameters, never string
  formatting. The rails are a read-only user, an SQL allow-list and per-query limits.
- **Honest timings.** `use_query_cache=0`. Server time comes from the response's
  `statistics.elapsed`, and a deterministic verify step re-runs the winner.

## Set up from scratch

```bash
cd demos/analytics-agentic-speed
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # fill in ClickHouse Cloud, Anthropic and Langfuse project keys
```

The ClickHouse Cloud service's **IP access list must include this machine**. A TLS error
on connect (curl exit 35) means it doesn't.

```bash
.venv/bin/python scripts/load_data.py      # one-off: schema, 231.6M rows (~6 min), read-only agent user
.venv/bin/python scripts/seed_langfuse.py  # score configs, prompts, dashboard (idempotent)
.venv/bin/python scripts/seed_dataset.py   # 15 questions, ground truth computed from reference SQL
```

## Before a demo

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
.venv/bin/python scripts/warmup.py --runs 4        # wake both replicas' caches
.venv/bin/python main.py --question q-padding      # one fresh hero trace
./run_ui.sh                                        # live view, http://localhost:8099
.venv/bin/python scripts/verify_demo.py --links    # READY / NOT READY, plus the link for every act
```

The day before, rerun Experiment A if you want your own numbers in Act 4. It takes
~20 min and ~$4: `.venv/bin/python scripts/run_experiment.py --breadth both --concurrency 2`.

## Run it

```bash
.venv/bin/python main.py --list                        # question ids
.venv/bin/python main.py --question q-padding          # the hero question
.venv/bin/python main.py --ask "your own what-if"      # free text
.venv/bin/python scripts/verify_trace.py <trace_id>    # check what landed in Langfuse
```

Knobs: `--max-scenarios` (grid budget per round, default 300), `--rounds` (default 4),
`--concurrency` (queries in flight, default 32), `--effort`, `--model`,
`--prompt-label`, `--no-query-scores`.

### Live view

`./run_ui.sh` serves http://localhost:8099. Pick a question (or type one), choose
scenarios per round and max rounds, and run. The page streams the run as it happens:

- **The steps the agent takes:** profile, plan, lookups, each round, the refine-or-answer
  decision with the model's reasoning, and verify.
- **Live tiles:** scenarios run, scenario queries per second, ClickHouse server time per
  query (p50 · p95), the share of wall time spent waiting on ClickHouse, model calls and cost.
- **Where the time went:** ClickHouse vs model thinking vs everything else, live.
- **One heatmap per round:** the lever grid (e.g. airport × buffer minutes) fills in as
  each scenario's query returns, colored by its metric. Hover a cell for that query's
  server ms, round trip and rows read. The winning scenario is outlined.
- **A histogram** of ClickHouse server time across every query in the run.
- **The answer,** whether a re-run confirmed it, and a link to the run's Langfuse trace.

It runs the same `run_question` as the CLI (tags `live`, `ui`), so every run is a normal
trace. Files: `webapp/server.py` (FastAPI, Server-Sent Events) and `webapp/static/`
(plain HTML/CSS/JS on the vendored click-ui tokens, no build step).

### Many agents at once (optional act)

```bash
.venv/bin/python scripts/run_concurrency.py --levels 1,5,10 --dry-run   # the plan and the spend estimate
.venv/bin/python scripts/run_concurrency.py --levels 1,5,10 --yes       # ~3,700 queries at 10 agents
```

Each level gets its own Langfuse session. The dashboard's concurrency widget charts
server p50/p95 per level.

## Evaluation

- **Dataset:** `scenario-explorer-questions` holds 15 questions. Each has an exact answer
  from its `reference_sql` (`data/questions.yaml`), so grading needs no LLM judge. The
  dataset kept the demo's original name so the existing Experiment A runs stay linked.
- **Experiment A, sweep breadth:** `scripts/run_experiment.py --breadth both` runs 10 vs
  300 scenarios per round, both at 2 rounds.
- **Experiment B, prompt label or model:** `--compare prompt-labels production,<label>` or
  `--compare models claude-opus-5-5,claude-sonnet-5-5`.
- **Scores on every run:** `db_server_ms` per query span; `db_time_share`,
  `scenarios_evaluated`, `db_p95_ms`, `llm_calls`, `run_cost_usd`, `stop_reason` and
  `recommendation_grounded` per trace. Experiment items add `answer_correct` and
  `answer_key_match`.
- **Prompt CI gate:** `scripts/prompt_gate.py` runs the dataset on a candidate prompt label
  and blocks the deploy when it misses `cicd/thresholds.json`. The workflow is
  `.github/workflows/analytics-agentic-speed-prompt-gate.yml`. Setup and the
  blocked-prompt beat are in [cicd/README.md](cicd/README.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| TLS error / curl exit 35 on connect | This machine isn't on the service's IP access list. Add it in ClickHouse Cloud. |
| Langfuse 404s, or traces "missing" | Keys for another project. `unset LANGFUSE_*` and rerun; `verify_demo.py` checks which project the keys map to. |
| Server ms in the hundreds | Either a cold replica (run `warmup.py`), or the template doesn't filter on the sort key. Open the `sweep` span's input and check for `Origin = …` and `Year = …`. See [MEASUREMENTS.md](docs/MEASUREMENTS.md#act-2-the-hero-question). |
| Answer with NULL metrics, `recommendation_grounded = false` | The table was being reloaded mid-run. Wait for `load_data.py` to finish; a NULL metric never counts as grounded. |
| A run ends in `error` after a refusal | The model refused twice (it retries once). The category is on the generation. Rerun or rephrase. |
| Dashboard widget empty | Rerun `scripts/seed_langfuse.py` (it reconciles). The Experiment A widgets need runs with version `breadth-10` / `breadth-300`. |
| `410 Gone` from a read-back | A removed Langfuse Cloud endpoint. The scripts here use Observations v2, Scores v3 and experiment-items. |
| Port 8099 busy | `PORT=8100 ./run_ui.sh` |
| `EXPLORER_TARGET=playground` hits a quota | The public playground allows 20 runs per query shape per hour. Use it for smoke tests only. |

## Layout

| File | What it is |
|---|---|
| `explorer/ch_client.py` | Async ClickHouse HTTP client: typed params, per-query settings, server vs round-trip timing |
| `explorer/agent.py` | The loop and its trace (profile → plan → rounds → verify), all scores |
| `explorer/scenarios.py` | Template validation, the measure-don't-search rules, grid expansion (deterministic, no LLM) |
| `explorer/llm.py` | Claude calls with structured outputs, recorded as Langfuse generations with cost |
| `explorer/prompts.py` | Planner and analyst prompts (seeded to Langfuse, fetched by label) |
| `explorer/evaluators.py` | Exact-match grading for experiments |
| `explorer/tracing.py` | The Langfuse client, minus the Anthropic SDK's duplicate native spans |
| `main.py` | Run one question, print the DB-vs-LLM breakdown and the trace link |
| `webapp/` · `run_ui.sh` | Live view: the sweep filling in, DB vs model time, latency histogram |
| `data/questions.yaml` | 15 questions with reference SQL, answers, runner-ups and margins |
| `scripts/load_data.py` | Load flights into ClickHouse Cloud, create the read-only agent user |
| `scripts/warmup.py` | Pre-flight: cold vs warm timings for scenario-shaped queries |
| `scripts/verify_demo.py` | Readiness check for every act, plus `--links` |
| `scripts/seed_langfuse.py` | Score configs, prompts and the dashboard |
| `scripts/seed_dataset.py` | Ground truth from reference SQL → Langfuse dataset |
| `scripts/run_experiment.py` | Experiment A (sweep breadth) and B (prompt label / model) |
| `scripts/prompt_gate.py` · `cicd/` | Prompt CI gate and its thresholds |
| `scripts/run_concurrency.py` | Many agents at once against one service |
| `scripts/verify_trace.py` | Read a trace back and check its shape (Observations v2 + Scores v3) |
| `docs/MEASUREMENTS.md` | Every measured number, with its command |

## Known limits

- **The model is most of the wall time.** On the hero question ClickHouse is ~6% of the
  run, so its speed buys *breadth*, not a faster answer. A faster or smaller model shortens
  the run; a faster database lets it look further in the same time.
- **One query per scenario, on purpose.** A single `GROUP BY` over the whole grid would be
  faster on some questions. The rules forbid it so each scenario is an auditable span and
  Experiment A measures breadth, not who writes the cleverest SQL.
- **15 questions, one table.** Small enough to grade exactly, too small for fine-grained
  accuracy claims: one question is ~7 points.
- **The Experiment A figures predate the question fix.** Four questions didn't state the
  measured year. See [MEASUREMENTS.md](docs/MEASUREMENTS.md#act-4-experiment-a-sweep-breadth).
- **Timings depend on the setup:** service tier, region, and a ~350 ms laptop round trip.
  Re-measure before quoting.
- **Pre-rename traces** (before 2026-11-26) carry the root name `scenario-explorer`. The
  dashboard counts both names.

Rehearsing without a Cloud service: `EXPLORER_TARGET=playground` points the agent at the
same data on ClickHouse's public playground, for smoke tests only (see Troubleshooting).

## Tests

```bash
.venv/bin/python -m pytest -q
```
