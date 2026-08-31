# Cluster Health Investigator — Orchestrator–Workers (dynamic decomposition)

A "ClickHouse Cluster Doctor". Hand it a symptom in plain English — *"inserts got
slow after last night's deploy and CPU is pinned"* — and an **orchestrator LLM
decides at runtime which system-table analyses the symptom warrants** (slow-query
profile? parts explosion? merge backlog? disk pressure?), spawns **one worker LLM
per analysis** via LangGraph's `Send` API, reviews coverage, optionally re-plans a
second round, and synthesizes an evidence-cited diagnosis.

The investigation target is the demo stack's **own `langfuse-clickhouse`** — the
ClickHouse that stores the traces of the diagnosis is the ClickHouse being
diagnosed. Every run is a Langfuse trace whose **shape** — worker count, analysis
mix, round count — is decided by the model, not the code.

## What it demonstrates

| Capability | How |
|---|---|
| **Runtime task decomposition** (Pattern #4) | A planner LLM emits a structured `Plan` (list of `{analysis_type, focus, rationale}`); the number and shape of subtasks is decided per-input, not compiled |
| **Dynamic fan-out** | `assign_workers()` returns `[Send("worker", task) …]` — the edge list is computed at runtime, so two runs of the same binary produce different worker counts |
| **The plan is a first-class object** | It is the `output` of the orchestrator's `agent`-typed span — the artifact you evaluate, dataset, and A/B independently of worker execution |
| **Bounded dynamism (guardrail)** | The planner selects from a fixed **10-analysis catalog** (never free-form SQL); deterministic guards cap `MAX_WORKERS_TOTAL=8` and `MAX_PLAN_ROUNDS=2` — the anti-runaway rail |
| **Re-plan loop** | A re-plan gate (LLM judgment + deterministic stop) decides *sufficient coverage?* and dispatches a delta wave if not — "the LLM decides *what*, code decides *when to stop*" |
| **Fan-out as a monitorable metric** | `worker_count` is pushed as a native trace score → chartable, filterable, alertable; `check_fanout.py` (Metrics API) + `sql/worker_count_by_trace.sql` (straight ClickHouse) close the Monitor gap |
| **Trajectory-eval workaround** | `score_delegations.py` assembles the plan + sibling workers and pushes a per-worker `delegation_quality` via the Scores API — the thing Langfuse won't natively assemble |
| **ClickHouse triple-duty** | ClickHouse is the **subject** (system tables), the **evidence source** (workers run real read-only SQL), and the **observability backend** (traces land in the same server) |

**Distinct from the supervisor demo (`brand-promo-multi-agent`)**: that has
orchestrator *vocabulary* but a **static compiled graph** — its trace tree is
identical every run. This is the demo where the trace tree is *different every
run*, and Langfuse makes that legible.

## Who it's for

A ClickHouse SA showing a customer with **multi-agent cost/shape control**
concerns: "your multi-agent bill is unpredictable because the *shape* of the work
is decided at runtime by a model — can you see how many workers ran, what one run
cost, whether the planner over-spawned?" Open this demo's `worker_count` chart.

## Quick start

```bash
# From the repo root. --profile langfuse is needed because the investigation
# target is langfuse-clickhouse itself (same precedent as the dashboard).
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY      # avoid the shell-key 401 footgun

docker compose --profile langfuse --profile demo run --rm cluster-health python main.py
```

Seed prompts / datasets / judges (idempotent; also wired into `setup.sh`):

```bash
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py
# add --with-traces to also seed varied-shape traces on a fresh stack
```

Other entry points:

```bash
# one symptom, verbose
docker compose --profile langfuse --profile demo run --rm cluster-health \
  python main.py --symptom "inserts are slow and CPU is pinned since last night"

# interactive
docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --interactive

# the Monitor trigger — deterministic max fan-out, tagged fault:overplan
docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --fault overplan
```

The **client-facing script** is [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md); the calibrated
symptom catalog (with worker-count ranges + the reliable re-plan trigger) is
[`DEMO_SYMPTOMS.md`](DEMO_SYMPTOMS.md).

## The trace shape (what the Agent Graph renders)

```
trace: investigate-cluster-symptom            session: cluster-health-<uuid8>
│      input: {symptom}   scores: worker_count=N, plan_execution_complete, diagnosis_coverage*
├── orchestrator (agent)                       output: {tasks:[…N…], reasoning}  ← THE PLAN
│   └── plan-analyses (generation)             prompt-linked: cluster-health-planner
├── worker (agent) × N   ← N VARIES PER RUN     metadata: {analysis_type, focus}; score: delegation_quality*
│   ├── run-system-query (tool)                output: {sql, row_count}
│   └── interpret-findings (generation)        model: haiku (tiered)
├── replan-gate (evaluator)                    output: {sufficient, missing, replan}
├── [round 2, only if replan=true] orchestrator → worker × M → replan-gate
└── synthesize-diagnosis (generation)          prompt-linked: cluster-health-synthesizer
```

Aggregated Agent Graph collapses this to `orchestrator → worker (N/N) →
replan-gate → synthesize-diagnosis`. Run A: `worker (2/2)`. Run B: `worker
(6/6)`. Same graph, different counters.

## AI Engineering loop coverage

**Trace · Monitor · Datasets · Experiment · Evaluate · Deploy** — see
[`../../AI_ENGINEERING_LOOP.md`](../../AI_ENGINEERING_LOOP.md).

| Stage | Artifact |
|---|---|
| Trace | typed observations; the plan is the orchestrator span's `output`; `worker (N/N)` in the Aggregated Agent Graph |
| Monitor | `worker_count` trace score; `scripts/check_fanout.py` (Metrics API, CI gate); `sql/worker_count_by_trace.sql` (native ClickHouse); Monitor config in `DEMO_SCRIPT.md` |
| Datasets | `cluster-health/plan-quality` + `cluster-health/worker-quality` (`scripts/seed_datasets.py`, `--from-traces` capture) |
| Experiment | `scripts/run_experiment.py` — planner prompt A/B; `avg_worker_count` + `synthesis_quality` per run; `--ci` fan-out gate |
| Evaluate | managed judges `diagnosis-coverage` + `plan-scaling` (`scripts/seed_evaluators.sh`); per-worker `delegation_quality` (`scripts/score_delegations.py`); in-app deterministic `plan_execution_complete` |
| Deploy | 3 managed prompts by label with local fallback (`scripts/seed_prompts.py`); the planner prompt is both the experiment lever *and* the fault lever |

## Configuration

Defaulted in `docker-compose.yaml`; override in the repo-root `.env`:

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | planner + synthesizer |
| `WORKER_MODEL` | `claude-haiku-4-5` | worker + gate (model tiering) |
| `MAX_WORKERS_TOTAL` | `8` | anti-runaway fan-out cap |
| `MAX_PLAN_ROUNDS` | `2` | re-plan loop bound |
| `TARGET_CH_HOST` / `_PORT` / `_USER` / `_PASSWORD` | `langfuse-clickhouse` / `8123` / `langfuse` / `langfuse123` | **env-swappable investigation target** — point it at a customer cluster or ClickHouse Cloud in a PoC |

## Safety

`ch_client.py` accepts a **single SELECT/WITH statement only**, forces a `LIMIT`,
caps `max_execution_time`, and sets `readonly=1`. The planner never authors SQL —
it only picks a catalog key; the worker renders a fixed template and the
planner's free-text `focus` is carried only as a sanitised SQL comment. See
`tests/test_catalog_safety.py`.

## Tests

```bash
pytest demos/cluster-health-investigator/tests    # LLM-free (mocked planner/worker/gate)
```

- `test_catalog_safety.py` — every catalog SQL is SELECT-only, LIMIT-bounded; focus can't inject.
- `test_graph_shape.py` — plan of N → exactly N worker invocations; re-plan guards enforced; fault:overplan hits the cap. (Skips if `langgraph` isn't installed.)
- `test_plan_schema.py` — Plan round-trip; malformed plan → retry-once-then-abort.
