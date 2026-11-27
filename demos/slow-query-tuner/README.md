# Slow Query Tuner — Autonomous plan-act-observe agent that tunes a slow ClickHouse query and decides when it is fast enough, with spend caps and a kill switch

An open-ended plan-act-observe agent gets a ClickHouse SA's everyday task: *"Here is a slow
query. Make it at least 5x faster without changing what it returns. You decide how."* It
`EXPLAIN`s the query, inspects the schema, rewrites the SQL and runs each candidate against a
**live ClickHouse** lab, reading back the measured elapsed time, rows read and bytes read. A
deterministic probe checks that each candidate returns the same rows. It keeps going, 3 turns
for an easy query and 9 or more for a hard one, until *it* decides the target is met, the task is
structurally blocked (it then proposes DDL through a human-approval gate), or a turn, budget or
wall-clock backstop ends the run.

This is the contrast demo to the repo's bounded loops (`real-estate` caps single-turn Q&A at
`MAX_ITERS=5`; `agentic-rag` is a predefined graph). Here **the agent decides when it is
done**, and the caps are a backstop, not the design. There is no fictional company: the lab is
one table of 30,000,000 synthetic, deterministic rows (`sql/init/02_seed.sql`). ClickHouse is
both the environment the agent acts on (real timings, so the agent cannot hallucinate a
speedup) and the observability backend judging it (Langfuse v3 stores traces in ClickHouse).

**The question it answers:** how do you let an agent decide when it is finished, and still
sleep at night?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Datasets · Experiment · Evaluate |
| **Stack** | Python · Anthropic SDK · ClickHouse · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 25 min · short path 12 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 25 minutes, 12-minute short path)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| **Open-ended autonomous loop** | Raw Anthropic tool-use API, one tool call per turn; no code path sets the sequence. Step count varies run to run (`agent_loop.py`) |
| **Environment ground truth** | Every `run-query` tool span carries the measured `elapsed_ms`, `read_rows`, `read_bytes` and a result signature from the live lab (`ch_env.py`) |
| **Result-equivalence probe** | An order-independent sha256 signature of the result set. A rewrite that got fast by returning different rows scores `semantics_preserved = 0` |
| **Self-assessed termination, verified** | The agent calls `finish`; the controller re-runs the final SQL three times, takes the median and checks equivalence. A false claim is rejected and returned as the next observation |
| **Backstops, not termination** | `TUNER_MAX_TURNS`, `TUNER_MAX_BUDGET_USD` (from real Anthropic usage), a wall-clock watchdog and a kill sentinel. A run that ends on one is recorded as `error_max_*` or `killed` |
| **Pause/resume as a session** | State is checkpointed every turn; `--resume <run_id>` restores it in a fresh process with the same `session_id`, so Langfuse shows both traces as one session |
| **HITL for irreversible actions** | `propose_ddl` pauses for a human y/n; DDL runs only on a separate admin connection after approval. The read-only `tuner_agent` cannot write |
| **Runaway-cost Monitor** | `--runaway` trips the spend cap on purpose; on a Langfuse v4+ server the `query-tuner runaway cost` Monitor (max `totalCost` per trace) crosses its alert threshold. The pinned v3 stack has no Monitors, so there Act 4 compares the runaway trace's cost with a normal run's |
| **Scores** | Trace scores `turns_used`, `run_cost_usd`, `task_completed`, `verified_speedup`, `trajectory_efficiency`; span scores `semantics_preserved`, `improvement_delta`; evaluator scores `goal_drift` (managed judge) and `cap_terminated` / `termination_class` (code evaluator) |
| **Prompt management + experiment** | `query-tuner-system` versions `v1-naive` and `v2-disciplined` (= `production`), compared on the `query-tuner/goals` dataset with caps and tools pinned |

## Who it's for

ClickHouse SAs and their prospects. Every prospect has a slow query, and watching an agent do
the SA's own job, safely and with rails you can point to, is the hook. Pick it over
`agentic-rag` or `real-estate` when the room asks about agents whose step count is not known in
advance: termination, runaway cost, pause/resume and human approval.

There is deliberately **no LibreChat agent** for this demo. An unbounded loop with a spend cap
does not belong behind a chat box where a user can fire it repeatedly; it belongs behind an
operator-run CLI with explicit caps and a kill switch. That choice is part of the guardrail story.

## What runs where

```
 you (repo root) ── docker compose --profile demo run --rm slow-query-tuner python main.py …
                     │  CLI container, no port. ./demos/slow-query-tuner is mounted at /app,
                     │  so checkpoints/<run_id>.json survives `run --rm` for --resume
                     ├──► Anthropic API: plan-next-action (ANTHROPIC_MODEL)
                     ├──► clickhouse-tuning  host :8126 → 8123, ClickHouse 26.3
                     │      tuning_lab.web_events, 30M rows, ORDER BY tuple()
                     │      tuner_agent (readonly=2, quotas) for every agent query
                     │      tuner_admin only inside an approved propose_ddl
                     └──► Langfuse langfuse-web:3000 (host :3001), project `LLM Observability Demo`
   trace `tune-clickhouse-query` · session `qtune-<8 hex>` per run, reused on --resume
   tags `slow-query-tuner`, `demo` (+ `fault:runaway`) · 5 trace scores + 2 span scores
```

Each turn is `plan-next-action` (generation) → one tool span (`run-query`, `explain-query`,
`get-schema`, `check-equivalence`, `propose-ddl`) → `assess-progress` (evaluator), under one
`tune-clickhouse-query` agent root. Span names are low-cardinality; the turn number is in
metadata. Langfuse's own ClickHouse is never touched.

## Set up from scratch

Prerequisites: Docker and the root stack, set up with `./setup.sh`, and an `ANTHROPIC_API_KEY`
in the root `.env`. Run these from the repo root:

```bash
./setup.sh                          # root stack + .env; also runs this demo's seed, both evaluators
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
docker compose --profile demo up -d clickhouse-tuning       # the lab; seeds 30M rows on first start
python demos/slow-query-tuner/scripts/seed_all.py           # prompts, dataset, monitors (runs in the container)
./scripts/seed-code-evaluators.sh                           # includes runaway-loop-guard (cap_terminated)
./scripts/seed-query-tuner-evaluators.sh                    # the managed goal_drift judge
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1   # one fresh run, capped at $0.75
python3 demos/slow-query-tuner/scripts/verify_demo.py      # should print READY
```

`./setup.sh` runs `seed_all.py`, `seed-code-evaluators.sh` and `seed-query-tuner-evaluators.sh`
for you; the explicit lines above are for re-seeding. `seed_all.py` is idempotent and prints what
it created, updated or left unchanged. A prompt gets a new version only when its type, text or
config drifted; a missing or moved label is reattached to the matching version. Dataset items are
written by stable id only when they differ (an archived one is restored to ACTIVE) and read back.
It exits 1 if any write fails. On the host it checks the root `.env`, then runs itself inside the
`slow-query-tuner` container, which has the dependencies and reads that `.env`.

The root `.env` is the only source of `LANGFUSE_*`. The service reads its keys through
`env_file`, `verify_demo.py` drops shell-exported `LANGFUSE_*` before reading `.env`, and
`seed_all.py` strips them before calling compose. Unset them anyway: other services interpolate
`${...}` from the shell first. With no keys at all the loop still runs and every observation is
a no-op, so an empty Langfuse is the symptom.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `python demos/slow-query-tuner/scripts/seed_all.py`, then one `--query q1` run and `scripts/run_experiment.py --sample 1` (real spend) | Prompts and labels match the code, a fresh trace exists, Act 6's Runs tab is populated |
| The day before | On a Langfuse v4+ server only: create the two Monitors in **Monitors → New Monitor** from the fields `seed_all.py` prints. On this stack's v3 server, skip it | Act 4's live alert beat needs v4+; on v3 the act uses its trace-cost Fallback, which `verify_demo.py` checks |
| 30 minutes before | `python demos/slow-query-tuner/scripts/reset_demo.py`, then `python3 demos/slow-query-tuner/scripts/verify_demo.py --links` | Drops DDL a rehearsal approved and clears stale kill sentinels; prints READY and the link each act opens |
| 5 minutes before | Open the links it printed | The q1 trace on **Agent Graph → Expanded**, Sessions, the system prompt, and Monitors on v4+ |

## Run it

```bash
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1   # short, self_completed
docker compose --profile demo run --rm slow-query-tuner python main.py --query q2   # long: env error + plateau
docker compose --profile demo run --rm slow-query-tuner python main.py --query q3   # blocked -> propose_ddl (HITL)
docker compose --profile demo run --rm slow-query-tuner python main.py --runaway    # trips the cost cap; up to $2.50
docker compose --profile demo run --rm slow-query-tuner python main.py --resume <run_id>
docker compose --profile demo run --rm slow-query-tuner python main.py --sql "SELECT ..." --target-ms 500
```

Add `--auto-approve-ddl` or `--auto-deny-ddl` to script the HITL gate, and `--max-turns`,
`--max-budget-usd` or `--watchdog-s` to override a backstop for one run. `python
scripts/run_live_demo.py` (in the container) prints the beat plan; `--run` runs q1, q2 and the
runaway back to back.

**What you see in Langfuse:** a `tune-clickhouse-query` trace per run, with the goal on the root
input and `original_sql`, `final_sql`, `verified_speedup`, `termination_reason` and `summary` on
its output. Under it, one `plan-next-action → {tool} → assess-progress` group per turn.

- **Agent Graph Expanded**: one run's exact sequence. q1 (a short chain) next to q2 (a long one)
  under the same trace name is the pattern's defining visual.
- **Agent Graph Aggregated**: the loop collapses to `plan-next-action → {tool} → assess-progress ↺`
  with repeat counts, which is how you read an agent whose step count is unbounded.
- **Sessions**: a run paused (`termination_reason=killed`) and resumed share one `qtune-…` session.
- **Monitors** (Langfuse v4+ only): `max(totalCost)` per trace (runaway) and `max(turns_used)` across the fleet.

## Evaluation

| What | Command | Notes |
|---|---|---|
| In-run code scores | every `main.py` run | 5 trace scores and 2 span scores, written by `agent_loop.py`; no model call |
| `goal_drift` managed judge | `./scripts/seed-query-tuner-evaluators.sh` | Self-hosted, seeded through Postgres; scores new `tune-clickhouse-query` roots only. Prints UI steps on Cloud |
| `cap_terminated`, `termination_class` | `./scripts/seed-code-evaluators.sh` | `evaluators/runaway-loop-guard.ts` |
| `query-tuner/goals` dataset | `python demos/slow-query-tuner/scripts/seed_all.py` | 3 root-level items (q1–q3): goal in, completion criteria out |
| Prompt A/B experiment | `docker compose --profile demo run --rm slow-query-tuner python scripts/run_experiment.py --sample 2` | `v1-naive` vs `v2-disciplined`, caps pinned at 12 turns / $1.00 per item, so a full 3-item 2-arm run is bounded near $6 |
| Gate | add `--ci` | Exit 1 unless every arm has `pass_rate >= 0.8` and `cap_hit_rate == 0` |

The dataset has **root-level items only**: `expected_output` holds completion criteria, never a
step sequence. The same goal legitimately takes different valid paths run to run (PREWHERE first
or predicate fix first), so a per-step dataset would score a better, shorter path as a failure.
Step-level *code* checks on live traces (`semantics_preserved`, `improvement_delta`) are still fine.

## The bad-query catalog

| Id | Query (sins) | Target | Designed outcome |
|---|---|---|---|
| **q1** easy | daily uniques for June: `count(DISTINCT user_id)` + `toDate(parseDateTimeBestEffortOrNull(ts_raw))` in WHERE | 800 ms | 2–4 turns; `event_date` + `uniq()`; clean **self_completed** |
| **q2** medium | top-10 URLs by avg duration, one country + month: `SELECT *` subquery, `lower(country)='us'`, string-date parse, global `ORDER BY` | 800 ms | 5–10 turns incl. an env error and a plateau; **self_completed** (long-trace + pause/resume vehicle) |
| **q3** blocked | needle: one `user_id`, one date; physically needs a sort key or projection | 800 ms | rewrites exhausted → `propose_ddl(ADD PROJECTION …)` → **HITL** (approve → met; deny → honest `self_gave_up`). `--runaway` retargets it to 50 ms with the naive prompt → **error_max_budget_usd** |

The turn bands come from `queries.py` and feed `trajectory_efficiency`; they are soft
expectations, not assertions.

## Safety rails

- **Sandboxed identity**: the agent connects as `tuner_agent`: `SELECT` only, `readonly=2` (may
  `SET` per-query settings, cannot write), `max_execution_time=30`, `max_memory_usage=4G`,
  `max_result_rows=10k` (`sql/init/03_users.sql`).
- **App-side SQL-shape allow-list** (SELECT/WITH/EXPLAIN/SHOW, single statement) in front of the
  grants: defence in depth, and errors come back as observations.
- **Dedicated throwaway container** (`clickhouse-tuning`): the blast radius is one lab.
- **DDL only through the human gate**: the `tuner_admin` connection is opened only inside the
  approved `propose_ddl` path.
- **Caps + kill switch**: checked before every turn. See [Known limits](#known-limits) for exactly
  what they do and do not bound.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where `verify_demo.py` reads; the container uses `LANGFUSE_INTERNAL_URL` (default `http://langfuse-web:3000`) |
| `ANTHROPIC_API_KEY` | blank | The planner's key; required |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | The policy model; `budget.py` prices it for the spend cap |
| `TEMPERATURE` | `0.2` | Planner temperature |
| `TUNER_MAX_TURNS` | `15` | Backstop: `error_max_turns` once the turn counter passes it |
| `TUNER_MAX_BUDGET_USD` | `0.75` | Backstop: `error_max_budget_usd` once cumulative cost reaches it |
| `TUNER_WATCHDOG_S` | `600` | Backstop: `error_watchdog` after this many seconds of wall clock |
| `TUNER_COMPACT_AFTER` | `6` | Fold turn pairs older than this into one-line summaries |
| `TUNER_CHECKPOINT_DIR` | `checkpoints` | Run state and kill sentinels |
| `CLICKHOUSE_TUNING_HTTP_PORT` | `8126` | Host port for the tuning lab; `verify_demo.py` reads it |
| `TUNING_MAX_EXECUTION_TIME`, `TUNING_MAX_RESULT_ROWS`, `TUNING_MAX_RESULT_BYTES` | `30`, `10000`, `50000000` | Per-query settings on every agent query |
| `TUNING_CH_*` | set in `docker-compose.yaml` | Lab host, port, database and the two users |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/slow-query-tuner/
├── demo.yaml            manifest: profile, coverage, commands (feeds the index)
├── README.md            this file
├── DEMO_SCRIPT.md       the talk track, act by act
├── Dockerfile           image for the root compose `demo` profile (CLI, no server)
├── main.py              CLI: --query / --sql / --runaway / --resume / --interactive / cap overrides
├── agent_loop.py        the pattern: plan-act-observe controller, finish-claim check, HITL gate, scores
├── tools.py             six tool schemas + dispatcher (run_query … propose_ddl, finish)
├── ch_env.py            the lab: tuner_agent (RO) + tuner_admin (DDL gate) clients, allow-list, signatures
├── budget.py            turn / cost / wall-clock caps + kill sentinel; Anthropic price table
├── checkpoint.py        LoopState + per-run JSON state (saved every turn; --resume reuses session_id)
├── queries.py           bad-query catalog (q1/q2/q3) + expected-turn bands
├── prompts.py           managed prompts (query-tuner-system v1/v2, query-tuner-goal) + local fallbacks
├── langfuse_config.py   SDK v4 wiring: typed observe(), trace_context(), scores, get_prompt(), flush()
├── sql/init/            lab schema, 30M-row seed, sandboxed users and grants
├── checkpoints/         runtime state (git-ignored)
├── scripts/
│   ├── seed_all.py      the one seed entry point: prompts, dataset, monitors
│   ├── seed_prompts.py · seed_dataset.py · seed_monitors.py
│   ├── verify_demo.py   readiness check: READY or NOT READY
│   ├── reset_demo.py    drops approved DDL from the lab, clears kill sentinels
│   ├── run_experiment.py   prompt / model A/B on the dataset (--ci gate)
│   └── run_live_demo.py    prints the beat plan; --run warms q1, q2, runaway
└── tests/               no services, no model calls; fake_services.py fakes Langfuse + ClickHouse HTTP
```

Root-level companions: `evaluators/runaway-loop-guard.ts` (seeded by
`scripts/seed-code-evaluators.sh`) and `scripts/seed-query-tuner-evaluators.sh` (the managed
goal-drift judge).

## Tests

```bash
cd demos/slow-query-tuner
pip install -r requirements-dev.txt      # Python 3.11, like the image
python -m pytest -q                       # no services, no model calls, a few seconds
```

They cover the caps tripping at their boundaries and the kill switch's precedence, finish-claim
verification (true, too slow, different rows, gave up, bad SQL), the checkpoint round trip and
compaction, the SQL allow-list and result signatures, and `reset_demo.py`'s DDL detection. The
seeders run their real reconcile paths against a fake SDK client: unchanged reseeds write nothing,
a missing label is reattached, config or text drift adds a version, and partial or total item
write failures fail `seed_all.py`. `verify_demo.py` runs through its real HTTP code against
`tests/fake_services.py`, a fake Langfuse and ClickHouse that rejects query parameters the API
does not accept and returns only the requested field groups. Those tests cover v3 and v4 servers,
empty and repeated cursor pages, missing or partial `.env` files with hostile shell exports, and
missing assets for each act.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `NOTE: Langfuse keys not set — the loop runs, all instrumentation no-ops` | The root `.env` has no keys. Run `./setup.sh` |
| `keys map to project '…' (want 'LLM Observability Demo')` | The keys belong to another project. Fix the root `.env`; shell exports are ignored by verify |
| `clickhouse-tuning … Connection refused` | The lab is not running: `docker compose --profile demo up -d clickhouse-tuning` |
| `clickhouse-tuning … HTTP 4xx` as `tuner_agent` (authentication) | `03_users.sql` did not run, usually an old volume from before `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1`. Recreate the lab volume (see the script's [Reset / re-run](DEMO_SCRIPT.md#reset--re-run)) |
| `lab table still pessimal` fails | A rehearsal approved DDL in Act 5 and it is still applied: `python demos/slow-query-tuner/scripts/reset_demo.py` |
| `a 'tune-clickhouse-query' run in the last 7 days` fails | Nothing has run yet: run `--query q1` |
| `latest run's scores … missing …` | The run crashed mid-loop (no `finalize`), or scores are still landing. Re-run q1 |
| `Act 4: no documented API reads Monitors` warning | Expected on a v4+ server: confirm the two Monitors in the UI. On the v3 stack verify checks the Fallback's scores instead |
| `ERROR: no checkpoint for run_id` on `--resume` | Run it from the same checkout; state lives in `demos/slow-query-tuner/checkpoints/` |
| A normal run ends `error_max_budget_usd` | The run hit `TUNER_MAX_BUDGET_USD`. Raise it for one run with `--max-budget-usd` |

## Known limits

- **Monitors are a Langfuse v4+ feature.** This stack pins Langfuse v3.221.1, which has no
  Monitors, so here Act 4 always runs on its trace-cost Fallback. On a v4+ server,
  `seed_monitors.py` creates them when a Monitors API answers, or prints the fields to paste into
  **Monitors → New Monitor**; `verify_demo.py` cannot confirm them, because no documented API
  reads Monitors. Everything else (traces, scores, sessions, agent graph, code evaluators, the
  managed judge) is native on v3.
- **The thresholds are not calibrated.** The Monitor bands ($0.40 warn, $1.00 alert) and the
  runaway caps (25 turns, $2.50) are spec defaults in `seed_monitors.py` and `main.py`, not
  measured against real runs.
- **Caps are checked between turns, not during one.** `budget.check()` runs before each turn, so
  the turn in flight always finishes: a run can end up to one turn's cost past
  `TUNER_MAX_BUDGET_USD`, and the watchdog can overrun by one turn's duration. A single lab query
  is bounded by `max_execution_time=30` s.
- **The spend cap is the app's own estimate.** Cost comes from `budget.py`'s price table (four
  Claude models; any other model is charged at Sonnet rates; cache tokens at the input rate), not
  from Langfuse's cost. The two can disagree.
- **"Never removable" means not by the loop.** `--runaway` and the `--max-*` flags only replace
  the values; nothing sets an upper bound on what an operator passes.
- **The kill switch is cooperative.** `touch checkpoints/<run_id>.kill` or Ctrl-C ends the run at
  the next turn boundary with a checkpoint (`killed`, exit 130); it does not interrupt a model
  call or query already running.
- **Non-determinism is the point.** q2's turn count, and whether q3 reaches `propose_ddl`, vary
  run to run. Say so out loud.
- **Approved DDL persists.** An approved Act 5 projection stays in the lab volume until
  `reset_demo.py` drops it; until then q3 is fast without DDL and the runaway target may verify.
- **Self-hosted only for the judge.** `seed-query-tuner-evaluators.sh` writes to Langfuse's
  Postgres; on Cloud it prints UI steps instead.
