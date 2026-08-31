# The AI Engineering Loop — mapped to this repo

This repo is a working reference for the full **[AI Engineering
loop](https://langfuse.com/academy/ai-engineering-loop)** on ClickHouse-backed
Langfuse — not just tracing. Every demo here plugs into the same loop; this page
maps each step to the concrete code, scripts, and Langfuse pages that implement
it across the whole stack.

> You can't unit-test your way to confidence with probabilistic LLM outputs. The
> loop is how you improve systematically: observe production, learn from it, and
> ship improvements you can trust — then repeat.

```
                            ┌─────────────────────────────────────┐
                            │               DEPLOY                │
                            │   prompt labels · GitHub CI/CD      │
                            └───────────────▲───────────┬─────────┘
   ── ONLINE (understand production) ──     │           │   ── OFFLINE (improve) ──
                                            │           ▼
   ┌───────────┐     ┌───────────┐     ┌────┴──────┐  ┌────────────┐  ┌───────────┐
   │  1 TRACE  │ ──► │ 2 MONITOR │ ──► │ 3 DATASETS│─►│4 EXPERIMENT│─►│ 5 EVALUATE│
   └───────────┘     └───────────┘     └───────────┘  └────────────┘  └───────────┘
        ▲                                                                    │
        └──────────────  ship a change → new traces → repeat  ◄─────────────┘
```

## The apps

| App | What it is | Instrumentation |
|-----|-----------|-----------------|
| `demos/text-to-sql/` | NL → SQL over ClickHouse via MCP — the P1 **prompt-chaining-with-gates** demo (deterministic catalog gate + hybrid grounding gate, bounded retry, abort/escalate) | LangChain + Langfuse `CallbackHandler` |
| `demos/vector-rag/` | RAG over ChromaDB | LangChain + Langfuse `CallbackHandler` |
| `demos/agentic-rag/` | Self-correcting RAG on ClickHouse-native vectors | LangGraph + Langfuse SDK |
| `demos/query-router/` | Front-door classification-dispatch over the other demos (Pattern 2: Routing) — the cleanest full-loop story after real-estate, at a fraction of the size | raw Anthropic SDK + `httpx` + Langfuse SDK |
| `demos/cluster-health-investigator/` | Orchestrator–workers: a planner LLM decides fan-out at runtime (LangGraph `Send`) and diagnoses the stack's own ClickHouse | LangGraph + Langfuse SDK |
| `librechat/` | Shared chat frontend | LibreChat native Langfuse tracing |
| `demos/real-estate/` | Self-contained agentic concierge — the loop shown end-to-end in one place | Langfuse SDK |
| `demos/brand-promo-multi-agent/` | Multi-agent promo-planning assistant (standalone): synthetic history, online + offline evals, persona dashboards | LangGraph + CrewAI + Langfuse `CallbackHandler` |
| `demos/slow-query-tuner/` | Autonomous agent loop: open-ended query optimization against a live ClickHouse lab (Pattern #7) | Raw Anthropic tool-use + Langfuse SDK (typed observations) |

## Where each loop step lives

| # | Step | Across the main stack | Deep-dive example |
|---|------|----------------------|-------------------|
| 1 | **Trace** | All apps emit full traces (prompts, tools, retrieval, cost) to the Langfuse project | `demos/agentic-rag/graph.py`, `demos/text-to-sql/sql_pipeline.py` |
| 2 | **Monitor** | **LLM Observatory** dashboard (`dashboard/`, `:8005`); code + LLM-judge scores; **👍/👎 user feedback** — LibreChat thumbs → `user-feedback` score natively (v0.8.6+), and the `demos/real-estate/` portal | **fan-out / cost** monitoring in `demos/cluster-health-investigator/` (`worker_count` trace score + `scripts/check_fanout.py` Metrics API gate + `sql/worker_count_by_trace.sql`) |
| 3 | **Build datasets** | `scripts/seed-datasets.py` (coding-quality + security datasets); production traces → dataset from the UI | `demos/real-estate/` 10-item eval set |
| 4 | **Experiment** | `scripts/run-experiments.py` — compare **models / datasets** on the eval sets | **prompt-variant** experiments: `demos/real-estate/` + `agentic-rag` |
| 5 | **Evaluate** | Deterministic **code evaluators** (`evaluators/*.ts`, seeded by `scripts/seed-code-evaluators.sh`) + **LLM-as-a-Judge** (`scripts/seed-llm-judge-evaluators.sh`) | human **annotation** queue in `demos/real-estate/` |
| ⟳ | **Deploy** | **Prompt management by label** — apps fetch prompts from Langfuse at runtime with a local fallback: `agentic-rag` (`scripts/seed-langfuse-prompt.py`), `text-to-sql` + `vector-rag` (`scripts/seed-app-prompts.py`). Promote a label to ship a prompt with no redeploy. | **GitHub CI/CD quality gate** — promoting a prompt fires a workflow that re-runs the eval set and blocks a regression: `demos/real-estate/cicd/` |

### `demos/query-router/` closes the whole loop on the cheapest surface (Pattern 2: Routing)

The front-door router is a one-LLM-call classifier, so it demonstrates every
loop step at a fraction of a full agent's size:

1. **Trace** — the router decision is its own `route-query` **generation**
   (`{route, confidence, rationale}`, `metadata.route`, prompt-linked) under a
   stable `route-and-dispatch` trace, with exactly one handler's full subtree
   nested beneath it (SDK v3 distributed tracing across services).
2. **Monitor** — a **Router Ops** dashboard: route-distribution-over-time
   (drift), fallback rate, avg `router_confidence`, misroute rate — seeded with
   14 days of history by `scripts/seed-router-history.py`.
3. **Datasets** — `query-router-accuracy` (`scripts/seed-router-dataset.py`);
   production misroutes pinned via `source_observation_id` on the `route-query`
   generation.
4. **Experiment** — `scripts/run-router-experiment.py` varies ONLY the router
   prompt label/model, scoring `route-match` + run-level `avg_route_accuracy`
   (`--ci` gate).
5. **Evaluate** — deterministic `evaluators/route-match.ts` (code) +
   categorical `route-plausibility` LLM judge (`scripts/seed-router-judge.sh`);
   misroutes recorded as post-hoc **scores** (`routing_correct`), never
   retroactive tags. **Deploy** = promote the router prompt's `production` label.

## The Deploy node across the stack

Historically the apps hard-coded their prompts. Now the LLM apps fetch prompts
**by label** (`production`) from Langfuse at runtime, each with a hard-coded
fallback so they still run if Langfuse is unreachable, and each **links the
fetched prompt version to the generation** (so quality ties back to a version):

```bash
# Seed the managed prompts (idempotent), then edit/promote them in the UI:
python scripts/seed-app-prompts.py        # text-to-sql-analysis / -response, vector-rag-generation
python scripts/seed-langfuse-prompt.py    # agentic-rag-generation (v1 + v2 production)
```

Editing a prompt in the Langfuse UI — or promoting a new version to `production`
— changes the app's behaviour on the next run with **no code change**. That
promotion is **gated by CI**: Langfuse fires a `repository_dispatch`,
[`.github/workflows/langfuse-prompt-ci.yml`](.github/workflows/langfuse-prompt-ci.yml)
re-runs the eval dataset against the changed version, and the build fails if any
run-level mean drops below `demos/real-estate/cicd/thresholds.json` — so a
regressing prompt never reaches the deploy job. Setup and the "show the gate
blocking a bad prompt" demo path are in
[`demos/real-estate/cicd/`](demos/real-estate/cicd/README.md).

## User feedback (Monitor) — how LibreChat's thumbs reach Langfuse

LibreChat's native 👍/👎 (`PUT /api/messages/:conv/:msg/feedback`) writes a
Langfuse `user-feedback` score **natively** (v0.8.6+, `packages/api/src/langfuse/
feedback.ts`): a BOOLEAN score (`value` 1/0, id `feedback-<traceId>`) on the
answer's trace, deleted when the user retracts the rating — so real user
judgement sits next to the automated evals. It's activated by the same
`LANGFUSE_*` env vars that turn on tracing; no extra service is required.
(Earlier builds lacked this, so the demo previously reconstructed the score with
an nginx-mirrored `feedback-bridge` sidecar — removed now that it's native.)

## The loop, end-to-end in one demo

`demos/real-estate/` is the self-contained walkthrough that shows every step —
trace → monitor → dataset → experiment (models **and** prompts) → evaluate →
**deploy a prompt by label** → repeat — with a presenter runbook. Start there to
see the whole loop close in ~20 minutes, then map the same pattern onto the main
stack using the table above.

## The loop for an autonomous agent — `demos/slow-query-tuner/`

Pattern #7 (open-ended plan-act-observe) maps onto the loop differently from a
bounded pipeline, because the step count is agent-decided:

- **Trace** — a variable-depth `tune-clickhouse-query` trace: `plan-next-action`
  (generation) → `run-query`/`explain-query`/… (tool) → `assess-progress`
  (evaluator), repeated as many times as the *agent* chooses. Short vs long runs
  look visibly different in the Agent Graph Expanded view; the Aggregated view
  collapses the loop to one cycle. Pause/resume reuses one `session_id`.
- **Monitor** — the **headline**: a `max(totalCost)` Monitor on the trace name
  catches runaway loops burning tokens (the `--runaway` beat trips it); a second
  Monitor on the `turns_used` score catches "self-assessment failed, the backstop
  stopped it". (Monitors are a Langfuse v4+ feature — `scripts/seed_monitors.py`
  prints the exact UI fields on this v3 stack.)
- **Datasets** — `query-tuner/goals`, ROOT-LEVEL items only (goal in, completion
  criteria out): the same goal yields different valid trajectories, so per-step
  ground truth would be actively wrong.
- **Experiment** — `scripts/run_experiment.py` varies one component (system-prompt
  `v1-naive` vs `v2-disciplined`) with caps + tool list pinned; outcome-graded
  (pass_rate / avg_cost / avg_turns / cap_hit_rate), `--ci` gate.
- **Evaluate** — step-level code scores on live observations
  (`semantics_preserved`, `improvement_delta`), an app-assembled trajectory score
  set (`turns_used`, `run_cost_usd`, `verified_speedup`, `task_completed`,
  `trajectory_efficiency`), the deterministic `runaway-loop-guard.ts` code
  evaluator, and an independent managed `goal_drift` judge.
- **Deploy** — both system-prompt versions are Langfuse-managed;
  `v2-disciplined` carries the `production` label (flipping the label is the
  deploy beat, reused as the Experiment's variable).

## Honest scope (what's live vs documented)

| Capability | Status |
|---|---|
| Trace / Monitor / Datasets / Evaluate across the stack | **Live** |
| Prompt management (label fetch + fallback + link) — agentic-rag, text-to-sql, vector-rag | **Live** |
| Model / dataset experiments (`run-experiments.py`) | **Live** |
| Prompt-variant experiments | **Live** in demos/real-estate + agentic-rag (not the `run-experiments.py` harness) |
| User feedback → Langfuse (LibreChat thumbs + real-estate portal) | **Live** |
| GitHub repository-dispatch CI/CD **quality gate** for prompt deploys | **Live** — [`.github/workflows/langfuse-prompt-ci.yml`](.github/workflows/langfuse-prompt-ci.yml) runs the eval set on a changed prompt version and fails the build below the bar in `demos/real-estate/cicd/thresholds.json`. Requires the demo to run against **Langfuse Cloud** (a GitHub runner can't reach localhost) plus 3 one-time setup steps — see [`demos/real-estate/cicd/`](demos/real-estate/cicd/README.md) |
| Prompt **sync-to-repo** (commit each prompt version to git) | **Documented** — needs a publicly reachable webhook endpoint (`demos/real-estate/cicd/`) |
