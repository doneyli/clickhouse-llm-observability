# Analytics at Agentic Speed — design spec

Status: **built.** This is the design record; README.md and DEMO_SCRIPT.md hold the
measured results. Numbers marked *measured* were taken while writing the spec. Notes
marked *As built* record where the build departed from the original design and why.

## 1. What the demo proves

Agents don't ask a database one question. They explore: profile the data, try a
hypothesis, compare, narrow down, check again. The demo makes two claims and proves
both on a Langfuse trace:

1. **ClickHouse is fast enough to drop out of the agent's latency budget.** Every
   query the agent runs is its own span, with the time ClickHouse itself reports next
   to the round-trip time. On the timeline, the query spans are slivers beside the LLM
   calls.
2. **That speed changes what an agent can do.** An agent left alone can test hundreds
   of what-if scenarios per question, which no analyst would do by hand. And because each
   run is cheap in database time, the team can run more experiments: wider sweeps,
   more prompt versions, more models, per hour.

The second claim is the one that sells. The first is the evidence for it.

### The core idea, in one line

The LLM writes the plan once; ClickHouse runs it hundreds of times. LLM cost and
latency scale with the number of *rounds*. Database time scales with the number of
*scenarios*, and that is the cheap part.

## 2. Dataset

**Primary: US domestic flights (`ontime`), about 229M rows, 1987 onward.** *Measured:*
the copy on the public playground (`sql-clickhouse.clickhouse.com`) holds 228,792,589
rows in 8.32 GiB.

Why flights over the bigger datasets:

- **It's built for what-if questions.** Carrier, origin, destination, month, weekday,
  departure time block, delay minutes, and delay causes (carrier, weather, NAS, late
  aircraft) give the agent many levers to sweep.
- **Every audience understands it.** Late flights need no explanation.
- **It fits the presenter's service.** At 8 GiB it loads in minutes and stays in cache.

| Alternative | Size (*measured* on the playground) | When to pick it |
|---|---|---|
| GitHub events | 11.2B rows, 1.14 TiB | A technical audience, or when "billions of rows" is the headline |
| NYC taxi trips | 3.47B rows, 126 GiB | Pricing and demand what-ifs (zone × hour × fare) |
| UK property prices | 31.7M rows, 395 MiB | Already used by `text-to-sql`; too small for the speed story |

Columns the scenarios use (*measured*, `ontime.ontime` on the playground): `Year`,
`Month`, `DayOfWeek`, `FlightDate`, `Reporting_Airline`, `Origin`, `Dest`, `CRSDepTime`,
`DepTimeBlk`, `DepDelay`, `ArrDelay`, `ArrDel15`, `Cancelled`, `Diverted`, `Distance`,
`CarrierDelay`, `WeatherDelay`, `NASDelay`, `LateAircraftDelay`.

*As built:* the Cloud table holds **231,636,170 rows**, loaded entirely from the original
BTS monthly zips in `s3://clickhouse-public-datasets/ontime/original/` (`scripts/load_data.py`;
346 s for a clean load). It is 1.86 GiB compressed. `remoteSecure` from the playground
can't do the load: the `demo` user is capped at 60,000 result rows per hour. The zips
for 2025-08 through 2026-02 are HTML error pages in the bucket, so those months are
missing. Compared with the playground copy: the playground lacks 2022-04, and its
2018-01 to 2018-11 is an older BTS revision. The ground truth for the dataset questions
is computed on the Cloud copy.

### Table design matters, and the demo should say so

*Measured* on the playground, one scenario query (5 hubs, evening departures, 2015
onward, 68M rows read):

| Run | ClickHouse `elapsed_ns` |
|---|---|
| 1st (cold) | 1,675 ms |
| 2nd | 752 ms |
| 3rd (warm) | 119 ms |

Two consequences:

- **Sort key.** The playground table sorts by `(Year, Quarter, Month, DayofMonth,
  FlightDate, IATA_CODE_Reporting_Airline)`, so a filter on `Origin` scans every row in
  the date range. The demo's table sorts by the levers scenarios filter on, for example
  `(Origin, Year, Month, Reporting_Airline, FlightDate)`. Pick the final key in Phase 0
  from the actual scenario templates, and check it against the
  `clickhouse-best-practices` skill.
- **Warm-up.** The pre-flight runs each template once before the audience is watching.
  A cold first query is a real effect, not a bug, so the script mentions it rather than
  hiding it.

## 3. Architecture

```
CLI / small portal ──► Analytics agent (Python, one process, asyncio)
                         │
                         ├─ plan        LLM: question + schema → scenario grid + ONE SQL template
                         ├─ compile     deterministic: grid × template → N parameterised queries
                         ├─ sweep       N queries to ClickHouse Cloud, bounded concurrency (asyncio)
                         ├─ analyse     LLM: ranked results → answer, or "refine" with a narrower grid
                         │   └─ loops back to compile, at most MAX_ROUNDS
                         └─ answer      recommendation + the SQL and numbers behind it
                         │
                         ├─► ClickHouse Cloud service (read-only user) — the data
                         └─► Langfuse Cloud, its own project — the traces (stored in ClickHouse too)
```

- **Lives in** `demos/analytics-agentic-speed/`, standalone like `real-estate` (own venv,
  own `.env`, own `.env.example`). It doesn't depend on the root compose stack.
- **Langfuse:** a new Langfuse Cloud (US) project, e.g. `clickhouse-agent-speed`. The
  ClickHouse service is in AWS us-west-2, so US keeps the trace path short.
- **ClickHouse:** the presenter's own Cloud service, via `clickhouse-connect`'s async
  client over HTTPS. Not the public playground: it's shared, its latency isn't ours to
  show, and it has quotas.
- **Reused from the repo:**
  - from `slow-query-tuner/ch_env.py`: the measured-query pattern and the SQL allow-list;
  - from `cluster-health-investigator`: fan-out ideas;
  - the Langfuse v4 setup used everywhere else.

### Why asyncio, not a thread pool

Langfuse v4 is built on OpenTelemetry, and context flows into asyncio tasks
automatically through `contextvars`. A plain `ThreadPoolExecutor` drops the parent
span, so each query becomes its own root trace. Use `asyncio.gather` with a semaphore.
If a thread pool is ever needed, copy the context into each task explicitly.

## 4. The agent loop

Example question: *"For evening departures at the 10 busiest hubs, how much schedule
padding would lift on-time arrival above 85%, and where does it take the fewest
minutes?"*

1. **Plan (LLM, one call).** The agent reads the schema (`DESCRIBE`, plus a few
   profiling queries it chooses), then returns:
   - the levers and their values, e.g. 10 hubs × 7 buffers (0–30 min) × 4 departure
     blocks = **280 scenarios**;
   - one SQL template with typed placeholders;
   - the metric to rank on.
2. **Compile (deterministic).** Validate the template: `SELECT` only, allow-listed
   tables, typed parameters bound through ClickHouse query parameters rather than string
   formatting. Then expand the grid, capped at `MAX_SCENARIOS_PER_ROUND` (default 500).
3. **Sweep.** Run all scenarios with `SWEEP_CONCURRENCY` in flight (default 16). Each
   query returns its result plus ClickHouse's own statistics.
4. **Analyse (LLM, one call).** Read the ranked table. Either answer, or **refine**:
   zoom in on the best region with a finer grid (1-minute buffer steps, split by
   month) and loop back to step 2.
5. **Stop.** Stop when the agent says it's done, at `MAX_ROUNDS` (default 4), or at the
   budget cap (`MAX_BUDGET_USD`). A run that ends on a cap is recorded as such
   (`stop_reason`), not hidden.
6. **Answer.** The recommendation, the numbers, and the exact SQL behind it, so anyone
   can rerun it.

**Be honest about the model.** "Padding" here means counting a flight as on time if
`ArrDelay <= 15 + buffer`. That is a simplification, and the script says so. The demo
is about exploration speed, not airline operations research.

**About 300 scenarios per round, 2–4 rounds, 3–6 LLM calls in total.** The demo's
strength is that the LLM call count barely moves when the sweep gets ten times wider.

## 5. What the trace shows: database latency on every query

Trace tree (stable, low-cardinality names; parameters go in metadata, never in names):

```
analytics-agentic-speed                   agent   root: question in, answer out
├─ profile                                chain
│  └─ clickhouse.query  ×k                tool
├─ plan                                   generation  (may ask for lookups first)
├─ lookups                                chain   optional: up to 5 fact-finding queries
│  └─ clickhouse.query  ×≤5               tool
├─ plan                                   generation  (second call, after lookups)
├─ round                                  chain   round_index in metadata; round_* scores
│  ├─ compile                             span
│  └─ sweep                               chain
│     └─ clickhouse.query  ×280           tool    one per scenario
├─ analyse                                generation  → "refine" or "answer"
├─ round …                                chain
├─ analyse                                generation  → "answer" (the final answer)
└─ verify                                 chain   re-runs the winning scenario
   └─ clickhouse.query                    tool
```

*As built:* the `analyse` call that decides "answer" carries the final answer, so there
is no separate answer generation. The Anthropic SDK's own OpenTelemetry spans are
filtered out of export (`explorer/tracing.py`); otherwise every model call shows up
twice and its cost is counted twice.

### On every `clickhouse.query` span

| Field | Source | Why it's there |
|---|---|---|
| span duration | Langfuse timeline (wall clock, client side) | The visual: slivers beside LLM bars |
| `db.server_ms` | `elapsed_ns` from ClickHouse's `X-ClickHouse-Summary` response header (*measured*: present) | **The database's own number.** The headline. |
| `db.roundtrip_ms` | client wall clock | Shows what the network adds |
| `db.read_rows`, `db.read_bytes` | same header | Proves the work was real: millions of rows, not a cache hit |
| `db.rows_per_sec` | derived | The scale line for the audience |
| `db.query_id` | response header | Lets the presenter find the query in `system.query_log` |
| `scenario.*` | the grid values | Which scenario this was (hub, buffer, block) |
| `db.system` = `clickhouse` | constant | OpenTelemetry database convention |

Input is the rendered SQL and output is the result row(s). Overall input and output go
on the root observation (`root.update(input=…, output=…)`), per the repo's v4
convention.

### `db.server_ms` as a score, too

Langfuse's Metrics API can't group by metadata, so a number that lives only in metadata
can't be charted. Each query span also gets an observation-level numeric score
`db_server_ms`. That makes the number visible on the span in the trace view, and
filterable and chartable in dashboards. Volume: one score per query, about 300–1,200 per
trace. That's fine for a demo. If it becomes a problem, keep only the round-level
aggregates.

### Network vs database: say it out loud

When the agent runs on a laptop, round-trip time includes the trip to us-west-2.
Showing `db.server_ms` next to the span duration makes the split explicit: the gap is
network, not database. For the cleanest numbers, run the agent from a host in the same
region (Phase 5, optional).

### Round and trace scores

| Score | Level | Meaning |
|---|---|---|
| `scenarios_evaluated` | round, trace | Number of scenarios run |
| `db_time_total_ms` | round, trace | Sum of `db.server_ms` |
| `db_p95_ms` | round, trace | p95 of `db.server_ms` |
| `sweep_wall_ms` | round | Wall-clock time for the round's sweep (shows the effect of concurrency) |
| `db_wait_ms_total` | trace | Wall time the agent spent waiting on ClickHouse (sweeps count once, not per query) |
| `db_time_share` | trace | `db_wait_ms_total` ÷ run wall time. **The punchline**: expected to be a few percent. (Summed server time can exceed wall time when queries run in parallel, so it isn't the numerator.) |
| `scenarios_per_minute` | trace | Throughput |
| `llm_calls` | trace | Should stay at 3–6 whatever the sweep width |
| `stop_reason` | trace (categorical) | `answered` / `max_rounds` / `budget` |
| `recommendation_grounded` | trace (boolean, deterministic) | Rerun the recommended scenario's SQL and compare it with the number the answer states |

## 6. Experiments: more of them, per hour

This act ties the speed story to the Langfuse loop.

- **Dataset:** 12–20 ops questions in a Langfuse dataset. Each item's expected output
  is computed by a **reference SQL query**, so ground truth is exact and deterministic,
  with no LLM judge needed.
- **Experiment A, sweep breadth (the hero).** Same prompt and model, with
  `MAX_SCENARIOS_PER_ROUND` = 10 vs 300. Hypothesis to verify: the wide sweep finds the
  right answer far more often, at near-identical LLM cost and only slightly longer wall
  time. **Breadth is a knob you can afford to turn up.**
- **Experiment B, prompt or model version.** Two prompt versions (by Langfuse prompt
  label) or two models, the usual comparison, to show how fast a full dataset run
  completes.
- **Scores per item:** `answer_correct` (deterministic, against the reference), plus
  the latency scores above. The experiment-compare view shows accuracy against
  `db_time_total_ms` side by side.
- **How it runs:** `dataset.run_experiment(...)` (SDK v4). Note the repo's known
  footgun: `run_experiment` drops items whose task raises. The task wrapper catches
  errors, records them as a score and returns them, so an item is never silently
  missing.

*As built: the first run disproved the hypothesis as designed, and that's what fixed
the design.* With unlimited rounds (4) and unrestricted lookups, 10 scenarios per round
scored the same as 300: 93% vs 93%, at about the same cost and wall time. The narrow
agent got there two ways:

- **Lookups as a side door.** Before planning, the agent may run up to 5 fact-finding
  queries. For argmax questions it wrote a lookup that ranked the whole space by the
  question's own metric in one `GROUP BY`, and the sweep only confirmed the winner.
- **Bisecting.** On threshold questions it spent more rounds instead: 4.8 model calls
  per question vs 3.9.

Neither tests breadth. The fix keeps the agent's design consistent: lookups find *who*
is in the population, and the sweep *measures*. Three rules, all enforced in code
(`explorer/scenarios.py`), not just in the prompt:

1. **Lookups list candidates only.** They may not read outcome columns (delays,
   cancellations, diversions, taxi and air time) or compute metrics (avg, sum,
   quantiles, `*If`). A lookup that does is rejected and not run, and the model is told why.
2. **Templates measure one scenario.** Search functions (`arrayFirst`, `range`,
   `argMax`, `groupArray`, …) are rejected. This was already a prompt rule after the
   hero's first run solved all 61 buffers inside one query.
3. **Experiment A holds rounds fixed at 2 for both breadths.** A round is a model call
   (10–25 s); a scenario is a ClickHouse query (milliseconds). Comparing breadth at
   equal rounds asks the real question: in the same model time, what does more
   database work buy?

*As built, the rerun with those rules* (15 questions, Claude Opus 5.5, 2 rounds each):
10 scenarios per round scored **40%**, 300 per round scored **80%**. The wide runs did
22× the scenarios (419 vs 19 per question) with fewer model calls (3.6 vs 4.1), 11 s
more wall time (66 vs 55 s) and $0.026 more per question ($0.144 vs $0.118). ClickHouse
was 6.7% of wall time vs 2.8%. The hypothesis holds at equal rounds.

*Found after that rerun:* four question texts (`q-padding`, `q-late-departure-block`,
`q-padding-carrier`, `q-connection-window`) ranked airports "of 2019" without saying to
measure 2019, and on all four both breadths' templates had no `Year` filter: every year
measured, ~4.4M rows per scenario instead of one year's slice. The comparison stays fair
(same handicap on both sides), but one wide-run miss (LGA/OO, 40 vs 41 minutes) is most
likely this. Every text now opens with its measured period, enforced by
`tests/test_questions.py::test_text_opens_with_the_measured_period`. Ground truth is
unchanged. The 40% vs 80% figures predate the fix; rerun before quoting them.

## 7. Concurrency act (optional)

Run 10–50 agents at once, each on a different question. A dashboard shows `db_p95_ms`
staying flat while the number of agents grows, and query volume climbing. This is the
"many agents on one database" story. Measure on the presenter's service tier first, and
show the real curve, including where it bends.

## 8. Dashboards (seeded as code)

One Langfuse custom dashboard, created through the API like `real-estate`'s:

- **DB vs LLM time per trace** (stacked: `db_time_total_ms` vs generation latency)
- **`db.server_ms` distribution** (p50 / p95 / p99 of the score)
- **Scenarios per run** and **scenarios per minute**
- **LLM calls per run vs scenarios per run**: the flat line against the rising one
- **Experiment A**: accuracy by sweep breadth

## 9. Safety rails

- **Read-only database user.** `readonly=2`, with grants on the demo database only.
- **Per-query limits** sent with every query: `max_execution_time`,
  `max_rows_to_read`, `max_result_rows`.
- **SQL allow-list and typed parameters.** The template is validated once and values
  are bound as ClickHouse query parameters, never interpolated.
- **Caps:** scenarios per round, rounds, budget in USD, and wall clock. Each records a
  distinct `stop_reason`.
- **Network:** the service's IP access list must include wherever the demo runs (see
  open decisions).

## 10. Demo script outline (about 15 minutes)

1. **Frame (1 min).** Agents explore; every exploration is a query; query speed sets
   how far an agent can look.
2. **Show one run (5 min).** Ask the padding question live. Open the trace and expand a
   sweep: 280 query spans, each with `db.server_ms` and rows read. Point at
   `db_time_share`.
3. **The agent left alone (3 min).** Round 2 zooms in without anyone asking. Show the
   finer grid and the change in the answer. Count the LLM calls: still five.
4. **More experiments (4 min).** Experiment A compare view: narrow vs wide sweep,
   accuracy vs DB time. "Breadth is a knob you can afford to turn up."
5. **Land (1 min).** The traces you just looked at live in ClickHouse too, via
   Langfuse.
6. **Optional: concurrency (2 min).**

The full script follows the repo's demo-script style in `DEMO_SCRIPT.md`, written once
measured numbers exist.

## 11. Phases

| Phase | Work | Exit criterion |
|---|---|---|
| 0 | Load `ontime` into the Cloud service. Try the public S3 copy first (verify the path against the ClickHouse docs), fall back to `remoteSecure` from the playground. Choose the sort key; create the read-only user. | Row count matches the source; a representative template runs warm in under 100 ms server time (target, to verify) |
| 1 | Measured-query client: async `clickhouse-connect`, summary-header parsing, limits, allow-list, typed parameters. Unit tests. | `db.server_ms` matches `system.query_log` for the same `query_id` |
| 2 | Agent loop (plan → compile → sweep → analyse → refine) with v4 tracing; span tree as in §5. | One run produces one trace with every query nested correctly (no orphan roots), and an observation count of at least the scenario count |
| 3 | Scores and dashboard seeding. | All §5 scores present on a live trace; dashboard renders |
| 4 | Dataset with reference SQL, Experiments A and B. | Both experiments complete on Cloud; no dropped items |
| 5 | Concurrency run, optional same-region run, `DEMO_SCRIPT.md` with measured numbers, README, `.env.example`. | Script rehearsed end to end on a fresh project |

Verification rule from past demos: **assert on what Langfuse holds, not on exit codes.**
Read the trace back and count observations and scores. A 200 response on an empty trace
is not a pass. `scripts/verify_trace.py` does this.

*As built:* Langfuse Cloud removed `GET /api/public/traces/{id}` and the v2 scores API
on 2026-11-16 (both return HTTP 410). Read-backs use Observations v2 (cursor paging,
`fields=core,basic,usage,metadata`) and Scores v3 (cursor paging; the score's target is
in `subject` {kind, id} with `fields=core,subject`).

*As built:* the public playground is for smoke tests only. Its `demo` user allows 20
queries per normalized query hash per hour, and every scenario in a sweep shares one
hash, so a real sweep hits `QUOTA_EXCEEDED` after about 20 scenarios. The demo runs on
the presenter's own Cloud service; `EXPLORER_TARGET=playground` exists for rehearsing
without one.

## 12. Decisions (resolved during the build)

1. **ClickHouse Cloud service.** The presenter's always-on service (no idle scaling, so
   no cold start mid-demo). Its IP access list must include the demo machine: add the
   venue's address before presenting.
2. **Dataset.** Flights.
3. **Model.** Claude Opus 5.5 (`claude-opus-5-5`) at `medium` effort for both plan
   and analyse, with structured outputs (`output_config.format`). Forced `tool_choice`
   returns a 400 on this model. Override with `EXPLORER_MODEL` / `EXPLORER_EFFORT`.
4. **Front end.** CLI (`main.py`) prints the sweep live; Langfuse is the visual.
5. **Per-query `db_server_ms` scores:** on by default (`--no-query-scores` turns them off).
