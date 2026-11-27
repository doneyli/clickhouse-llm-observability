# Brand Promo Multi-Agent — A re-themeable promo-planning agent fleet on LangGraph and CrewAI with 50k synthetic traces, persona dashboards, online judges and a certification gate

PromoPlanner is a promo-planning assistant for a consumer-brands company: a LangGraph
orchestrator classifies the request, hands it to two CrewAI crews (research, then
strategy), runs a deterministic compliance graph and composes a campaign brief. Around it
sits a fleet of five other agents that exist only in seeded history, so Langfuse shows
the operation at fleet scale. The default company, BrandCo, is fictional, and its brands,
sales, inventory and regulators are synthetic. Every one of them comes from
`demo.config.yaml`, so the demo re-themes to another company by editing one file.

**The question it answers:**

1. When agents delegate to agents across two frameworks, which one broke the answer?
2. Can the exec, ops and engineering each get their own view of the same fleet?
3. What decides whether a new prompt or model version is allowed to ship?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy |
| **Stack** | Python · LangGraph · CrewAI · LangChain · Anthropic SDK |
| **Runs as** | Standalone: own toolchain and `.env` |
| **Langfuse** | Cloud or self-hosted · project `brand-promo-demo` |
| **Length** | 25–30 min · short path 10–12 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (25–30 minutes, 10–12-minute short path)
- **Design record:** [SPEC.md](SPEC.md), with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/EVALS_PLAN.md](docs/EVALS_PLAN.md)
- **Long-form runbook:** [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md), a 60-minute segment-by-segment version

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Multi-agent, multi-framework tracing | One `PromoPlanner` trace per run: LangGraph nodes, CrewAI agents, tasks, tools and their LLM calls in one tree (`src/observability.py`) |
| Fleet-scale history | About 50,000 synthetic traces across six agents over 30 days, from `scripts/generate_history.py` |
| Persona dashboards | Executive, Ops and Engineer dashboards over the same data, built from the checklist in `scripts/seed_dashboards.py` |
| Cost by model tier | Sonnet for orchestration and research, Opus for strategy and judging, Haiku-tier compliance in the history |
| Fail-closed compliance gate | `q2_compliance_catch` comes back REJECTED; a compliance tool error is ERROR, never APPROVED (`src/agents/compliance_agent.py`) |
| Online LLM-as-judge | Three trace judges at 10% sampling, created in the UI from the checklist in `scripts/seed_evaluators.py` |
| Golden dataset and certification gate | `promo-planner-golden-v1` (75 items), 6 deterministic and 4 judge evaluators, `--ci` exits 1 when the gate fails |
| Prompt A/B | `--system-prompt-file prompts/strategy_v2.md` against the same items and evaluators |
| Human review | The `PromoPlanner Human Review` annotation queue and the `human_brief_review` score |

## Who it's for

Teams evaluating Langfuse for agent fleets, especially with CrewAI, LangGraph or LangChain
in the stack, an incumbent tracing tool or a homegrown one, and more than one audience for
the data. Pick it over `real-estate` or `insurance-claims` when the room cares about fleet
scale, per-persona views and a release gate across mixed frameworks more than about one
agent in depth.

## What runs where

```
 you ── scripts/run_live_demo.py (Python 3.11, uv) ── no server, no ports
          │  PromoPlanner LangGraph: classify_intent → research_node (CrewAI research crew)
          │    → strategy_node (CrewAI strategy crew) → compliance_node (PromotionCompliance graph)
          │    → compose_brief         Anthropic API: claude-sonnet-4-6, claude-opus-4-7
          │  one Langfuse client per process: LangChain CallbackHandler for the graph,
          │  OpenInference CrewAI instrumentor for the crews' own LLM and tool calls
          ▼
 Langfuse project `brand-promo-demo` (Cloud or self-hosted, from .env)
   live run    trace `PromoPlanner` · session `live-<UTC timestamp>-<6 hex>` · tag `live_demo`
   experiment  trace `PromoPlanner` · session = run name · tags `experiment`, <label>
   history     traces `promo_planner_run`, `<agent>_run` · tags `synthetic`, <agent> · 30 days back
```

`scripts/generate_history.py` writes the history through the batch ingestion API with no
model calls. A batch is keyed on its inputs and its UTC day, and its trace and observation
ids derive from that key, so re-sending it upserts instead of duplicating
(`src/synthetic/history_batch.py`). `scripts/run_experiment.py` runs the same graph per dataset item with fault
injection switched off.

## Set up from scratch

Prerequisites: Python 3.11 with [uv](https://docs.astral.sh/uv/), an Anthropic API key,
and a Langfuse project named `brand-promo-demo` with its API keys. On a self-hosted
Langfuse, `scripts/setup_langfuse_project.py` can create the project if you set
`LANGFUSE_ADMIN_TOKEN`; on Cloud, create it in the UI.

```bash
cd demos/brand-promo-multi-agent
uv sync --extra dev
cp .env.example .env                              # then fill in the keys
cp demo.config.example.yaml demo.config.yaml      # optional: the example is the default
uv run scripts/seed_all.py                        # prompts, score configs, dataset, history, queue; several minutes the first time
uv run scripts/run_live_demo.py play-all          # five real runs; model calls on your Anthropic key
uv run python scripts/run_experiment.py --run-name rehearsal --sample 10 --evaluators deterministic   # the baseline run Act 5 compares with
uv run python scripts/verify_demo.py              # should print READY
```

Every seed step reconciles: it reads its assets back, writes only what is missing or
drifted, and prints created / updated / unchanged / failed counts, so a second run with
nothing changed writes nothing. `seed_all.py` exits 0 when everything is in place, 1 when
any step failed (its summary names the step), and 2 when only a manual UI step is left.
Two things the API cannot create are manual: the three dashboards and the three online
judges. `seed_all.py` reads both back and prints a checklist for whatever is missing. The
judges need an Anthropic LLM connection in the project first (Settings → LLM Connections).

The `.env` is the only source of the Langfuse keys and host: `src/config.py` sets them
from the file over any shell export, and `verify_demo.py` drops shell-exported
`LANGFUSE_*`. `./setup.sh` does not run this demo's seed.

## Before a demo

| When | Do | Why |
|---|---|---|
| Within 30 days | `uv run scripts/generate_history.py` | The history is backdated 30 days from its batch day and ages out of Act 1. A no-op while a batch from the last 7 days is complete |
| The day before | `uv run scripts/run_live_demo.py play-all`, then the rehearsal experiment below | A fresh live trace for Act 3 and a run in the Runs tab for Acts 4 and 5 |
| 30 minutes before | `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL`, then `uv run python scripts/verify_demo.py --links` | Prints READY for the full script and the link each act opens. For the 10–12 minute path, `--short-path` checks Acts 1–3 only and says so |
| 5 minutes before | Open the links it printed | Traces, the three dashboards, the dataset, the review queue, and a terminal |

## Run it

```bash
uv run scripts/run_live_demo.py play q1_happy_path            # full plan: research, strategy, compliance pass
uv run scripts/run_live_demo.py play q2_compliance_catch      # children under 12: REJECTED
uv run scripts/run_live_demo.py play q5_hallucination_catch   # invented SKUs for the evals to catch
uv run scripts/run_live_demo.py play-all                      # all five demo queries in a row
uv run scripts/run_live_demo.py query "Plan a Q4 promo for Brand C in the West"
```

Each run prints the brief, the intent, the compliance status, the tools called and the
trace URL. The query ids and their text live in `live_demo_queries` in the config.

**What you see in Langfuse:** a `PromoPlanner` trace whose root carries the user query as
input and the finished brief as output. Under it are `classify_intent`, `research_node`
and `strategy_node` with the CrewAI agents, tasks, tool calls and generations nested
inside (short names such as `DataAnalyst.SalesAndInventory.execute`), `compliance_node`
with the `PromotionCompliance` graph (`brand_check`, `regulatory_check`, `aggregate`), and
`compose_brief`. Session, tags and the `agent_name`, `customer` and `demo_run_id` metadata
come from `make_observability_run_config()`. The richer `promo_planner_run` trees in the
history model the same steps as synthetic spans; about 10% of them carry synthetic
judge-style scores for the dashboards.

## Evaluation

| What | Command | Notes |
|---|---|---|
| Rehearsal experiment | `uv run python scripts/run_experiment.py --run-name rehearsal --sample 10 --evaluators deterministic` | About 30 s by the earlier estimate; no judge cost, but the agent's own model calls run |
| Full deterministic run | `uv run python scripts/run_experiment.py --run-name full-baseline --evaluators deterministic` | All 75 items, about 3–5 min by the earlier estimate |
| Full run with judges | `uv run python scripts/run_experiment.py --run-name full-all --evaluators all` | About $10–15 and 15–20 min by the earlier estimate; four Opus judges per item |
| Prompt A/B | `uv run python scripts/run_experiment.py --run-name strategy-v2 --label strategy-v2 --system-prompt-file prompts/strategy_v2.md --sample 10` | Compare against a `--label baseline` run in Datasets → Runs |
| CI gate | `uv run python scripts/run_experiment.py --run-name ci-check --evaluators deterministic --ci` | Exits 1 when `certification_gate` fails |
| Route failures to review | `uv run python scripts/run_experiment.py --run-name flagged --sample 20 --queue-failures` | Needs the queue from `scripts/seed_annotation_queue.py` |
| Fill the review queue | `uv run scripts/seed_annotation_queue.py` | Up to 10 seeded traces with a `response-factuality` score of 0.6–0.8; never grows past 10 on re-runs |

- **Dataset:** `promo-planner-golden-v1`, 25 hand-authored items (`scripts/seed_dataset.py`)
  plus 50 generated from the config's brands, regions and retailers
  (`src/evals/dataset_builder.py`). Item ids are content hashes, so re-seeding upserts.
- **Evaluators** (`src/evals/evaluators.py`): deterministic `intent_classification_accuracy`,
  `tool_call_match`, `compliance_status_match`, `brief_contains`, `sku_validity`,
  `brief_length_sanity`; judges `tool_call_correctness`, `response_factuality`,
  `compliance_adherence`, `brief_quality` on `claude-opus-4-7`.
- **Gate:** `certification_gate` passes when every dimension present meets its threshold:
  intent ≥ 0.85, compliance ≥ 0.90, factuality ≥ 0.80 (the factuality judge only runs with
  `--evaluators all`). Thresholds are flags (`--intent-threshold` and the others).
- **Score configs:** `scripts/setup_score_configs.py` registers 20, item and run level.
- **Online judges:** `tool-call-correctness`, `response-factuality` and
  `compliance-adherence` on traces at 10%, created in the UI.

Design and rationale: [docs/EVALS_PLAN.md](docs/EVALS_PLAN.md).

## Re-theme for a customer

Copy `demo.config.example.yaml` to `demo.config.yaml` and change the customer, brand
families and SKUs, regions, retail partners, regulators, fleet agents, models and the
five demo queries. Set `langfuse.project_name` if the project is renamed;
`verify_demo.py` reads it. Then re-run the seed. The code reads only `demo.config.yaml`
and falls back to the example when it is absent.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | blank | Every model call: orchestrator, crews, judges |
| `LANGFUSE_HOST` | `http://localhost:3001` | Langfuse host or Cloud region; set from `.env` over the shell |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | blank | Keys for the `brand-promo-demo` project; set from `.env` over the shell |
| `LANGFUSE_BASE_URL` | unset | Read by the SDK and by `verify_demo.py` ahead of `LANGFUSE_HOST`; set both to the same value or leave it out |
| `LANGFUSE_ADMIN_TOKEN` | blank | Self-hosted only: lets `setup_langfuse_project.py` create the project |
| `BACKEND` | `langfuse` | The only supported backend |
| `TAVILY_API_KEY` | blank | Real market search in `get_market_trends`; canned insights without it |
| `PROMO_DISABLE_FAULT_INJECTION` | unset | Turns off the ~20% injected faults; `run_experiment.py` sets it |
| `PROMO_SYSTEM_PROMPT_OVERRIDE` | unset | Appended to the strategist's backstory; set from `--system-prompt-file` |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project_name` in the config, else `brand-promo-demo` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/brand-promo-multi-agent/
├── demo.yaml                  manifest: profile, coverage, commands (feeds the index)
├── README.md                  this file
├── DEMO_SCRIPT.md             the talk track, act by act
├── SPEC.md                    design record
├── .env.example               copy to .env
├── demo.config.example.yaml   the re-themeable company, fleet, models and demo queries
├── pyproject.toml             uv project; `uv sync --extra dev` adds the test tools
├── prompts/strategy_v2.md     the candidate strategy prompt for the A/B act
├── docs/                      ARCHITECTURE.md, EVALS_PLAN.md, DEMO_RUNBOOK.md
├── src/
│   ├── agents/                orchestrator, research and strategy crews, compliance graph
│   ├── tools/                 mock sales, inventory, market and compliance tools; fault injection
│   ├── prompts/               prompt text, also seeded to Langfuse under promo-planner/
│   ├── evals/                 evaluators, gate, dataset builder
│   ├── experiment_backends/   Langfuse experiment adapter
│   ├── synthetic/             synthetic history generator and its batch identity
│   ├── data/                  mock sales, inventory, promos, brand and regulatory rules
│   ├── observability.py       callbacks, run config, CrewAI instrumentation
│   ├── langfuse_rest.py       the seeders' REST client: errors raise, paging is bounded
│   └── config.py              config and .env loading
├── scripts/
│   ├── seed_all.py            the one seed entry point; calls the seeders below
│   ├── seed_prompts.py, seed_evaluators.py, setup_score_configs.py, seed_dataset.py,
│   │   seed_dashboards.py, generate_history.py, seed_annotation_queue.py,
│   │   setup_langfuse_project.py
│   ├── run_live_demo.py       live runs: play, play-all, query
│   ├── run_experiment.py      experiments, A/B, CI gate, review routing
│   └── verify_demo.py         readiness check: READY or NOT READY
└── tests/                     no services, no model calls
```

## Tests

```bash
cd demos/brand-promo-multi-agent
uv sync --extra dev
uv run pytest -q              # no services, no model calls, about 15 s
```

They cover the deterministic evaluators and the gate, the compliance graph's fail-closed
aggregation, the tools and their injected faults, config loading and the `.env` key
override, the observability factory and the CrewAI instrumentation, an orchestrator smoke
run with mocked model calls, the synthetic trace generator and its deterministic batches,
the trace's root input and output, every seeder's reconcile and failure paths, and
`verify_demo.py`'s checks and verdicts. The seeders and `verify_demo.py` run against
in-memory fakes that reject parameters and field groups the real API rejects.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `keys map to project '…' (want 'brand-promo-demo')` | The `.env` holds another project's keys. Put this project's keys there; shell exports are ignored |
| `seeded 'promo_planner_run' traces in the last 30 days` fails | The history aged out or was never written: `uv run scripts/generate_history.py` |
| `… fleet agents in the seeded history; missing <name>_run` | The fleet in `demo.config.yaml` changed since the history was written. Re-run `generate_history.py`; the new fleet changes the batch key |
| `seed_all.py` exits 2 | Only a manual step is left: build the dashboards or judges from the checklist it printed, then re-run |
| `0 'baseline' experiment run(s)` | Acts 4–5 have nothing to compare with: run the rehearsal experiment above, which is labelled `baseline` |
| `persona dashboards` fails | The dashboards API cannot create them. Build them from the `seed_dashboards.py` checklist, with the exact names |
| `Langfuse read failed: HTTP 404` on `/api/public/v2/observations` | A self-hosted v3 server has no Observations v2. Run against Cloud or a v4 server |
| The online judge setup fails in the UI | The project has no Anthropic LLM connection. Add one under Settings → LLM Connections |
| A live trace shows the graph but no crew generations | The CrewAI instrumentor did not start or the run exited early. Use `run_live_demo.py`, which flushes the CrewAI event bus before the SDK |
| `Some CrewAI trace events are still pending.` | The event bus did not drain in 30 s. Wait a moment and reload the trace |
| An experiment against a self-hosted server rejects `datasetVersion` | Covered by the workaround in `src/experiment_backends/langfuse_backend.py`; upgrade the SDK and server together |

## Known limits

- The 50,000-trace history and the scores on it are synthetic. Live runs and experiments
  are real executions. The history is backdated from when it was written, so it ages out
  of a 30-day view.
- The dashboards and the online judges are created by hand. `seed_dashboards.py` and
  `seed_evaluators.py` read them back and print the checklist; they do not create them.
- A history batch older than the 7-day reuse window is not replaced: the next run writes
  a fresh batch and the old one stays in the project until it ages out of view. The same
  happens when the synthetic-history settings, fleet or models change.
- Score-config descriptions cannot be updated through the API; `setup_score_configs.py`
  reports a description drift but leaves it.
- The compliance checks are keyword and regex rules over the brief. They show the shape
  of a fail-closed gate, not a policy engine.
- The market tool returns canned insights unless `TAVILY_API_KEY` is set.
- `verify_demo.py` checks that history exists for each agent, not that it reaches 50,000
  traces, and it needs a Langfuse that serves Observations v2.
- The cost and time of `play-all` and of the experiment rows above are earlier estimates,
  not measurements from a recorded rehearsal.
