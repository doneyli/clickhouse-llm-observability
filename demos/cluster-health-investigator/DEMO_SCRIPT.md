# Cluster Health Investigator — Demo Script (Orchestrator–Workers)

A ready-to-run demo of **dynamic task decomposition** (pattern #4, Orchestrator–Workers):
an LLM planner reads a symptom and decides *at runtime* how many system-table analyses to
run, fans out one worker per analysis (LangGraph `Send`), re-plans if coverage is thin,
and synthesizes an evidence-cited diagnosis of the stack's **own ClickHouse**.

- **App:** `main.py` in a terminal, run through `docker compose`; LangGraph `orchestrator → worker × N → replan-gate → synthesize` (`graph.py`)
- **Observability:** Langfuse self-hosted (`http://localhost:3001`), project `LLM Observability Demo`, trace `investigate-cluster-symptom`, tag `cluster-health`
- **Models:** planner + synthesizer `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`); workers + re-plan gate `WORKER_MODEL` (default `claude-haiku-4-5`); managed judges on the project's default eval model
- **Data:** the live `system.*` tables of `langfuse-clickhouse` (env-swappable via `TARGET_CH_*`); symptoms from [`DEMO_SYMPTOMS.md`](DEMO_SYMPTOMS.md)
- **Prompts:** `cluster-health-planner` (labels `production`, `candidate-scoped-decomposition`, `fault-overplan`), `cluster-health-worker`, `cluster-health-synthesizer`
- **Run length:** 20 min; short path is the Opening, Acts 1–3 and the Close (8 min)

> Setup lives in [README.md](README.md). The calibrated symptom catalog and the reliable
> re-plan trigger are in [`DEMO_SYMPTOMS.md`](DEMO_SYMPTOMS.md).

---

## How to run this script

It's a conversation, not a walkthrough. Every act does four things:

- **Frame** — the problem, in their terms (say this *before* you touch the screen).
- **Show** — the exact clicks / commands.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that invites them to map it to their own world.

The Ask answers tell you which act gets the time. The short path is the Opening, Acts 1–3
and the Close; add Acts 4–6 when there's appetite.

---

## 0 · Pre-flight

From the repo root:

```bash
# Clear leaked Langfuse keys: exported shell vars can send traces to another project.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL

# Langfuse (+ its ClickHouse, the investigation target) and the demo image
docker compose --profile langfuse up -d
docker compose --profile langfuse --profile demo build cluster-health

# Seed prompts and datasets (idempotent), then the managed judges from the host
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py
bash demos/cluster-health-investigator/scripts/seed_evaluators.sh
```

**Pre-run BOTH headline traces and pin them in tabs.** Planner output is
non-deterministic *by design* — never fish for the fan-out live.

```bash
# A narrow symptom (expect worker (2/2)) and a broad one (expect worker (6/6))
docker compose --profile langfuse --profile demo run --rm cluster-health \
  python main.py --symptom "One dashboard query got slow this afternoon; everything else is fine."
docker compose --profile langfuse --profile demo run --rm cluster-health \
  python main.py --symptom "Inserts got slow after last night's deploy, CPU is pinned and disk is filling."
```

Also pre-run the **re-plan trigger** (symptom #3 in `DEMO_SYMPTOMS.md`) a couple of times
and pin a trace that shows two `orchestrator` visits. For Acts 4–6, run `--fault overplan`,
`scripts/run_experiment.py` and `scripts/score_delegations.py` the day before (commands in
the acts below). Then:

```bash
python demos/cluster-health-investigator/scripts/verify_demo.py --links   # READY, plus the link for every act
```

`verify_demo.py` exits 1 when a required check fails; fix what it names before the meeting.
Its warnings name what an optional act (3–6) is still missing.

Open the narrow and broad traces in Langfuse → **Traces**, switch each to **Agent Graph →
Aggregated view**, confirm `worker (2/2)` vs `worker (6/6)`, and pin each in its own tab.

**Browser tabs ready:** Langfuse Traces (`:3001`, `demo@example.com` / `demodemo1!`), one
narrow trace, one broad trace, the re-plan trace, Dashboards, Datasets, Evaluators.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| Runtime decomposition: the model chooses the fan-out, the code does not | Act 1 |
| Tracing an agent whose shape changes every run; the plan as a first-class artifact | Act 2 |
| Bounded dynamism: the LLM decides what is missing, code decides when to stop | Act 3 |
| Monitoring fan-out as a metric (score chart, Metrics API, native ClickHouse, a Monitor) | Act 4 |
| Datasets + experiments: a planner prompt A/B that reads cost and quality together | Act 5 |
| Evaluation: managed judges, plus per-delegation scores assembled by the app | Act 6 |

---

## Opening · Can you see the shape of the work? (1 min)

**Frame.** "Your multi-agent bill is unpredictable because the *shape* of the work is
decided at runtime by a model. Can you even see how many workers ran yesterday? What one
run cost? Whether the planner over-spawned? Most teams can't — the fan-out is invisible
until the invoice arrives."

**Ask.** *"Who decides task decomposition in your agents today — your code, or your
model?"*

**Land.** "Let me show you one program producing different shapes, and how each of those
decisions becomes something you can inspect, chart and grade."

---

## Act 1 · Watch the model decide, in the terminal (2 min)

**Frame.** "Same binary, same code path. The only thing that changes is the
sentence I hand it."

**Show.**
```bash
docker compose --profile langfuse --profile demo run --rm cluster-health \
  python main.py --symptom "One dashboard query got slow this afternoon; everything else is fine."
#   → plan → 2 task(s) [slow_queries, settings_audit] | worker×2 | gate → sufficient | diagnosis…

docker compose --profile langfuse --profile demo run --rm cluster-health \
  python main.py --symptom "Inserts got slow after last night's deploy, CPU is pinned and disk is filling."
#   → plan → 6 task(s) [insert_profile, parts_pressure, merge_backlog, memory_pressure, disk_usage, slow_queries] | worker×6 …
```

**Land.** "Two workers for the narrow one, six for the broad one — the *program*
didn't change, the *model* chose the fan-out. That's dynamic decomposition. In
the supervisor demo the graph never changes; here it changes every run."

**Ask.** *"When a task's decomposition is unpredictable, is that a bug or a
feature for your workloads?"*

> **Fallback:** if a live run lands on an unexpected count, say so — that *is* the point —
> and switch to the two pinned traces from the pre-flight.

---

## Act 2 · The money shot: the Langfuse Agent Graph (2 min)

**Frame.** "Now let's see that decision as a picture — and prove the plan is a
first-class artifact you can inspect, not a black box."

**Show.**
1. Open **trace A** (narrow) → **Agent Graph → Aggregated view**: `worker (2/2)`.
2. Open **trace B** (broad) side-by-side: `worker (6/6)`. Same graph, different counters.
3. Click **`orchestrator`** → its **`output` IS the plan** — the task list,
   each with `analysis_type`, `focus`, `rationale`, plus the planner's `reasoning`.
4. Click one **`worker`** → `metadata.analysis_type`, and the nested
   **`run-system-query`** tool span with the real `system.*` SQL and `row_count`.

**Land.** "The plan is the artifact. You can evaluate it, dataset it, and A/B it
*independently* of whether the workers executed well — because bad decomposition
and bad execution are different failures with different fixes."

**Ask.** *"When your agent misbehaves, can you currently see what it **planned**,
separate from what it **did**?"*

---

## Act 3 · The re-plan loop (2 min)

**Frame.** "Dynamism needs a brake. Who decides when enough is enough?"

**Show.** Open the pinned re-plan trace (symptom #3):
- `replan-gate` (round 1) → `output: {sufficient: false, missing: [mutation_status]}`
- a **second `orchestrator` visit** emitting the delta
- a second `worker` wave, then `replan-gate` → sufficient → `synthesize-diagnosis`

**Land.** "The LLM decides *what's missing*; deterministic code decides *when to
stop* — `MAX_PLAN_ROUNDS=2`, `MAX_WORKERS_TOTAL=8`. That's the anti-runaway rail,
unit-tested, not a prayer in the prompt."

**Ask.** *"What stops your agents from spawning 50 sub-agents for a trivial
request?"*

> **Fallback:** the re-plan is not guaranteed on any one run. If there is no pinned
> two-round trace, show `should_replan()` in `graph.py` and its unit test instead.

---

## Act 4 · Fan-out is a metric: Monitor (optional, 4 min)

**Frame.** "Fan-out is cost. If it's a number, it's chartable and alertable."

**Show.**
1. **Dashboards** → chart the `worker_count` score (avg + p95 over time).
2. Run the fault live:
   ```bash
   docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --fault overplan
   ```
   → `worker_count` jumps to **8**, trace tagged `fault:overplan`.
3. Metrics API + native ClickHouse:
   ```bash
   docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/check_fanout.py
   #   avg workers/trace, avg cost/trace; exits 1 over FANOUT_THRESHOLD (the CI gate)

   docker exec -i langfuse-clickhouse clickhouse-client --user langfuse --password langfuse123 \
     --multiquery < demos/cluster-health-investigator/sql/worker_count_by_trace.sql
   ```

**Configure a Monitor** (documented step, created in the UI): Dashboards →
Monitors → New. Metric = **avg of score `worker_count`**, filter **trace name =
`investigate-cluster-symptom`**, window **1h**, threshold **`> 6`**, notify. Add a
second monitor on **avg `totalCost` per trace** for the same filter.

**Land.** "Fan-out distribution is one `GROUP BY` away — because Langfuse stores
observations in ClickHouse, the same engine the agent just diagnosed. Here it's a
chartable, alertable number, not a surprise on the invoice."

**Ask.** *"What's your budget guardrail today — a threshold, or a prayer in the
prompt?"*

> **Fallback:** the overplan run takes a while (eight workers). If time is short, open the
> `fault:overplan` trace from the day before instead of running it live.

---

## Act 5 · Fix the planner, prove it: Datasets + Experiment (optional, 4 min)

**Frame.** "If the planner over-spawns, that's a prompt problem. Prove a fix
moves cost *and* quality — and that you changed nothing else."

**Show.**
1. **Datasets** → `cluster-health/plan-quality`. Note items link to real
   `orchestrator` spans via `source_observation_id` (click through).
2. Run the A/B (the day before; it makes model calls for every item, twice):
   ```bash
   docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/run_experiment.py
   ```
3. **Datasets → Runs** → compare `planner-production` vs
   `planner-candidate-scoped-decomposition`: the table reads coverage
   (`synthesis_quality`) *and* `avg_worker_count` together.

**Land.** "Variable isolation — only the planner prompt moved. The candidate is
better *and* cheaper, or the tradeoff is exposed in one table. That's how you
ship a decomposition change with confidence."

**Ask.** *"How do you prove a prompt change didn't just move the cost somewhere
you're not looking?"*

> **Fallback:** source-linked items exist only after
> `scripts/seed_datasets.py --from-traces N`; on a fresh seed every item is a golden one.
> Show the golden criteria instead.

---

## Act 6 · Who grades the delegations: Evaluate (optional, 4 min)

**Frame.** "Outcome quality is one thing. Was *each individual delegation*
appropriate? Langfuse's managed judges see one observation at a time — they can't
pull the plan and sibling workers into one evaluation."

**Show.**
1. **Evaluators** → `diagnosis-coverage` scoring the `synthesize-diagnosis`
   generation (coverage + non-duplication), and `plan-scaling` scoring the
   `orchestrator` plan JSON directly.
2. The workaround beat:
   ```bash
   docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/score_delegations.py
   ```
   → walks the observation tree, assembles plan + this worker + its siblings,
   and pushes `delegation_quality` **onto each worker observation**.
3. **Traces** → filter `delegation_quality < 0.5` → you're triaging individual
   bad delegations inside a dynamic fan-out.

**Land.** *"Per-worker 'was each delegation appropriate' scoring is not natively
assembled — Langfuse won't pull sibling worker observations into one evaluator —
so the application assembles the trajectory and pushes scores through the Scores
API. Thirty lines closes the gap."*

**Ask.** *"For your multi-agent runs, can you point at one sub-agent and say 'that
delegation was the bad one'?"*

> **Fallback:** judge scores land a minute or so after a run, and only on runs made after
> the judges were seeded. If none show, open the Evaluators page and the `delegation_quality`
> scores from the day before.

---

## Close · The shape of the work, made visible (1 min)

Three takeaways:
1. **Dynamic decomposition** is the pattern for unpredictable work — and it makes
   cost/shape a *runtime variable*.
2. **Langfuse makes the plan, the fan-out, and every delegation individually
   inspectable and scoreable.**
3. **It's all in ClickHouse** — the same engine the agent just diagnosed.

Hand them the repo: `demos/cluster-health-investigator` runs against any ClickHouse by
setting `TARGET_CH_*`, with the steps in the README.

---

## Show me the code

**1 · The fan-out is computed at runtime — `graph.py:273`**
`assign_workers()` returns one `Send("worker", …)` per planned task, and
`graph.py:524` wires it as a conditional edge. The edge list does not exist until the
planner has answered.

**2 · The plan is the orchestrator's output — `graph.py:350`**
`obs.update(output=plan.model_dump())` puts the whole plan on the `orchestrator` agent span,
which is what Act 2 clicks and what the `plan-scaling` judge reads.

**3 · Worker identity in metadata, not the name — `graph.py:368`**
Every worker span is named `worker`, so the Aggregated Agent Graph collapses them into
`worker (N/N)`; `analysis_type` and `focus` go in metadata.

**4 · The LLM decides what, code decides when to stop — `graph.py:108` and `graph.py:133`**
`enforce_guards()` dedupes and caps the plan to `MAX_WORKERS_TOTAL`; `should_replan()`
re-plans only when the gate says insufficient *and* both bounds allow it.

**5 · Fan-out as a score — `graph.py:430`**
The terminal node writes `worker_count` and `plan_execution_complete` as trace scores,
which is what the Act 4 chart and Monitor read.

**6 · One trace per symptom — `graph.py:541`**
`trace_context()` (`langfuse_config.py:81`) sets the trace name, session and tags with
`propagate_attributes`, so every observation of the run carries them.

**7 · The planner never writes SQL — `ch_client.py:104` and `analysis_catalog.py:39`**
Queries run with `readonly=1` after a SELECT-only check; the planner's `focus` is
sanitised into a comment, never the query body.

**8 · The app assembles the trajectory — `scripts/score_delegations.py:83`**
It gathers the plan, each worker and its siblings, asks a judge, and posts
`delegation_quality` on the worker observation (`scripts/score_delegations.py:100`).

---

## Talking points & objections

- **"The extra planning call costs money."** True — show it as a measurable
  overhead right in the trace (the `plan-analyses` generation). You're trading a
  cheap planning call for not over-spawning expensive workers.
- **"Why not just parallelize statically?"** Because you *cannot know in advance*
  how many analyses a symptom needs. Static Parallelization (P3) hardcodes the
  fan-out; this decides it per input. Show the P3-vs-P4 contrast: `brand-promo`
  has orchestrator vocabulary but a static graph — its tree never changes.
- **"Non-determinism breaks testing."** Correct — that's why evaluation shifts to
  *outcome grading* (diagnosis coverage) + *structural checks*
  (`plan_execution_complete`, `worker_count`) rather than step-by-step assertion.

---

## Known limits

- "The worker counts I quoted are calibrated ranges, not guarantees. Only the overplan
  fault is deterministic, which is why I pre-ran the traces."
- "The re-plan in Act 3 happens often for that symptom, not every time."
- "The Monitor is a step I create in the UI; nothing here provisions it for you."
- "The judge setup writes to a self-hosted Langfuse's database. On Langfuse Cloud you'd
  create the same two judges in the UI."
- "The agent diagnoses; it never changes the cluster. It can only pick from ten vetted,
  read-only analyses."

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

```bash
# Fresh varied-shape traces (one session), plus one overplan run
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/run_live_demo.py --with-fault

# Re-seed everything (idempotent); judges from the host
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py --with-traces
bash demos/cluster-health-investigator/scripts/seed_evaluators.sh

python demos/cluster-health-investigator/scripts/verify_demo.py   # READY again
```
