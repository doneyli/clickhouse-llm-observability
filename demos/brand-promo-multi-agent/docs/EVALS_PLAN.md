# Evals Plan: Brand Promo Multi-Agent Demo

## Context

The brand-promo-multi-agent demo already has the scaffolding for evals (a 25-item golden dataset, 3 LLM-as-judge evaluator configs, 3 judge prompts), but **no experiment runner** to actually score the agent against the dataset, **no score-config registration**, and **no dataset-replay loop**. The synthetic trace generator only attaches one score dimension (`response-factuality`) to ~5% of historical traces, leaving the eval story incomplete for the demo.

The reference pattern is a complete Langfuse eval pipeline: `dataset.run_experiment()` with a task closure, mixed deterministic + LLM-as-judge item evaluators, run-level aggregates and pass/fail gates, REST API for run-level scores, and idempotent setup scripts.

The intended outcome: a CPG-adapted version of that finance pattern that lets you demo three things live - "here's a golden dataset", "here's a one-command experiment run that scores the agent on it", and "here are the per-dimension scores and a pass/fail gate". Plus a synthetic golden dataset richer than the current 25 items so the demo feels real.

This plan **researches and documents** the approach. No code is written yet.

---

## What we're replicating from the finance project

| Pattern | Finance file | Demo target |
|---|---|---|
| Score config registration | `setup_score_configs.py` | `scripts/setup_score_configs.py` (new) |
| Prompt management | `setup_prompts.py` | extend `scripts/seed_prompts.py` (judge prompts already exist) |
| Golden dataset push | `setup_datasets.py` | extend `scripts/seed_dataset.py` (expand to ~75 items) |
| Item-level evaluators (deterministic + judge) | `evaluators.py` | `src/evals/evaluators.py` (new) |
| Run-level aggregates + gate | `evaluators.py` (factories) | same file |
| Experiment runner | `run_certification.py` | `scripts/run_experiment.py` (new) |
| Run-level score POST | inline in `run_certification.py` | reuse same REST pattern |
| Annotation queue for failures | `setup_annotation_queues.py` | extend `scripts/seed_annotation_queue.py` |

---

## Golden dataset design (synthetic, expanded)

**Current state:** 25 hand-authored items in `scripts/seed_dataset.py` covering 4 intents (`plan_promo`, `compare_brands`, `compliance_check_only`, `out_of_scope`).

**Proposed expansion:** ~75-100 items, generated programmatically by combining slots from `demo.config.yaml` (brands, SKUs, regions, retail partners, quarters). Item structure stays identical to keep the existing 25 valid.

Slot taxonomy:
- **Intent buckets** (target distribution):
  - `plan_promo` - 50%
  - `compare_brands` - 20%
  - `compliance_check_only` - 15%
  - `out_of_scope` - 5%
  - `compliance_edge_case` (new bucket: requires judgment between APPROVED and CONDITIONAL) - 10%
- **Compliance status mix**: APPROVED 60%, CONDITIONAL 25%, REJECTED 15%
- **Failure-mode tests**: dedicated items for each of the 5 demo failure modes (hallucinated SKU, compliance rejection, tool failure recovery, max iterations, out-of-scope)

Each item carries the same fields the existing dataset uses, plus two new optional fields:
- `metadata.intent_bucket` - for stratified analysis in run reports
- `metadata.judge_focus` - hint to the LLM-as-judge about what to weight (e.g., `factuality`, `compliance`, `tool_use`)

Generation lives in a new helper `src/evals/dataset_builder.py` that the seed script imports. It reads `demo.config.yaml` so the dataset re-themes per customer overlay without code changes - same generic-by-default principle as the rest of the demo.

---

## Evaluator suite

Three categories, mirroring the finance project. Lives in `src/evals/evaluators.py`.

### Deterministic item-level evaluators

Fast, free, deterministic. Run on every item in the experiment.

| Evaluator | Score range | What it measures |
|---|---|---|
| `intent_classification_accuracy` | 0/1 | Does `state.intent` match `expected_output.intent`? |
| `tool_call_match` | 0.0-1.0 | Jaccard overlap between `state.tools_called` and `expected_output.expected_tools` |
| `compliance_status_match` | 0/1 | Does `state.compliance_status` match expected status? |
| `brief_contains` | 0.0-1.0 | Fraction of `expected_output.brief_should_contain` substrings present in `state.final_brief` |
| `sku_validity` | 0.0-1.0 | Fraction of SKU codes in the brief that exist in `mock_sales.json` (catches hallucinated SKUs deterministically) |
| `brief_length_sanity` | 0/1 | Brief is between 200 and 5000 chars (catches empty or runaway outputs) |

`sku_validity` is the cheap deterministic floor under the LLM-as-judge `response-factuality` - it catches obvious hallucinations without an API call.

### LLM-as-judge item-level evaluators

The 3 already-designed judges in `src/prompts/judge.py` get wired up here:

| Judge | Already prompted? | Notes |
|---|---|---|
| `tool-call-correctness` | yes | judges semantic appropriateness of tools (catches cases where Jaccard misses nuance) |
| `response-factuality` | yes | the existing 0.0-1.0 judge, focused on hallucinated SKUs/brands/regions |
| `compliance-adherence` | yes | judges whether brief respects compliance findings |

One additional judge to add: `brief_quality` - generic 0.0-1.0 score for clarity/structure/actionability, useful as a "everything else" catch-all and easy to demo.

Judge model: `claude-opus-4-7` (matches existing config in `seed_evaluators.py`). Each judge call gets a fallback to `None` if the JSON parse fails so a flaky judge doesn't crash the run.

### Run-level evaluators (factories)

Aggregate across all items, exactly like the finance project's `average_score_evaluator` and `certification_gate`:

- `avg_intent_classification_accuracy`
- `avg_tool_call_match`
- `avg_compliance_status_match`
- `avg_brief_contains`
- `avg_sku_validity`
- `avg_tool_call_correctness` (judge)
- `avg_response_factuality` (judge)
- `avg_compliance_adherence` (judge)
- `certification_gate` - PASS if `avg_intent_classification_accuracy >= 0.85` AND `avg_compliance_status_match >= 0.90` AND `avg_response_factuality >= 0.80`

The gate thresholds are chosen so the baseline orchestrator passes most categories but the hallucination/compliance buckets surface visibly low scores. Tunable via CLI flags.

---

## Score config registration

New script `scripts/setup_score_configs.py`, modeled directly on the finance file. Idempotent. Registers every numeric score above with `dataType=NUMERIC, minValue=0, maxValue=1` and a human-readable description. Without this step, scores still write but show up in the UI with no schema metadata.

Falls back to MANUAL instructions if the Langfuse v3 score-config API path isn't available (same pattern as `seed_evaluators.py`).

---

## Experiment runner design

New script `scripts/run_experiment.py`. Modeled on `run_certification.py`. Single-file CLI driven by `typer`.

**Flow:**
1. `load_env()` at top of file (before any langfuse import - the OTel-state issue we already debugged).
2. Fetch dataset via `langfuse.get_dataset("promo-planner-golden-v1")`.
3. Build task closure: `task(item) -> dict` that calls `src.agents.orchestrator.run_orchestrator(item.input["query"])` and returns the `OrchestratorState`.
4. Select evaluators based on `--evaluators` flag: `all | deterministic | judge | accuracy`.
5. Call `dataset.run_experiment(name=..., task=task, evaluators=[...], run_evaluators=[...])`.
6. POST run-level scores to `/api/public/scores` (per the finance pattern - SDK doesn't persist run-level evals automatically in v4).
7. Optional `--queue-failures` flag to push failed items into the annotation queue.
8. Print a `rich`-formatted summary table at the end.
9. Optional `--ci` flag to `sys.exit(1)` on gate failure.

**CLI examples** (for the runbook later):
```
uv run python scripts/run_experiment.py --run-name baseline
uv run python scripts/run_experiment.py --run-name strategy-v2 --label strategy-v2
uv run python scripts/run_experiment.py --evaluators deterministic --ci
uv run python scripts/run_experiment.py --max-concurrency 4 --queue-failures
```

**Cost guardrail:** at full concurrency on 75 items, each running the orchestrator + 4 judges, this is ~10-15 dollars per run on Anthropic. Add `--sample N` to run on a subset for cheap rehearsal.

**Run metadata attached to each trace:** `{run_name, label, evaluator_mode, threshold, dataset_version}`. This is what the persona dashboards in M11 filter on, so it has to be set consistently.

---

## Demo story: what the SA shows on stage

This is what the eval plan needs to actually unlock for the customer demo. Three moments:

1. **"Here's the golden dataset"** - open the dataset in Langfuse UI, show 75 items grouped by intent bucket. Walk through 2-3 items to show the structure (input, expected_output).

2. **"Here's a one-shot experiment run"** - in terminal: `uv run python scripts/run_experiment.py --run-name demo-baseline --sample 10`. Show the rich summary table at the end with per-dimension scores and the gate PASS/FAIL.

3. **"Here are the scores in Langfuse"** - flip to the dataset's Runs tab, show the run's per-item scores, click a low-scoring item, see judge rationale in the score comment. Then show the Engineer dashboard filtered by `metadata.run_name` to see distributions side-by-side.

Bonus moment if time: run a second experiment with a tweaked system prompt (`--label strategy-v2 --system-prompt-file prompts/strategy_v2.md`) and show the two runs compared in the Runs tab.

---

## Files to be created or modified

**New files:**
- `src/evals/__init__.py`
- `src/evals/evaluators.py` - all deterministic + judge evaluators + run-level factories
- `src/evals/dataset_builder.py` - synthetic golden dataset generator (slot-based)
- `scripts/setup_score_configs.py` - register score schemas
- `scripts/run_experiment.py` - the experiment runner CLI
- `prompts/strategy_v2.md` - optional second system prompt for the comparison demo moment

**Modified files:**
- `scripts/seed_dataset.py` - import `dataset_builder` to generate the expanded 75-item set; keep the original 25 as the "hand-curated core"
- `scripts/seed_all.py` - add `setup_score_configs` and (optionally) `run_experiment --sample 5` as smoke-test steps
- `scripts/seed_evaluators.py` - point judge prompts at the new evaluator infrastructure (so the live online evals and the offline experiment use identical prompt versions)
- `docs/DEMO_RUNBOOK.md` - add the three-moment eval segment to the timed flow
- `src/synthetic/trace_generator.py` - extend score attachment to all 4 judge dimensions (currently only `response-factuality`)
- `tests/test_evaluators.py` (new) - unit-test each deterministic evaluator with golden fixtures

**Existing files reused as-is (with file paths):**
- `src/prompts/judge.py` - 3 existing judge prompts are reused verbatim
- `src/agents/orchestrator.py` - `run_orchestrator(query)` is the task closure target
- `demo.config.yaml` - slot inventory for the synthetic dataset
- `src/data/mock_sales.json` - source of truth for `sku_validity` deterministic check

---

## Locked design decisions

- **Dataset size: 75 items.** 25 hand-authored core + 50 slot-generated. Stratified across intent buckets per the distribution in the dataset design section.
- **Comparison moment: YES.** Author `prompts/strategy_v2.md` as a tweaked strategy-crew system prompt. Experiment runner supports `--system-prompt-file` and `--label` flags so two runs appear side-by-side in Langfuse Datasets > Runs view.
- **Tag scope: shared.** Experiment-run traces carry `tags=["experiment", run_name]` AND `metadata.agent_name="PromoPlanner"`. This lets the Engineer dashboard show experiment traces alongside synthetic history and live demo traces - reinforces the "one pane of glass" story for the customer.

---

## Verification approach (post-implementation, for reference)

1. `uv run pytest tests/test_evaluators.py` - all deterministic evaluators pass on golden fixtures.
2. `uv run python scripts/setup_score_configs.py` - score configs visible in Langfuse UI under Settings.
3. `uv run python scripts/seed_dataset.py` - dataset `promo-planner-golden-v1` shows ~75 items grouped by intent_bucket metadata.
4. `uv run python scripts/run_experiment.py --run-name verify --sample 5` - completes without error, prints summary, gate result visible.
5. Open Langfuse UI - dataset run visible with per-item scores, judge comments readable, run-level scores attached to first trace.
6. `uv run python scripts/run_experiment.py --run-name compare-v2 --label strategy-v2 --system-prompt-file prompts/strategy_v2.md --sample 5` - second run appears side-by-side with first in Runs tab.
7. Engineer dashboard filtered by `metadata.run_name = verify` - widgets render run-specific scores.
