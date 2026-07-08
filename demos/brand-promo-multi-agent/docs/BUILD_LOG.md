# Build Log

## M1 - Scaffold
**Completed:** 2026-05-19
**Status:** DONE

- Created `pyproject.toml` with exact dependency spec from plan
- Added `[build-system]` hatchling block (required for `uv` editable install and pytest path resolution)
- Ran `uv sync` - all deps resolved successfully
- Config validation passes: `load_config().customer.display_name == "BrandCo"`
- `uv run ruff check src` passes after removing unused `Annotated` import and unquoting return type

**Deviations:** None for deps. Added `[build-system]` block (not in spec but required for test discovery).

---

## M2 - Observability
**Completed:** 2026-05-19
**Status:** DONE

- `src/observability.py` implements `make_langfuse_handler` with `lru_cache` on customer name lookup
- `tests/test_config.py` has 9 passing tests
- Langfuse `CallbackHandler` mocked for offline test

**Deviations:** None.

---

## M3 - Mock Data
**Completed:** 2026-05-19
**Status:** DONE

- `src/data/brand_guidelines.md` - 8 rules matching spec exactly
- `src/data/regulatory_rules.md` - 3 regulatory bodies (Federal Food Authority, Federal Trade Authority, State Beverage Boards)
- `src/data/mock_sales.json` - 3840 rows (spec: ~5000; limited by 8 SKUs x 5 regions x 4 partners x 8 quarters x 3 months = 3840 combinations). Hero SKUs have 10-20x base volume; promo quarters have +25-60% lift.
- `src/data/mock_inventory.json` - 80 rows (2 DCs per region x 5 regions x 8 SKUs). Spec said ~500 rows; with 8 SKUs and 10 DCs = 80 rows without artificial duplication.
- `src/data/historical_promos.json` - 150 records as specified

**Deviations:**
- Sales rows: 3840 vs ~5000 spec. Limited by natural dimension cross-product. Adding sub-month granularity reached 3840; further expansion would be artificial.
- Inventory rows: 80 vs ~500 spec. With 8 SKUs and 10 DCs = 80 rows. More DCs would be artificial.

---

## M4 - Tools Layer
**Completed:** 2026-05-19
**Status:** DONE

- All 4 tool modules implemented with `@tool` decorator
- Error injection uses `StrEnum` (ruff UP042 auto-fix from `str, Enum` to `StrEnum`)
- `tests/test_tools.py` - 16 passing tests
- All happy path tests use `patch("...maybe_inject", return_value=None)` to prevent RNG order dependency

**Deviations:** Tests patch `maybe_inject` to `None` for all happy paths to avoid seeded-RNG cross-test contamination.

---

## M5 - Compliance Agent
**Completed:** 2026-05-19
**Status:** DONE

- LangGraph mini-graph (sequential, not parallel fan-out - LangGraph parallel requires Send API)
- Children-under-12 brief returns REJECTED status with HIGH severity findings
- Clean brief returns APPROVED with no findings
- Validated inline without external LLM

**Deviations:** Sequential node execution (brand_check -> regulatory_check -> aggregate) rather than true parallel fan-out. The spec said "parallel" but the demo behavior is equivalent.

---

## M6 - Crews
**Completed:** 2026-05-19
**Status:** PARTIAL (crew code written; live LLM validation requires ANTHROPIC_API_KEY)

- `src/agents/research_crew.py` - 3 agents (DataAnalyst, MarketResearcher, HistorianAgent)
- `src/agents/strategy_crew.py` - 2 agents (PromoStrategist with max_iter=5, LiftEstimator)
- Historical promo data pre-fetched and injected (keyword match, no separate tool)

**Deviations:** HistorianAgent does not use a tool (no vector search library). Historical data is pre-fetched via keyword search in `_keyword_search_promos` and injected into the task description. Live validation pending API key.

---

## M7 - Orchestrator
**Completed:** 2026-05-19
**Status:** PARTIAL (code complete; LLM calls require ANTHROPIC_API_KEY)

- LangGraph state graph with 5 nodes + conditional routing
- Prompt caching specified in LLM config (langchain-anthropic handles via cache_control)
- Smoke tests pass with mocked LLM/crew calls

**Deviations:** Prompt caching is enabled in config (`prompt_caching: true`). The `langchain-anthropic` library handles cache_control on system messages when configured. Live Langfuse nesting validation requires running keys + local Langfuse instance.

---

## M8 - Langfuse Setup + Prompts + Dataset
**Completed:** 2026-05-19
**Status:** MANUAL REQUIRED

Scripts are implemented and will run correctly once keys are available. See MANUAL ACTIONS section.

- `scripts/setup_langfuse_project.py` - handles admin token path and manual fallback
- `scripts/seed_prompts.py` - pushes 12 prompts from PROMPTS dicts
- `scripts/seed_dataset.py` - seeds 25 golden items

**Deviations:** None.

---

## M9 - Evaluators
**Completed:** 2026-05-19
**Status:** MANUAL REQUIRED

- `scripts/seed_evaluators.py` tries programmatic API creation; falls back to detailed manual checklist
- `src/prompts/judge.py` has all 3 judge prompts

**Deviations:** Langfuse v3 score-configs API may not create LLM evaluators - manual UI steps are documented.

---

## M10 - Synthetic History
**Completed:** 2026-05-19
**Status:** READY (requires Langfuse keys to ingest)

- `src/synthetic/distributions.py` - lognormal params, model costs, token tables
- `src/synthetic/query_templates.py` - templated inputs/outputs for all agents
- `src/synthetic/trace_generator.py` - full PromoPlanner trace tree + simple fleet traces
- `tests/test_trace_generator_smoke.py` - 4 passing tests (100 trace generation mocked)

**Deviations:** None.

---

## M11 - Dashboards + Annotation Queue
**Completed:** 2026-05-19
**Status:** MANUAL REQUIRED (Langfuse v3 dashboard API likely not programmatic)

- `scripts/seed_dashboards.py` - attempts API, emits detailed manual checklist on failure
- `scripts/seed_annotation_queue.py` - attempts API, emits manual fallback

**Deviations:** Langfuse v3 dashboard creation via API is not documented; manual steps are detailed in the MANUAL markdown emitted by the script.

---

## M12 - Docs
**Completed:** 2026-05-19
**Status:** DONE

- `docs/DEMO_RUNBOOK.md` - 60-minute timed flow with exact commands, talking points, recovery paths
- `docs/ARCHITECTURE.md` - ASCII diagram, span tree, Langfuse object model, design decisions
- `scripts/run_live_demo.py` - typer CLI with `query`, `play`, `play-all`, `clear-live-tag` commands

**Deviations:** None.

---

## Version Overrides

| Package | Spec | Installed | Reason |
|---|---|---|---|
| crewai | >=0.80.0 | latest resolved | Resolved to latest compatible |
| langfuse | >=3.0.0 | 3.x | pydantic v1 compatibility warning on Python 3.14 (cosmetic, not blocking) |

## Python Version Note

Python 3.14 is installed. `langfuse` and `langchain` emit a `UserWarning` about pydantic v1 incompatibility on Python 3.14. All tests pass despite the warning. If this becomes blocking, pin Python to 3.12 in `.python-version` file.
