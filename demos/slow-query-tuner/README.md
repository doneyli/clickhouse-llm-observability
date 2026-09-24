# Slow Query Tuner — Autonomous Agent Loop (Pattern #7)

An open-ended plan-act-observe agent handed a ClickHouse SA's bread-and-butter
task: *"Here is a slow query. Make it at least 5x faster without changing what it
returns. You decide how."*

The agent runs a genuinely variable-length loop against a **live ClickHouse
instance**: it `EXPLAIN`s the query, inspects the schema, rewrites the SQL,
**executes each candidate for real and reads back the actual elapsed time / rows
read / bytes read as ground truth**, verifies result-set equivalence with a
deterministic probe, and keeps iterating — 3 turns for an easy query, 9+ for a
hard one, **nobody knows in advance** — until *it* decides the target is met, the
task is structurally blocked (and it proposes DDL through a human-approval gate),
or a budget/turn/wall-clock backstop forcibly ends the run.

This is the contrast demo to the repo's bounded loops (`real-estate` is
`MAX_ITERS=5` single-turn Q&A; `agentic-rag` is a predefined graph). Here **the
agent decides when it is done**, and the caps are a backstop, not the design.

The ClickHouse double punchline: the *environment the agent acts on* is
ClickHouse (real timings — the agent cannot hallucinate a speedup), and the
*observability backend judging the agent* is ClickHouse (Langfuse v3).

## What it demonstrates

| Capability | How |
|---|---|
| **Open-ended autonomous loop** | Raw Anthropic tool-use API; the model emits one tool call per turn, no code path dictates the sequence. Step count 2–15+, run to run. |
| **Environment ground truth** | Every `run-query` returns *measured* `elapsed_ms` / `read_rows` / `read_bytes` + a result signature from a live ClickHouse. Progress is validated against reality, not the model's text. |
| **Result-equivalence probe** | An order-independent sha256 signature of the result set; the DB arbitrates equivalence. A rewrite that got fast by returning different rows fails `semantics_preserved`. |
| **Self-assessed termination, verified** | The agent calls `finish`; the controller **independently re-executes** the final SQL + equivalence probe (median of 3) and rejects a false claim, bouncing it back as the next observation. |
| **Backstops, not termination** | `MAX_TURNS`, `MAX_BUDGET_USD` (from real Anthropic usage), a wall-clock watchdog, and a kill sentinel. A run ending on one is recorded as a *failure mode* (`error_max_*` / `killed`). |
| **Pause/resume as a session** | State checkpoints every turn; `--resume <run_id>` restores it in a fresh process reusing the same `session_id`, so Langfuse stitches both traces into one investigation. |
| **HITL for irreversible actions** | `propose_ddl` pauses for human y/n; DDL runs only through a separate admin connection after approval. The read-only `tuner_agent` cannot write. |
| **Runaway-cost Monitor** | `--runaway` deliberately trips the spend cap; a seeded Monitor on `max(totalCost)` per trace crosses its alert threshold. |
| **First-class scores** | Trace scores `turns_used`, `run_cost_usd`, `verified_speedup`, `task_completed`, `trajectory_efficiency`; span scores `semantics_preserved`, `improvement_delta`; a managed `goal_drift` judge. |

## Who it's for

ClickHouse SAs and their prospects. Every prospect has a slow query; watching an
agent do the SA's own job — safely, with rails you can point to — is the hook.
The demo's opening frame is *"yesterday's demos were workflows; today the code
does not know the path — how do you let an agent decide when it's done and still
sleep at night?"*

## Quick start

```bash
# 1. Bring up the disposable tuning lab (seeds ~30M rows in seconds) + build the app.
docker compose --profile demo up -d clickhouse-tuning

# 2. Seed managed prompts + the root-level dataset + monitors (idempotent).
docker compose --profile demo run --rm slow-query-tuner python scripts/seed_all.py

# 3. Run the agent on a query from the catalog.
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1   # short, self_completed
docker compose --profile demo run --rm slow-query-tuner python main.py --query q2   # long (env error + plateau)
docker compose --profile demo run --rm slow-query-tuner python main.py --query q3   # blocked -> propose_ddl (HITL)

# 4. Trip the cost cap on purpose (the Monitor beat).
docker compose --profile demo run --rm slow-query-tuner python main.py --runaway

# Ad-hoc query:
docker compose --profile demo run --rm slow-query-tuner python main.py --sql "SELECT ..." --target-ms 500
```

`setup.sh` runs step 2 automatically (guarded, idempotent, non-fatal).

The demo **degrades gracefully with no Langfuse keys**: the loop still runs; all
instrumentation no-ops.

## The bad-query catalog

| Id | Query (sins) | target | Designed outcome |
|---|---|---|---|
| **q1** easy | daily uniques for June: `count(DISTINCT user_id)` + `toDate(parseDateTimeBestEffortOrNull(ts_raw))` in WHERE | 800 ms | 2–4 turns; `event_date` + `uniq()`; clean **self_completed** (short-trace money shot) |
| **q2** medium | top-10 URLs by avg duration, one country + month: `SELECT *` subquery, `lower(country)='us'`, string-date parse, global `ORDER BY` | 800 ms | 5–10 turns incl. an env error (memory limit) and a plateau; **self_completed** (long-trace + pause/resume vehicle) |
| **q3** blocked | needle: one `user_id`, one date — physically needs a sort key/projection | 800 ms | agent exhausts rewrites → `propose_ddl(ADD PROJECTION …)` → **HITL** (approve → met; deny → honest `self_gave_up`). `--runaway` retargets to 50 ms + naive prompt → **error_max_budget_usd** |

## How the loop is visible in Langfuse

- **Agent Graph Expanded** — one run's exact think-act-observe sequence. q1 (3-node
  chain) vs q2 (9-node chain) under the *same* trace name is the pattern's
  defining visual.
- **Agent Graph Aggregated** — the loop collapses to `plan-next-action → {tool} →
  assess-progress ↺` with repeat counts: how you read an agent whose step count
  is unbounded.
- **Sessions** — a run paused (`termination_reason=killed`) and resumed share one
  `session_id`, replayed as a single investigation.
- **Monitors** — `max(totalCost)` per trace (runaway) and `max(turns_used)`
  (backstop-stopped) across the fleet.

## Why per-step datasets are the wrong call here

The `query-tuner/goals` dataset has **root-level items only**: `input` = the goal,
`expected_output` = completion *criteria*, never a step sequence. The same goal
legitimately yields different valid trajectories run to run (PREWHERE-first or
predicate-fix-first both reach the target). A per-step dataset would enshrine one
arbitrary trajectory as "correct" and score a *better, shorter* path as a failure.
Task completion belongs on the **root** of the trace. (Step-level *code* checks on
live traces are still fine — see `semantics_preserved` / `improvement_delta`.)

## When NOT to hand a pattern to end users

There is **deliberately no LibreChat agent** for this demo. An unbounded loop with
a spend cap does not belong behind a chat box where a user can fire it repeatedly;
it belongs behind an operator-run CLI with explicit caps and a kill switch. That
choice is itself part of the pattern's guardrail story.

## Safety rails (the guardrail layer)

- **Sandboxed identity**: the agent connects as `tuner_agent` — `SELECT` only,
  `readonly=2` (may `SET` per-query settings, cannot write), quotas
  `max_execution_time=30`, `max_memory_usage=4G`, `max_result_rows=10k`.
- **App-side SQL-shape allow-list** (SELECT/WITH/EXPLAIN/SHOW, single statement)
  in front of the grants — defense in depth, and clean error-as-observation.
- **Dedicated throwaway container** (`clickhouse-tuning`) — blast radius = one lab.
  Langfuse's own ClickHouse is never touched.
- **DDL only through the human gate** — a separate `tuner_admin` connection is
  opened *only* inside the approved `propose_ddl` path.
- **Caps + kill switch** — env-tunable, never removable.

## Files

```
main.py            CLI: --query / --sql / --runaway / --resume / --interactive / cap overrides
agent_loop.py      THE PATTERN: plan-act-observe controller, finish-claim verification, HITL gate, compaction
tools.py           six tool schemas + dispatcher (run_query, explain_query, get_schema, check_equivalence, propose_ddl, finish)
ch_env.py          environment interface: tuner_agent (RO) + tuner_admin (DDL gate) clients, allow-list, timing, signatures
budget.py          turn/cost/wall-clock caps + kill sentinel; Anthropic price table
checkpoint.py      LoopState + per-run JSON state (save every turn; --resume reuses session_id)
queries.py         bad-query catalog (q1/q2/q3) + expected-turn bands
prompts.py         managed prompts (query-tuner-system v1/v2, query-tuner-goal) + local fallbacks
langfuse_config.py v3 wiring (typed observe(), trace_context(), scores, get_prompt(), flush())
sql/init/          tuning-lab schema, 30M-row seed, sandboxed users/grants
scripts/           seed_prompts / seed_dataset / seed_monitors / seed_all / run_experiment / run_live_demo
tests/             LLM-free + DB-free (budget, termination, checkpoint, ch_env)
DEMO_SCRIPT.md     presenter runbook (Frame/Show/Land/Ask)
```

Root-level companions: `evaluators/runaway-loop-guard.ts` (seeded by
`scripts/seed-code-evaluators.sh`) and `scripts/seed-query-tuner-evaluators.sh`
(the managed goal-drift judge).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `TUNER_MAX_TURNS` | 15 | backstop only |
| `TUNER_MAX_BUDGET_USD` | 0.75 | backstop only (real Anthropic usage) |
| `TUNER_WATCHDOG_S` | 600 | wall-clock backstop |
| `TUNER_COMPACT_AFTER` | 6 | compact turns older than this into summaries |
| `CLICKHOUSE_TUNING_HTTP_PORT` | 8126 | host port for the tuning lab |
| `ANTHROPIC_MODEL` | claude-sonnet-4-6 | the policy model |
| `TEMPERATURE` | 0.2 | |

## Testing

```bash
pip install -r requirements-dev.txt
pytest            # LLM-free + DB-free: caps trip at boundaries, finish-claim
                  # verification, checkpoint round-trip, SQL allow-list
```

## Notes / caveats

- **Monitors are a Langfuse v4+ feature.** This stack pins Langfuse v3, which has
  no Monitors API, so `scripts/seed_monitors.py` prints the exact UI field values
  to paste into **Monitors → New Monitor**. Everything else (traces, scores,
  sessions, agent graph, code evaluators, the managed judge) is native on v3.
- **Cost calibration.** The runaway thresholds ($0.40 warn / $1.00 alert; caps
  raised to $2.50) are spec defaults. Do one calibration pass against real Sonnet
  pricing and bake the final numbers into `seed_monitors.py`.
- **Non-determinism is the point.** q2's turn count varies run to run — say so out
  loud; that variability *is* the pattern.
