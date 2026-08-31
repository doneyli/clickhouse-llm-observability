# Cluster Health Investigator — Demo Script (Orchestrator–Workers)

A ready-to-run demo of **dynamic task decomposition**: an LLM planner reads a
symptom and decides *at runtime* how many system-table analyses to run, fans out
one worker per analysis (LangGraph `Send`), re-plans if coverage is thin, and
synthesizes an evidence-cited diagnosis of the stack's **own ClickHouse**.

- **Pattern:** #4 Orchestrator–Workers (the repo's hardest pattern gap)
- **Agent:** LangGraph — `orchestrator → worker × N → replan-gate → synthesize` (`graph.py`)
- **Investigation target:** `langfuse-clickhouse` (env-swappable via `TARGET_CH_*`)
- **Observability backend:** Langfuse (`http://localhost:3001`), trace tag `cluster-health`
- **Run length:** ~20 min full; Acts 1–3 as the 8-min short path

> The graph, catalog, and Langfuse wiring live in
> `demos/cluster-health-investigator/`. The calibrated symptom catalog + the
> reliable re-plan trigger is [`DEMO_SYMPTOMS.md`](DEMO_SYMPTOMS.md).

---

## How to run this script

Every act does three things: it **frames** a problem the audience already has,
**shows** how the platform answers it, and **lands** the benefit — then hands a
**question** back to the room.

- **Frame** — the problem, in their terms (say this *before* you touch the screen).
- **Show** — the exact clicks / commands.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that invites them to map it to their own world.

The short path is Acts 1–3; add 4–6 when there's appetite.

---

## 0 · Pre-flight (do this BEFORE the meeting)

```bash
# CRITICAL: clear leaked Langfuse keys — exported shell vars override .env and 401 silently.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Langfuse (+ its ClickHouse) and the demo profile
docker compose --profile langfuse up -d
docker compose --profile langfuse --profile demo build cluster-health

# Seed prompts, datasets, and managed judges (idempotent)
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py
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

Open both traces in Langfuse → **Traces**, switch each to **Agent Graph →
Aggregated view**, confirm `worker (2/2)` vs `worker (6/6)`, and pin each in its
own tab. Also pre-run the **re-plan trigger** (symptom #3 in `DEMO_SYMPTOMS.md`)
a couple of times and pin a trace that shows two `orchestrator` visits.

**Browser tabs ready:** Langfuse Traces (`:3001`, `demo@example.com` /
`demodemo1!`), one narrow trace, one broad trace, the re-plan trace,
Dashboards, Datasets, Evaluators.

---

## Opening · Frame (no screen)

> "Your multi-agent bill is unpredictable because the *shape* of the work is
> decided at runtime by a model. Can you even see how many workers ran yesterday?
> What one run cost? Whether the planner over-spawned? Most teams can't — the
> fan-out is invisible until the invoice arrives."

**Ask:** *"Who decides task decomposition in your agents today — your code, or
your model?"*

---

## Act 1 · Watch the model decide (terminal)

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

**Ask:** *"When a task's decomposition is unpredictable, is that a bug or a
feature for your workloads?"*

---

## Act 2 · The money shot (Langfuse Agent Graph)

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

**Ask:** *"When your agent misbehaves, can you currently see what it **planned**,
separate from what it **did**?"*

---

## Act 3 · The re-plan loop

**Frame.** "Dynamism needs a brake. Who decides when enough is enough?"

**Show.** Open the pinned re-plan trace (symptom #3):
- `replan-gate` (round 1) → `output: {sufficient: false, missing: [mutation_status]}`
- a **second `orchestrator` visit** emitting the delta
- a second `worker` wave, then `replan-gate` → sufficient → `synthesize-diagnosis`

**Land.** "The LLM decides *what's missing*; deterministic code decides *when to
stop* — `MAX_PLAN_ROUNDS=2`, `MAX_WORKERS_TOTAL=8`. That's the anti-runaway rail,
unit-tested, not a prayer in the prompt."

**Ask:** *"What stops your agents from spawning 50 sub-agents for a trivial
request?"*

---

## Act 4 · Fan-out is a metric (Monitor)

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

**Ask:** *"What's your budget guardrail today — a threshold, or a prayer in the
prompt?"*

---

## Act 5 · Fix the planner, prove it (Datasets + Experiment)

**Frame.** "If the planner over-spawns, that's a prompt problem. Prove a fix
moves cost *and* quality — and that you changed nothing else."

**Show.**
1. **Datasets** → `cluster-health/plan-quality`. Note items link to real
   `orchestrator` spans via `source_observation_id` (click through).
2. Run the A/B:
   ```bash
   docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/run_experiment.py
   ```
3. **Datasets → Runs** → compare `planner-production` vs
   `planner-candidate-scoped-decomposition`: the table reads coverage
   (`synthesis_quality`) *and* `avg_worker_count` together.

**Land.** "Variable isolation — only the planner prompt moved. The candidate is
better *and* cheaper, or the tradeoff is exposed in one table. That's how you
ship a decomposition change with confidence."

**Ask:** *"How do you prove a prompt change didn't just move the cost somewhere
you're not looking?"*

---

## Act 6 · Who grades the delegations (Evaluate)

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

**Ask:** *"For your multi-agent runs, can you point at one sub-agent and say 'that
delegation was the bad one'?"*

---

## Closing

Three takeaways:
1. **Dynamic decomposition** is the pattern for unpredictable work — and it makes
   cost/shape a *runtime variable*.
2. **Langfuse makes the plan, the fan-out, and every delegation individually
   inspectable and scoreable.**
3. **It's all in ClickHouse** — the same engine the agent just diagnosed.

Hand them the repo.

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

## Reset / re-run

```bash
# Fresh varied-shape traces (one session)
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/run_live_demo.py --with-fault

# Re-seed everything (idempotent)
docker compose --profile langfuse --profile demo run --rm cluster-health python scripts/seed_all.py --with-traces
```
