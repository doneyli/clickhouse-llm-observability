# Cluster Health Investigator — Orchestrator–Workers (dynamic decomposition)

A "ClickHouse Cluster Doctor". Hand it a symptom in plain English — *"inserts got
slow after last night's deploy and CPU is pinned"* — and an **orchestrator LLM
decides at runtime which system-table analyses the symptom warrants** (slow-query
profile? parts explosion? merge backlog? disk pressure?), spawns **one worker LLM
per analysis** via LangGraph's `Send` API, reviews coverage, optionally re-plans a
second round, and synthesizes an evidence-cited diagnosis.

The investigation target is the demo stack's **own `langfuse-clickhouse`** — the
ClickHouse that stores the traces of the diagnosis is the ClickHouse being
diagnosed. There is no fictional company and no synthetic data: the workers read the
live `system.*` tables of the running stack. Every run is a Langfuse trace whose
**shape** — worker count, analysis mix, round count — is decided by the model, not the code.

**The question it answers:** when a model decides how many sub-agents to spawn, can you
see how many ran, what one run cost, and whether the planner over-spawned?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Datasets · Experiment · Evaluate |
| **Stack** | Python · LangGraph · LangChain · ClickHouse · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 20 min · short path 8 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 20 minutes, 8-minute short path)
- **Symptom bank:** [DEMO_SYMPTOMS.md](DEMO_SYMPTOMS.md), the calibrated symptoms with worker-count ranges and the re-plan trigger

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| **Runtime task decomposition** | A planner LLM emits a structured `Plan` (list of `{analysis_type, focus, rationale}`); the number and shape of subtasks is decided per input, not compiled (`graph.py`) |
| **Dynamic fan-out** | `assign_workers()` returns `[Send("worker", task) …]`: the edge list is computed at runtime, so two runs of the same binary produce different worker counts |
| **The plan is a first-class object** | It is the `output` of the `orchestrator` agent span — the artifact you evaluate, dataset and A/B independently of worker execution |
| **Bounded dynamism (guardrail)** | The planner selects from a fixed **10-analysis catalog** (never free-form SQL); deterministic guards cap `MAX_WORKERS_TOTAL=8` and `MAX_PLAN_ROUNDS=2` |
| **Re-plan loop** | A re-plan gate (LLM judgment + deterministic stop) decides *sufficient coverage?* and dispatches a delta wave if not |
| **Fan-out as a monitorable metric** | `worker_count` is a trace score, so it is chartable, filterable and alertable; `scripts/check_fanout.py` (Metrics API) and `sql/worker_count_by_trace.sql` (straight ClickHouse) |
| **Trajectory-eval workaround** | `scripts/score_delegations.py` assembles the plan + sibling workers and pushes a per-worker `delegation_quality` via the Scores API |
| **Prompt management** | 3 managed prompts served by label with a local fallback; the planner prompt is both the experiment lever and the fault lever |
| **ClickHouse triple-duty** | ClickHouse is the **subject** (system tables), the **evidence source** (workers run real read-only SQL) and the **observability backend** (traces land in the same server) |

Loop stages covered: Trace · Monitor · Datasets · Experiment · Evaluate — see
[`../../AI_ENGINEERING_LOOP.md`](../../AI_ENGINEERING_LOOP.md).

## Who it's for

A ClickHouse SA showing a customer with **multi-agent cost and shape** concerns: "your
multi-agent bill is unpredictable because the *shape* of the work is decided at runtime by
a model". Pick it over the supervisor demo (`brand-promo-multi-agent`) when the point is
that the trace tree is *different every run*: that demo has orchestrator vocabulary but a
**static compiled graph**, so its tree is identical every run.

## What runs where

```
 you ── docker compose run --rm cluster-health python main.py      (profile `demo`, no port)
          │  LangGraph: orchestrator → worker × N (Send) → replan-gate → synthesize
          │
          ├─► langfuse-clickhouse:8123  (profile `langfuse`)  read-only system.* SELECTs
          ├─► Anthropic API              planner + synthesizer: ANTHROPIC_MODEL; workers + gate: WORKER_MODEL
          └─► langfuse-web:3000          (host: http://localhost:3001) traces, prompts, scores
                    │
                    └─ stores everything in langfuse-clickhouse — the server being diagnosed

 Langfuse project `LLM Observability Demo` (self-hosted)
   trace `investigate-cluster-symptom` · session `cluster-health-<8 hex>` (one per batch or interactive
   session; a new one per --symptom run) · tags `cluster-health`, `demo` (+ `fault:overplan`)
   scores: worker_count, plan_execution_complete (trace), coverage_sufficient (replan-gate span),
           diagnosis-coverage, plan-scaling (managed judges), delegation_quality (per worker)
```

The `cluster-health` service has no `depends_on` (`docker-compose.yaml`), because a
`demo`-profile service cannot depend on a `langfuse`-profile one. So **`--profile langfuse`
must be up** before you run it; every command below passes both profiles.

## Set up from scratch

Prerequisites: Docker and the root stack, set up with `./setup.sh`. Run these from the
repo root:

```bash
./setup.sh                          # the root stack and its .env (idempotent); also runs this demo's seed
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
docker compose --profile langfuse up -d                       # the target ClickHouse + Langfuse
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py
bash demos/cluster-health-investigator/scripts/seed_evaluators.sh   # managed judges: host only (needs docker exec)
docker compose --profile langfuse --profile demo run --rm cluster-health python main.py   # 6 runs, model calls
python demos/cluster-health-investigator/scripts/verify_demo.py   # should print READY
```

`seed_all.py` seeds the 3 prompts and the 2 datasets. Its third step, the managed judges,
writes to Langfuse's Postgres through `docker exec`, which the container cannot do: inside
the container it prints a warning and moves on. Run `seed_evaluators.sh` from the host for
the judges. `--with-traces` also runs the 8-symptom live batch (`scripts/run_live_demo.py`).

The root `.env` is the only source of `LANGFUSE_*`. The `cluster-health` service reads the
keys from `env_file`, never from the shell; `verify_demo.py` drops shell-exported
`LANGFUSE_*` before reading `.env`. Unset them anyway: other compose services interpolate
`${LANGFUSE_PUBLIC_KEY}` from the shell.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `seed_all.py`, `seed_evaluators.sh` (host), then pre-run symptoms #1, #5 and #3 from [DEMO_SYMPTOMS.md](DEMO_SYMPTOMS.md) | Planner output is non-deterministic by design: pin a narrow, a broad and a re-plan trace rather than fishing live |
| The day before | `--fault overplan`, `scripts/run_experiment.py`, `scripts/score_delegations.py` | Acts 4, 5 and 6 read their results |
| 30 minutes before | `python demos/cluster-health-investigator/scripts/verify_demo.py --links` | Prints READY and the link each act opens; warnings name what an optional act is missing |
| 5 minutes before | Open the links it printed, plus Dashboards and Evaluators | The narrow and broad traces in Agent Graph → Aggregated view, the re-plan trace, the dataset |

## Run it

```bash
C="docker compose --profile langfuse --profile demo run --rm cluster-health"
$C python main.py                                      # the 6 headline symptoms, one session; 3 + N model calls each
$C python main.py --symptom "inserts are slow and CPU is pinned since last night"   # one symptom
$C python main.py --interactive                        # type symptoms; one session
$C python main.py --fault overplan                     # deterministic max fan-out (8 workers), tagged fault:overplan
$C python scripts/run_live_demo.py --with-fault        # the 8 dataset symptoms + one overplan run
$C python scripts/check_fanout.py                      # avg workers and cost per trace, last 24 h; exits 1 over FANOUT_THRESHOLD
docker exec -i langfuse-clickhouse clickhouse-client --user langfuse --password langfuse123 \
  --multiquery < demos/cluster-health-investigator/sql/worker_count_by_trace.sql   # same fan-out, straight SQL
```

**What you see in Langfuse:** one `investigate-cluster-symptom` trace per symptom. In
Agent Graph → Aggregated view it collapses to `orchestrator → worker (N/N) → replan-gate
→ synthesize-diagnosis`, and N changes with the symptom. The `orchestrator` span's output
is the plan; each `worker` carries its `analysis_type` in metadata and a `run-system-query`
tool span with the real SQL and `row_count`. Trace scores `worker_count` and
`plan_execution_complete` land with the trace; the judge scores arrive a minute or so later.

## Evaluation

| What | Command (prefix `$C` as above) | Notes |
|---|---|---|
| Datasets `cluster-health/plan-quality` (8 items) and `cluster-health/worker-quality` (10 items) | `$C python scripts/seed_datasets.py` | Run by `seed_all.py`. `--from-traces N` adds items linked to real `orchestrator` / `worker` observations |
| Planner prompt A/B: `planner-production` vs `planner-candidate-scoped-decomposition` | `$C python scripts/run_experiment.py` | Only the planner label varies. Model calls: 8 items × 2 labels, each a full investigation plus a judge call. `--ci` exits 1 when `avg_worker_count` > `FANOUT_THRESHOLD` |
| Managed judges `diagnosis-coverage` (on `synthesize-diagnosis`) and `plan-scaling` (on `orchestrator`) | `bash demos/cluster-health-investigator/scripts/seed_evaluators.sh` | Host only; self-hosted Postgres upsert; scores new runs only (`time_scope=NEW`) |
| Per-worker `delegation_quality` | `$C python scripts/score_delegations.py` | One judge call per worker of the latest trace; `--limit N` for more traces |
| Deterministic checks | in every run | `worker_count`, `plan_execution_complete`, `coverage_sufficient` (`graph.py`) |

## The trace shape

```
trace: investigate-cluster-symptom            session: cluster-health-<uuid8>
│      scores: worker_count=N, plan_execution_complete, diagnosis-coverage*
├── orchestrator (agent)                       output: {tasks:[…N…], reasoning}  ← THE PLAN
│   └── plan-analyses (generation)             prompt-linked: cluster-health-planner
├── worker (agent) × N   ← N VARIES PER RUN     metadata: {analysis_type, focus}; score: delegation_quality*
│   ├── run-system-query (tool)                output: {sql, row_count}
│   └── interpret-findings (generation)        model: WORKER_MODEL (tiered)
├── replan-gate (evaluator)                    output: {sufficient, missing, replan}; score: coverage_sufficient
├── [round 2, only if replan=true] orchestrator → worker × M → replan-gate
└── synthesize-diagnosis (generation)          prompt-linked: cluster-health-synthesizer
```

`*` written after the run. `--fault overplan` skips `plan-analyses`: the plan is filled to
the cap in code. The LangGraph callback handler adds its own chain spans around these.

## Safety

`ch_client.py` accepts a **single SELECT/WITH statement only**, forces a `LIMIT`, caps
`max_execution_time` and sets `readonly=1`. The planner never authors SQL: it only picks a
catalog key; the worker renders a fixed template and the planner's free-text `focus` is
carried only as a sanitised SQL comment. See `tests/test_catalog_safety.py`.

## Configuration

Defaulted in `docker-compose.yaml`; override in the repo-root `.env`:

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where `verify_demo.py` and `seed_evaluators.sh` read on the host; the container uses `LANGFUSE_INTERNAL_URL` (default `http://langfuse-web:3000`) |
| `ANTHROPIC_API_KEY` | blank | Model key for every LLM call |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Planner + synthesizer (and the experiment's `synthesis_quality` judge) |
| `WORKER_MODEL` | `claude-haiku-4-5` | Worker + re-plan gate (model tiering) and `score_delegations.py` |
| `TEMPERATURE` | `0.2` | All agent LLM calls |
| `MAX_WORKERS_TOTAL` | `8` | Anti-runaway fan-out cap across all rounds; `verify_demo.py` uses it for the Act 4 check |
| `MAX_PLAN_ROUNDS` | `2` | Re-plan loop bound |
| `PLANNER_LABEL` | `production` | Planner prompt label `main.py` fetches |
| `TARGET_CH_HOST` / `_PORT` / `_USER` / `_PASSWORD` | `langfuse-clickhouse` / `8123` / `langfuse` / `langfuse123` | The investigation target; point it at another cluster in a PoC (`TARGET_CH_SECURE=true` for TLS) |
| `FANOUT_THRESHOLD` | `6` | Gate for `check_fanout.py` and `run_experiment.py --ci` |
| `LANGFUSE_EVAL_MODEL` | `claude-haiku-4-5-20251001` | Model `seed_evaluators.sh` sets as the project's default eval model, if none exists |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/cluster-health-investigator/
├── demo.yaml                 manifest: profile, coverage, commands (feeds the index)
├── README.md                 this file
├── DEMO_SCRIPT.md            the talk track, act by act
├── DEMO_SYMPTOMS.md          calibrated symptom bank (the `questions` add-on)
├── Dockerfile                image for the root compose `cluster-health` service
├── main.py                   CLI: batch of 6, --symptom, --interactive, --fault overplan
├── graph.py                  the LangGraph orchestrator-workers agent, guards, fallback prompts
├── analysis_catalog.py       the 10 vetted read-only analyses the planner picks from
├── ch_client.py              read-only ClickHouse client (SELECT-only, LIMIT, readonly=1)
├── langfuse_config.py        Langfuse wiring: trace context, typed observations, scores, prompts
├── sql/worker_count_by_trace.sql   fan-out per trace, straight from Langfuse's ClickHouse
├── scripts/
│   ├── seed_all.py           the seed entry point: prompts, datasets, judges (setup.sh runs it)
│   ├── seed_prompts.py       3 managed prompts and their labels
│   ├── seed_datasets.py      plan-quality + worker-quality datasets
│   ├── seed_evaluators.sh    2 managed judges (host, self-hosted Postgres)
│   ├── run_live_demo.py      8 symptoms in one session (+ --with-fault)
│   ├── run_experiment.py     planner prompt A/B on plan-quality
│   ├── score_delegations.py  per-worker delegation_quality via the Scores API
│   ├── check_fanout.py       avg workers / cost per trace via the Metrics API
│   └── verify_demo.py        readiness check: READY or NOT READY
├── tests/                    no services, no model calls
└── requirements.txt
```

## Tests

```bash
cd demos/cluster-health-investigator
pip install -r requirements.txt pytest
python -m pytest -q                 # no services, no model calls, a few seconds
```

- `test_catalog_safety.py` — every catalog SQL is SELECT-only and LIMIT-bounded; `focus` can't inject.
- `test_graph_shape.py` — plan of N → exactly N worker invocations; re-plan guards enforced; fault:overplan hits the cap. Skips if `langgraph` isn't installed.
- `test_plan_schema.py` — Plan round-trip; malformed plan → retry once, then abort.
- `test_verify_demo.py` — `verify_demo.py`'s checks, exit code, `.env` override, the Observations v2 → v1 fallback and the run-shape logic, against a fake Langfuse.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `langfuse-clickhouse (the investigation target) is …` fails, or workers report connection errors | The `langfuse` profile is not up: `docker compose --profile langfuse up -d` |
| `keys map to project '…' (want 'LLM Observability Demo')` | The root `.env` holds another project's keys. Re-run `./setup.sh` or fix the keys |
| Traces land in another project, or 401 on export | Shell-exported `LANGFUSE_*`: `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL` |
| `managed judge scores on the latest run: none` | Judges were never seeded (`seed_all.py` in the container skips them) or the run predates them: run `seed_evaluators.sh` on the host, then a new run |
| Same worker count for every symptom | Planner variance, or the planner fell back to the local prompt. Use the calibrated phrasings in `DEMO_SYMPTOMS.md`; check `prompt cluster-health-planner: production` passes in `verify_demo.py` |
| No second `orchestrator` visit for symptom #3 | The re-plan is not guaranteed. Run it a few times and pin a trace that shows two rounds |
| `latest run has 0 observation(s)` | The trace landed before its observations, or `langfuse-worker` stalled: wait 30 s; if it persists, `docker compose restart langfuse-worker` |

## Known limits

- **Self-hosted only.** The judge seeder writes to Langfuse's Postgres; on Cloud it prints UI steps instead. `scripts/seed_datasets.py --from-traces` and `scripts/score_delegations.py` read the legacy `/api/public/traces` endpoints, which Langfuse Cloud no longer serves.
- `seed_all.py` cannot seed the judges from inside the container, and it exits 0 even when a step fails. Read its output, then run `verify_demo.py`.
- `seed_prompts.py` skips a prompt whose `production` label already resolves, so a changed fallback prompt in `graph.py` does not reach Langfuse on a re-seed.
- Worker counts are non-deterministic by design. `DEMO_SYMPTOMS.md` gives a calibrated range for each phrasing, not a guarantee; only `--fault overplan` is deterministic.
- The Monitor in Act 4 is created by hand in the UI; nothing seeds it.
- The planner can only choose from the 10 catalog analyses. It diagnoses; it never changes the cluster.
