# Brand Promo Multi-Agent Demo

A generic, re-themeable multi-agent system used to demo **LLM observability** for CPG, retail, and FMCG customers. Designed to be re-themed for any similar company via `demo.config.yaml`.

Runs against **Langfuse**. All agents, prompts, dataset, and evaluators are instrumented via Langfuse callback handlers.

## What it demonstrates

A multi-agent **promo planning assistant** with realistic failure modes, populated history, evaluations, and persona dashboards in Langfuse.

- **LangGraph orchestrator** routing user intents
- **CrewAI sub-crews** for research and strategy
- **LangGraph compliance node** for brand and regulatory checks
- Mock enterprise tools (sales, inventory, market trends, brand guidelines)
- All instrumented via Langfuse callback handlers
- Synthetic trace history (~50k traces over 30 days)
- LLM-as-judge online evals (10% sampling on live traces)
- **Offline eval suite**: 75-item golden dataset, deterministic + LLM-as-judge evaluators, multi-dimensional certification gate, side-by-side run comparison
- Annotation queue for human review of failed items
- Three persona dashboards: Executive, Ops, AI Engineer

## Who this is for

Any prospect evaluating Langfuse for multi-agent observability, especially when:

- The incumbent is an existing tracing/observability tool or homegrown
- They have CrewAI / LangGraph / LangChain in their stack
- They want persona-driven dashboards (exec / ops / engineer)
- They want to see Langfuse handle real fleet scale (Langfuse is ClickHouse-backed)

## Quick start (after implementer build is done)

```bash
cd demos/brand-promo-multi-agent
cp .env.example .env                       # fill in keys
cp demo.config.example.yaml demo.config.yaml   # or use a customer overlay
uv sync                                    # install deps
uv run scripts/setup_langfuse_project.py   # creates/validates project (self-hosted only — skip for Cloud)
uv run scripts/seed_all.py                 # seeds prompts, evals, score configs, dataset
uv run scripts/generate_history.py         # backfills synthetic traces
uv run scripts/run_live_demo.py            # interactive live agent
```

Then open Langfuse UI to drive the demo.

## Eval suite

The offline eval pipeline lets you measure agent quality against a labeled dataset, score every dimension (intent, tools, compliance, factuality, brief quality), and apply a pass/fail gate before shipping prompt changes.

**What's included:**

- **Golden dataset** (`promo-planner-golden-v1`, 75 items)
  - 25 hand-authored items covering known failure modes (`scripts/seed_dataset.py`)
  - 50 slot-generated items from `demo.config.yaml` brands × regions × retail partners (`src/evals/dataset_builder.py`)
  - Stratified across 5 intent buckets: plan_promo (50%), compare_brands (20%), compliance_check (15%), compliance_edge_case (10%), out_of_scope (5%)
- **Evaluators** (`src/evals/evaluators.py`)
  - 6 deterministic: `intent_classification_accuracy`, `tool_call_match`, `compliance_status_match`, `brief_contains`, `sku_validity`, `brief_length_sanity`
  - 4 LLM-as-judge (claude-opus-4-7): `tool_call_correctness`, `response_factuality`, `compliance_adherence`, `brief_quality`
  - Run-level: per-dimension averages + multi-threshold `certification_gate` (intent ≥ 85% AND compliance ≥ 90% AND factuality ≥ 80%)
- **Experiment runner** (`scripts/run_experiment.py`): CLI with `--sample`, `--label`, `--ci`, `--queue-failures`, `--system-prompt-file` flags
- **Score configs** (`scripts/setup_score_configs.py`): registers 20 score schemas in Langfuse so UI knows ranges and descriptions
- **Tests** (`tests/test_evaluators.py`): 28 unit tests covering deterministic evaluators and the gate

**Running experiments:**

```bash
# Cheap rehearsal — 10 items, deterministic only, free, ~30s
uv run python scripts/run_experiment.py --run-name rehearsal --sample 10 --evaluators deterministic

# Full deterministic run — all 75 items, no judge cost, ~3-5 min
uv run python scripts/run_experiment.py --run-name full-baseline --evaluators deterministic

# Full run with all judges — all 75 items, ~$10-15 on Anthropic, ~15-20 min
uv run python scripts/run_experiment.py --run-name full-all --evaluators all

# A/B comparison — show two runs side-by-side in Datasets > Runs tab
uv run python scripts/run_experiment.py --run-name baseline --label baseline --sample 10
uv run python scripts/run_experiment.py --run-name v2 --label strategy-v2 --system-prompt-file prompts/strategy_v2.md --sample 10

# CI mode — exit 1 if the certification gate fails (for deployment gates)
uv run python scripts/run_experiment.py --run-name ci-check --evaluators deterministic --ci

# Route low-scoring items to the annotation queue for human review
uv run python scripts/run_experiment.py --run-name flagged --sample 20 --queue-failures
```

**Demo flow (three moments):**

1. **Show the dataset** — Langfuse UI → Datasets → `promo-planner-golden-v1`. 75 items with intent buckets visible in metadata.
2. **Run the experiment live** — `uv run python scripts/run_experiment.py --run-name demo-baseline --sample 10`. Rich table prints per-dimension scores and PASS/FAIL gate at the end.
3. **Show scores in Langfuse** — Datasets > `promo-planner-golden-v1` > Runs tab. Per-item judge rationale visible in score comments. Filter Engineer dashboard by `metadata.run_name` to see distributions.

See `docs/EVALS_PLAN.md` for the full design rationale and `docs/DEMO_RUNBOOK.md` (Segment 6.5) for the on-stage flow.

## Status

**Built and demo-ready.** Orchestrator, synthetic trace generator (50k traces), seed scripts, persona dashboards, online evaluators, and offline eval suite (75-item dataset + experiment runner + multi-dimensional gate) are all functional. See `docs/ARCHITECTURE.md` for the design and `docs/EVALS_PLAN.md` for the eval pipeline.

Known notes:
- `scripts/setup_langfuse_project.py` is self-hosted only — for Langfuse Cloud, create the project via UI and skip this step
- On Python 3.14 the demo pins `langfuse>=4.0,<5.0` (v3 SDK depends on Pydantic v1 which is broken on 3.14)
- `scripts/run_experiment.py` includes a workaround for the v4 SDK / v3 server `datasetVersion` mismatch — remove it once the local server is upgraded to v4

## Customer overlays

- `demo.config.example.yaml` — generic "BrandCo" placeholder

To use this demo for a new customer, copy `demo.config.example.yaml` to `demo.config.yaml`, swap brand / region / regulatory details, and re-run the seed scripts.
