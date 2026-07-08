# Implementation Plan: Brand Promo Multi-Agent Langfuse Demo

**Status:** Spec (awaiting implementer agent)
**Owner:** Doneyli De Jesus
**Target:** Run end-to-end against a local Langfuse instance at `http://localhost:3001` and produce a demo-ready environment for a customer Langfuse demo.

---

## 0. How to use this document

You are the implementer agent. Execute milestones **M1 through M12 in order**. Do not skip ahead. After each milestone, run the **Validation** step and only proceed if it passes. The plan is designed so you can work non-stop. When you hit one of the explicit `MANUAL` markers, stop and report what is blocked. Anything not marked `MANUAL` is yours to do.

Tooling defaults you must follow:
- **Language**: Python 3.12
- **Package manager**: `uv` (already installed; if not, install with `pip install uv`)
- **Lint/format**: `ruff` (config in `pyproject.toml`)
- **Test**: `pytest`
- **All scripts** must be runnable as `uv run <script>` from the demo root.
- **All configuration** comes from `demo.config.yaml` and `.env`. No hardcoded brand names, regions, models, or keys anywhere in code.
- **Voice**: comments minimal; no em dashes per project conventions.

If a library version listed below is incompatible with current PyPI (newer breaking version, package renamed, etc.), pick the latest stable that satisfies the documented interfaces and record the chosen version in the milestone log.

---

## 1. Demo architecture overview

```
                                  +----------------------------+
                                  |   demo.config.yaml         |
                                  |   .env (creds)             |
                                  +-------------+--------------+
                                                |
                                                v
+-------------------------------------------------------------------------+
|                       PromoPlanner (hero agent)                          |
|                                                                          |
|   User Query                                                             |
|       |                                                                  |
|       v                                                                  |
|   +---+---------------------------+                                      |
|   | LangGraph Orchestrator        |  routes by intent, composes brief    |
|   +---+----+------+---------------+                                      |
|       |    |      |                                                      |
|       v    v      v                                                      |
|   Research  Strategy  Compliance                                         |
|   Crew      Crew      Agent                                              |
|   (CrewAI)  (CrewAI)  (LangGraph node)                                   |
|       |       |       |                                                  |
|       +--+----+--+----+                                                  |
|          |       |                                                       |
|          v       v                                                       |
|     Tools layer (mock SAP/Salesforce/vector/regulatory + error inject)   |
+-------------------------------+-----------------------------------------+
                                |
                                v Langfuse callback handler + OTel
                                |
                       +--------+--------+
                       |   Langfuse v3   |  ClickHouse + Postgres
                       |  localhost:3001 |
                       +-----------------+
                            |
              +-------------+----------------+
              |             |                |
              v             v                v
        Persona         Online evals     Datasets +
        dashboards     (LLM-as-judge)   Experiments
        (3 per persona)
```

Hero agent runs live during the demo. Synthetic fleet agents only exist in trace history. All trace context flows through one Langfuse callback handler so the Agent Graph view nests CrewAI sub-crews under the LangGraph orchestrator span.

---

## 2. Final project structure

You will create exactly this layout. File-level specs are in section 4.

```
demos/brand-promo-multi-agent/
├── README.md                          # exists
├── IMPLEMENTATION_PLAN.md             # this file
├── demo.config.example.yaml           # exists
├── demo.config.yaml                   # implementer copies from example
├── .env.example                       # exists
├── .env                               # MANUAL: filled by Doneyli
├── .gitignore                         # exists
├── pyproject.toml                     # YOU create in M1
├── uv.lock                            # YOU generate in M1
│
├── src/
│   ├── __init__.py
│   ├── config.py                      # loads demo.config.yaml + .env
│   ├── observability.py               # Langfuse handler factory
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── orchestrator.py            # LangGraph state graph
│   │   ├── research_crew.py           # CrewAI research crew
│   │   ├── strategy_crew.py           # CrewAI strategy crew
│   │   └── compliance_agent.py        # LangGraph compliance node
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── sales.py                   # mock sales / inventory tools
│   │   ├── market.py                  # mock market research (Tavily optional)
│   │   ├── compliance.py              # brand + regulatory checkers
│   │   └── error_injection.py         # probabilistic failure modes
│   │
│   ├── prompts/
│   │   ├── __init__.py
│   │   ├── orchestrator.py
│   │   ├── research.py
│   │   ├── strategy.py
│   │   ├── compliance.py
│   │   └── judge.py                   # LLM-as-judge prompts
│   │
│   ├── data/
│   │   ├── brand_guidelines.md        # YOU author from config (generic CPG)
│   │   ├── regulatory_rules.md        # YOU author from config
│   │   ├── mock_sales.json            # YOU generate from config
│   │   ├── mock_inventory.json        # YOU generate from config
│   │   └── historical_promos.json     # YOU generate from config
│   │
│   └── synthetic/
│       ├── __init__.py
│       ├── trace_generator.py         # builds backfilled traces via Langfuse SDK
│       ├── query_templates.py         # synthetic user queries
│       └── distributions.py           # latency / cost / error distributions
│
├── scripts/
│   ├── setup_langfuse_project.py      # create or validate Langfuse project
│   ├── seed_prompts.py                # register prompts in Langfuse
│   ├── seed_evaluators.py             # configure LLM-as-judge
│   ├── seed_dataset.py                # build golden dataset
│   ├── seed_dashboards.py             # create persona dashboards
│   ├── seed_annotation_queue.py       # add traces to annotation queue
│   ├── seed_all.py                    # runs all seeds in order
│   ├── generate_history.py            # backfill 50k synthetic traces
│   └── run_live_demo.py               # interactive CLI for stage use
│
├── tests/
│   ├── test_config.py
│   ├── test_tools.py
│   ├── test_orchestrator_smoke.py     # one full agent run
│   └── test_trace_generator_smoke.py  # generates 100 traces and verifies
│
└── docs/
    ├── DEMO_RUNBOOK.md                # YOU author in M12: how to drive on stage
    └── ARCHITECTURE.md                # YOU author in M12: diagrams + flow
```

---

## 3. Dependencies (pin in pyproject.toml)

```toml
[project]
name = "brand-promo-multi-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "langgraph>=0.2.0",
    "langchain>=0.3.0",
    "langchain-anthropic>=0.2.0",
    "langchain-core>=0.3.0",
    "crewai>=0.80.0",
    "crewai-tools>=0.12.0",
    "langfuse>=3.0.0",
    "anthropic>=0.40.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "httpx>=0.27",
    "rich>=13.0",
    "typer>=0.12",
    "tenacity>=9.0",
]

[project.optional-dependencies]
dev = ["ruff>=0.6", "pytest>=8.0", "pytest-asyncio>=0.24", "mypy>=1.10"]
market = ["tavily-python>=0.5"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]
ignore = ["E501"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

If any pin fails to resolve, drop to the latest compatible minor and note it in the milestone log.

---

## 4. File-by-file specifications

Below are the contract specs for each file. Code structure and behavior are pinned; implementation details are yours.

### 4.1 `src/config.py`

Single source of truth for runtime config.

**Public surface:**
```python
from pydantic import BaseModel

class DemoConfig(BaseModel):
    customer: CustomerConfig
    catalog: CatalogConfig
    regions: list[str]
    retail_partners: list[str]
    compliance: ComplianceConfig
    agent_fleet: AgentFleetConfig
    llm: LLMConfig
    synthetic_history: SyntheticConfig
    live_demo_queries: list[DemoQuery]
    langfuse: LangfuseConfig

def load_config(path: str = "demo.config.yaml") -> DemoConfig: ...
def load_env() -> EnvConfig: ...   # reads .env via python-dotenv
```

All other modules import `DemoConfig` via `load_config()` and never read YAML or env vars directly.

Pydantic must validate that `synthetic_history.failure_mode_distribution` sums to a reasonable value and that all referenced doc paths exist.

### 4.2 `src/observability.py`

Factory for the Langfuse callback handler. All agents and crews share one.

```python
from langfuse.langchain import CallbackHandler

def make_langfuse_handler(
    *,
    agent_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> CallbackHandler: ...
```

Reads keys from env. Sets `metadata` keys: `agent_name`, `customer`, `demo_run_id`. `demo_run_id` is a UUID per process so the live demo run is filterable in the UI.

Must work for both LangGraph and CrewAI: CrewAI uses litellm under the hood and supports Langfuse via the OpenTelemetry path. The implementer must verify that CrewAI sub-crew spans nest under the LangGraph parent trace. If they do not, wire the trace context manually using `langfuse.context` and pass parent IDs through CrewAI callbacks.

### 4.3 `src/agents/orchestrator.py`

LangGraph state graph. Node sequence:

1. `classify_intent` — LLM call to classify into one of: `plan_promo`, `compare_brands`, `compliance_check_only`, `out_of_scope`.
2. `research_node` — invokes Research Crew if intent needs it.
3. `strategy_node` — invokes Strategy Crew.
4. `compliance_node` — invokes Compliance Agent.
5. `compose_brief` — final LLM call composing the campaign brief.

Conditional edges:
- `out_of_scope` short-circuits to a polite refusal.
- `compliance_check_only` skips strategy.
- If `compliance_node` fails (rejection), the brief includes the rejection reason.

State schema must include accumulated tool outputs, intermediate decisions, and rationales (for the "why the agent took this decision" demo moment). Rationales must be visible as span attributes.

Use `claude-sonnet-4-6` for orchestrator LLM calls. Enable prompt caching on the system prompt.

### 4.4 `src/agents/research_crew.py`

CrewAI crew with three agents:
- **DataAnalyst**: pulls sales and inventory from mock tools.
- **MarketResearcher**: pulls market trends (Tavily if `TAVILY_API_KEY` else canned).
- **HistorianAgent**: pulls historical promo outcomes from `historical_promos.json` via a vector-search-like tool (simple keyword match is fine).

Crew uses sequential process. Output: structured `ResearchPackage` dict with sections per agent. Use `claude-sonnet-4-6`.

### 4.5 `src/agents/strategy_crew.py`

CrewAI crew with two agents:
- **PromoStrategist**: generates 2-3 promo strategy options.
- **LiftEstimator**: estimates expected sales lift per option using a tiny heuristic + LLM justification.

Crew uses sequential process, but `PromoStrategist` should be configured with `max_iter=5` so the "max iterations" failure mode is reachable. Use `claude-opus-4-7`.

### 4.6 `src/agents/compliance_agent.py`

LangGraph mini-graph with two parallel checks:
- `check_brand_guidelines`: scans the brief against `brand_guidelines.md` rules.
- `check_regulatory`: scans against `regulatory_rules.md` jurisdiction-tagged rules.

Both checks emit a structured `ComplianceFinding` with severity. Joiner node aggregates. Use `claude-haiku-4-5`.

### 4.7 `src/tools/sales.py`, `inventory.py` (in one module), `market.py`, `compliance.py`

All tools must:
- Be wrapped as LangChain `@tool` decorated callables, so CrewAI agents can use them too.
- Call `src/tools/error_injection.py` first to roll the dice on failure modes.
- Return strict JSON-serializable dicts.
- Emit a Langfuse span attribute `tool.outcome` of `ok` | `timeout` | `error` | `degraded`.

`sales.py` reads from `mock_sales.json`, supports filters by brand, SKU, region, time range. `inventory.py` similar with `mock_inventory.json`. `market.py` either calls Tavily or returns a randomized canned response. `compliance.py` exposes `check_brand_guidelines(brief: str)` and `check_regulatory(brief: str, jurisdictions: list[str])`.

### 4.8 `src/tools/error_injection.py`

Reads `failure_mode_distribution` from config. Single function:

```python
def maybe_inject(tool_name: str) -> InjectedFault | None: ...
```

Returns `None` most of the time. On hit, returns an enum value that the tool function uses to decide how to misbehave (raise timeout, return malformed payload, return empty result, etc.). Deterministic seeded RNG per process (seed from `demo_run_id`) so demo runs are reproducible if needed.

### 4.9 `src/prompts/*.py`

Each file exposes a `PROMPTS: dict[str, str]` of named prompt templates. Prompts use `{{var}}` placeholders. The `seed_prompts.py` script reads these and pushes them into Langfuse Prompt Management so they are version-tracked and editable in the UI.

Compliance prompts should reference the customer name and jurisdiction names from config, but the prompt template stored in Langfuse uses placeholders so the same prompt works across customer overlays.

### 4.10 `src/data/brand_guidelines.md`

A short generic brand guideline doc with ~8 rules. Examples:
- Do not advertise alcoholic beverages to audiences under 21.
- Do not make health claims not approved by the relevant regulator.
- Do not use competitor brand names disparagingly.
- Disclose all material affiliations.
- Avoid stereotypes in audience targeting.
- Pricing claims must be substantiated by sales data within the trailing 90 days.
- Limited-time offers must include clear end dates.
- Marketing to children under 12 requires legal review.

Write so it triggers naturally on the q2 and q5 live demo queries.

### 4.11 `src/data/regulatory_rules.md`

Short generic ruleset organized by regulatory body. The body names come from `compliance.regulatory_bodies` in config; the file is YOU-authored using a generic template that the seed step renders against the active config. Example rule categories: food safety, advertising fairness, pricing fairness, alcohol marketing, marketing-to-minors.

### 4.12 `src/data/mock_sales.json`

YOU generate this in M3 by writing a one-shot script (can be inline in `scripts/seed_dataset.py` if compact). Schema:

```jsonc
{
  "rows": [
    {
      "brand": "Brand A", "sku": "BRA-CLS-LRG", "region": "Southeast",
      "retail_partner": "MegaMart", "quarter": "2025-Q4",
      "units": 12450, "revenue_usd": 38247.50, "promo_active": true
    }
  ]
}
```

Generate ~5000 rows covering all brands x SKUs x regions x retail partners x trailing 8 quarters. Use realistic distributions: top SKUs have 10-20x volume vs long tail; promo quarters have +25 to +60% units vs baseline.

### 4.13 `src/data/mock_inventory.json`

Schema: per SKU per regional DC, days-of-supply + units on hand. ~500 rows.

### 4.14 `src/data/historical_promos.json`

~150 records of past promos with brand, region, partner, mechanic, depth, duration, observed lift, notes. The `HistorianAgent` searches these.

### 4.15 `src/synthetic/trace_generator.py`

The largest single piece of work. Builds synthetic traces directly via Langfuse SDK without invoking LLMs.

**Approach:**
- Use `langfuse.Langfuse` low-level API: `trace()`, `span()`, `generation()`, `score()`.
- For each synthetic trace:
  1. Pick an agent from the fleet (weighted by `trace_share`).
  2. Pick a backdated timestamp using business-hours weighting.
  3. Build a realistic span tree matching that agent's typical shape.
     - PromoPlanner traces look like real PromoPlanner runs (orchestrator -> crews -> tools).
     - Other agents have simpler 1-3 span structures.
  4. Roll for failure mode per `failure_mode_distribution`.
  5. Set realistic latency per span using lognormal distribution centered on agent-specific means.
  6. Set token counts and cost using model pricing tables (define in `distributions.py`).
  7. For ~5% of PromoPlanner traces, attach an LLM-as-judge `score` with realistic distribution (most 0.8-1.0, some 0.3-0.7 for caught issues).

**Realism notes:**
- Use actual span names matching the live agent so dashboards aggregate across history + live.
- Vary `model` attribute across the configured models (orchestrator uses sonnet, strategy uses opus, etc.).
- Inputs and outputs should be templated strings with light randomization, not LLM-generated. Keep them short.
- Backdate via Langfuse's explicit `timestamp` parameter on traces and spans.

**Validation hook:** after generation, query the Langfuse API for trace count by day and assert distribution matches the request.

### 4.16 `scripts/setup_langfuse_project.py`

Steps:
1. Read `langfuse.project_name` from config.
2. If `LANGFUSE_ADMIN_TOKEN` is present in env: call the Langfuse admin API to create the project (or no-op if it exists). Print the project's public/secret keys and instruct Doneyli to paste them into `.env`.
3. If admin token is absent: print a `MANUAL` instructions block telling Doneyli the exact UI clicks.

After Doneyli supplies keys in `.env`, re-running the script verifies access by hitting `/api/public/projects` and printing the connected project name.

### 4.17 `scripts/seed_prompts.py`

For each prompt in `src/prompts/*`, push to Langfuse Prompt Management API with a stable name like `promo-planner/orchestrator/classify-intent` and version label `production`. Idempotent.

### 4.18 `scripts/seed_evaluators.py`

Creates three LLM-as-judge evaluators:
- `tool-call-correctness`: judges whether tools selected match query intent.
- `response-factuality`: judges whether SKUs and figures cited exist in `mock_sales.json` / catalog.
- `compliance-adherence`: judges whether the final brief respects guidelines.

Each evaluator config:
- Target type: `trace` or `observation`.
- Sampling: 10% online to keep cost modest during the demo.
- Model: `claude-opus-4-7`.

**If the Langfuse evaluator-create API is not exposed in v3:** generate a `MANUAL` checklist with field-by-field values for Doneyli to enter via the UI.

### 4.19 `scripts/seed_dataset.py`

Builds the golden dataset `promo-planner-golden-v1` with 25 items. Each item: input query, expected intent classification, expected tools called, ideal brief snippet for human review. Push via dataset API.

Also creates an experiment scaffold `promo-planner-strategy-v2-vs-v1` that can be triggered after the dataset is in place. Implementer does not need to run the experiment itself; just stage it.

### 4.20 `scripts/seed_dashboards.py`

Three persona dashboards. Use the Langfuse dashboards API.

**Executive dashboard `Executive - Agent Fleet`:**
- Total agent invocations last 7 days (big number)
- Error rate trend last 30 days (line)
- Cost trend last 30 days (line, stacked by agent name)
- Top 5 failing flows last 24h (table)
- Agent invocations by agent (bar)

**Ops dashboard `Ops - Agent Health`:**
- Latency p50/p95/p99 by agent (lines)
- Throughput per agent per hour (heatmap or stacked)
- Error rate by tool (bar)
- Top 10 slowest traces last 24h (table with links)

**AI Engineer dashboard `Engineer - PromoPlanner Deep Dive`:**
- Filter: agent_name = PromoPlanner
- Score distributions: tool-call correctness, factuality, compliance (histograms)
- Cost by model tier (stacked bar: sonnet vs opus vs haiku)
- Trace volume by intent classification
- Recent failed traces (table)

**If dashboards API does not allow programmatic creation in v3:** emit a `MANUAL` markdown file with screenshots of the target configurations (or, lacking screenshots, exact field values) for Doneyli to build by hand.

### 4.21 `scripts/seed_annotation_queue.py`

Seeds an annotation queue called `PromoPlanner Human Review` with 10 traces from the synthetic history that have ambiguous scores (e.g. factuality 0.6-0.8). Demonstrates the human eval workflow.

### 4.22 `scripts/seed_all.py`

Single entry point that runs in order:
1. `setup_langfuse_project`
2. `seed_prompts`
3. `seed_evaluators`
4. `seed_dataset`
5. `seed_dashboards`
6. `generate_history` (delegated to its own script)
7. `seed_annotation_queue`

Each step is idempotent and logs progress to console with `rich`.

### 4.23 `scripts/run_live_demo.py`

CLI driven by `typer`. Subcommands:
- `query "<text>"` — runs the orchestrator once on a free-form query. Prints the run's Langfuse URL.
- `play <id>` — runs one of the pre-canned `live_demo_queries` from config by id, with a 2-second printed countdown so Doneyli can switch screens.
- `play-all` — runs all 5 demo queries in sequence with pauses between.
- `clear-live-tag` — adds the tag `demo_live_<timestamp>` to filter live traces in the UI.

---

## 5. Implementation milestones

Each milestone has a goal, files touched, and a validation gate.

### M1 — Scaffold (foundation)
**Goal:** Project builds, deps install, `uv run` works.
**Files:** `pyproject.toml`, `src/__init__.py`, all empty package `__init__.py`s, `src/config.py` (full impl), copy `demo.config.example.yaml` to `demo.config.yaml`.
**Validation:**
- `uv sync` succeeds.
- `uv run python -c "from src.config import load_config; print(load_config().customer.display_name)"` prints `BrandCo`.
- `uv run ruff check src` passes.

### M2 — Observability primitives
**Goal:** Langfuse handler factory works.
**Files:** `src/observability.py`, `tests/test_config.py`.
**Validation:** Smoke test calling `make_langfuse_handler(agent_name="test")` returns an object without error. Pytest passes.

### M3 — Mock data + brand/reg docs
**Goal:** All static data files exist and validate.
**Files:** `src/data/*`.
**Validation:** Each JSON loads, has expected schema, and row counts match section 4 targets. Brand guidelines doc renders at least 8 rules. Regulatory rules doc references all configured regulatory bodies.

### M4 — Tools layer
**Goal:** All tools callable; error injection works.
**Files:** `src/tools/*`.
**Validation:** `tests/test_tools.py` covers happy path + injected failure path for each tool. Each tool emits the expected Langfuse span attribute when wired (mock the handler in tests).

### M5 — Compliance agent
**Goal:** LangGraph compliance mini-graph returns `ComplianceFinding`s for a known offending input and passes for a clean input.
**Files:** `src/agents/compliance_agent.py`, `src/prompts/compliance.py`.
**Validation:** Pytest with two fixtures: a brief that violates "marketing to children under 12" returns at least one HIGH severity finding; a clean brief returns no findings.

### M6 — Crews
**Goal:** Research and Strategy crews run end-to-end against mock data.
**Files:** `src/agents/research_crew.py`, `src/agents/strategy_crew.py`, `src/prompts/research.py`, `src/prompts/strategy.py`.
**Validation:** Each crew returns its expected structured output for a sample query. Cost per crew run < $0.10. Spans appear in Langfuse under a test trace.

### M7 — Orchestrator
**Goal:** Full agent runs end-to-end for all 5 live demo queries, all spans land in Langfuse correctly nested.
**Files:** `src/agents/orchestrator.py`, `src/prompts/orchestrator.py`.
**Validation:**
- `uv run scripts/run_live_demo.py play-all` completes all 5 queries.
- In Langfuse UI: each of the 5 traces shows the expected Agent Graph shape. CrewAI spans nest under orchestrator spans. **This is the critical correctness check; do not proceed past M7 if nesting is broken.**
- q2 produces a compliance finding visible in the trace.
- q3 produces at least one retry visible as a span sequence.
- q5 produces a hallucinated SKU visible in the final brief (the LLM-as-judge eval will catch this in M9).

### M8 — Langfuse project setup + prompts + dataset
**Goal:** Project exists, prompts live in Langfuse Prompt Management, golden dataset exists.
**Files:** `scripts/setup_langfuse_project.py`, `scripts/seed_prompts.py`, `scripts/seed_dataset.py`.
**Validation:** Open Langfuse UI; prompts visible in Prompts page; dataset visible with 25 items. `MANUAL`: Doneyli pastes keys into `.env` if admin token path is not available.

### M9 — Evaluators
**Goal:** Three LLM-as-judge evaluators configured. Trigger one on a recent live trace and confirm a score appears.
**Files:** `scripts/seed_evaluators.py`, `src/prompts/judge.py`.
**Validation:** Run a fresh live query; within 60 seconds the trace has at least one score from each evaluator.

### M10 — Synthetic history
**Goal:** 50k synthetic traces backdated across 30 days, distributed across the fleet, with the configured failure-mode distribution.
**Files:** `src/synthetic/*`, `scripts/generate_history.py`.
**Validation:**
- Trace count in Langfuse matches `synthetic_history.total_traces` within 1%.
- Per-agent share matches `agent_fleet.*.trace_share` within 2 percentage points.
- Per-day distribution shows business-hours weighting.
- ~5% of PromoPlanner traces carry a score.
- Run `tests/test_trace_generator_smoke.py` (generates 100 traces and asserts schema).

### M11 — Dashboards + annotation queue
**Goal:** Three persona dashboards visible in Langfuse UI populated with data from history + live. Annotation queue seeded.
**Files:** `scripts/seed_dashboards.py`, `scripts/seed_annotation_queue.py`.
**Validation:** Walk all three dashboards in the UI and confirm each widget renders data. Annotation queue has 10 items with mixed scores.

### M12 — Docs + demo runbook
**Goal:** Doneyli has a single page he can read on the morning of the demo to drive it.
**Files:** `docs/DEMO_RUNBOOK.md`, `docs/ARCHITECTURE.md`.
**Contents:**
- `DEMO_RUNBOOK.md`: 60-minute timed flow mapped to specific UI screens, with exact commands to run, what to say, what to click, recovery paths if a query fails on stage.
- `ARCHITECTURE.md`: the diagram from section 1, span-tree shape, Langfuse object model summary.

**Validation:** Doneyli reads both end to end and signs off.

---

## 6. Synthetic data design details

### Span-tree shape per hero agent trace

```
trace: promo_planner_run (root)
 +-- span: classify_intent (generation, model=sonnet, ~400ms, ~150 tok)
 +-- span: research_crew
 |    +-- span: data_analyst
 |    |    +-- span: tool.query_sales (~250ms, may inject timeout)
 |    |    +-- span: tool.query_inventory (~150ms)
 |    |    +-- span: generation.summarize (sonnet, ~1500 tok)
 |    +-- span: market_researcher
 |    |    +-- span: tool.market_trends (~600ms or 2s if Tavily live)
 |    |    +-- span: generation.summarize (sonnet, ~800 tok)
 |    +-- span: historian
 |         +-- span: tool.query_historical_promos (~100ms)
 |         +-- span: generation.summarize (sonnet, ~600 tok)
 +-- span: strategy_crew
 |    +-- span: promo_strategist
 |    |    +-- span: generation.generate_options (opus, ~3000 tok)
 |    +-- span: lift_estimator
 |         +-- span: generation.estimate (opus, ~1200 tok)
 +-- span: compliance_agent
 |    +-- span: check_brand_guidelines (haiku, ~600 tok)
 |    +-- span: check_regulatory (haiku, ~600 tok)
 +-- span: generation.compose_brief (sonnet, ~2500 tok)
 +-- score (added later by online eval): tool-call-correctness
 +-- score: response-factuality
 +-- score: compliance-adherence
```

Per-span typical latency distributions (lognormal, mean / sigma in ms):
- Tool calls: mean 250, sigma 0.5
- Sonnet generations: mean 1200, sigma 0.4
- Opus generations: mean 3500, sigma 0.4
- Haiku generations: mean 500, sigma 0.4

Cost per generation: use these reference prices (adjust if Anthropic pricing differs at implementation time):
- Sonnet: $3 input / $15 output per million tokens
- Opus: $15 input / $75 output per million tokens
- Haiku: $0.80 input / $4 output per million tokens

### Other-agent shapes (simpler)

- `CustomerCareBot`: trace -> retrieve -> generation. Mean latency 1.5s.
- `SupplyChainPlanner`: trace -> tool.query_inventory -> tool.optimize -> generation. Mean 4s.
- `ShelfImageAnalyzer`: trace -> vision_call -> generation. Mean 2.5s.
- `PepGPT` / `InternalKBSearch`: trace -> retrieve -> generation. Mean 1.2s.
- `FinanceCloseBot`: trace -> sql_query -> generation. Mean 3.5s.

### Failure mode realism

- `sales_api_timeout`: tool span has `status=error`, `error.type=Timeout`, latency 5000ms.
- `hallucinated_sku`: final brief output contains a fake SKU code (e.g. `BRA-XX9-FAKE`); LLM-as-judge factuality score drops to 0.3-0.5.
- `compliance_rejection`: compliance span has `findings=[{severity: HIGH, rule: ...}]`; brief states "rejected pending legal review".
- `crew_max_iterations`: strategy crew span has `iterations=5` and an attribute `terminated_reason=max_iter`.
- `tool_error`: random tool returns `status=error`.

---

## 7. Manual tasks (only the things only Doneyli can do)

These should be the ONLY things you ask for once the implementer agent runs. Everything else is automated.

| # | Task | When | Why it's manual |
|---|---|---|---|
| 1 | Fill `.env` with `ANTHROPIC_API_KEY` | Before M2 | Implementer can't have API keys |
| 2 | (Optional) Fill `.env` with `TAVILY_API_KEY` | Before M6 | If skipped, market tool returns canned data |
| 3 | Fill `.env` with `LANGFUSE_ADMIN_TOKEN` OR create project manually in UI | Before M8 | Admin auth |
| 4 | Paste Langfuse project public/secret keys into `.env` after project exists | Before M8 completes | Project keys only visible post-creation |
| 5 | Visual review of Agent Graph nesting after M7 | At M7 validation gate | Human eye for "does the trace look right" |
| 6 | Visual review of each persona dashboard after M11 | At M11 validation gate | Subjective polish judgment |
| 7 | Sign off on `DEMO_RUNBOOK.md` | At M12 validation gate | Owner's final read-through |
| 8 | If `seed_evaluators` or `seed_dashboards` API path is unavailable in your Langfuse build | During M9 or M11 | UI clicks per the MANUAL checklist the script emits |

That is the complete list. Everything else, the implementer drives.

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CrewAI spans don't nest under LangGraph orchestrator | Medium | High | M7 validation is the hard gate. If it fails, implementer wires Langfuse context manually via `langfuse.context.with_trace_id()`. |
| Anthropic prompt caching incompatible with LangGraph callbacks | Low | Medium | Test in M6. If broken, drop caching and note in DEMO_RUNBOOK. |
| Langfuse v3 dashboard API does not expose programmatic creation | Medium | Medium | `seed_dashboards.py` emits a MANUAL checklist as a fallback. |
| Anthropic API costs blow budget if synthetic history uses real LLM calls | Mitigated by design | High | Synthetic history uses direct SDK ingestion only, no LLM calls. Live demo + golden eval are the only real-LLM paths. |
| Localhost Langfuse v3 missing endpoints used by the SDK | Low | Medium | Document Langfuse version checked into setup script. If endpoint missing, fall back to UI manual step. |
| Library version drift breaks code between spec and implementation | Medium | Low | Pinned majors in `pyproject.toml`. Implementer notes any version override in milestone log. |
| Live demo query produces unexpected output during demo | Medium | Medium | `live_demo_queries` are rehearsed; `run_live_demo.py play-all` is a dry-run command Doneyli runs the night before. |
| Token limits exceeded on long compose_brief generations | Low | Low | `max_tokens_default: 2048` and explicit shorter caps on judge. |

---

## 9. Acceptance criteria (the implementer reports DONE when all of these hold)

- [ ] `uv run scripts/seed_all.py` runs to completion against `http://localhost:3001` with no errors.
- [ ] `uv run scripts/run_live_demo.py play-all` runs all 5 queries and prints the 5 Langfuse trace URLs.
- [ ] Langfuse project `brandco-demo` (or whatever the active config sets) contains: 5 prompts in Prompt Management, 3 evaluators, 1 golden dataset with 25 items, 1 staged experiment, 3 persona dashboards rendering data, 1 annotation queue with 10 items, ~50,000 traces.
- [ ] All 5 live demo trace Agent Graphs visually match the expected span tree.
- [ ] `docs/DEMO_RUNBOOK.md` is a single page Doneyli can read top to bottom in 5 minutes to drive the call.
- [ ] All tests in `tests/` pass.
- [ ] Total Anthropic cost across the full build (including history generation) is reported in the final implementer summary.

---

## 10. Notes to the implementer

- The customer discovery notes and demo plan (kept outside this repo) are the source of truth for what the customer wants to see and map those asks to demo segments. If you face an ambiguity not covered here, lean on those docs.
- **No sampling.** Langfuse engineering position: capture everything, tune TTL. Configure all evaluators with a sampling rate (10% online is for cost control), but ingestion is 100%.
- **Generic by default.** Any string a customer would see (brand names, regions, regulator names) must come from `demo.config.yaml`. The only exception: this `IMPLEMENTATION_PLAN.md` itself.
- **Cost discipline.** History generation uses zero LLM calls. The expensive things are M6, M7, M9 (each is cents to dollars) and the live demo run itself.
- **Logging.** Use `rich` for human-readable console output in scripts. Save a single milestone log to `docs/BUILD_LOG.md` recording: completed milestone, time, any deviations, any version overrides.
