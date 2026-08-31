# Code Evaluators

This demo provisions five [Langfuse code evaluators](https://langfuse.com/docs/evaluation/evaluation-methods/code-evaluators) —
deterministic TypeScript checks that run **inside Langfuse** and attach scores to
observations. They complement the LLM-as-a-Judge evaluators: code handles the
objective half of evaluation (formats, policies, exact patterns), the judge
handles the semantic half (relevance, hallucination, helpfulness).

## Why code evaluators (the pitch)

| | Code evaluator | LLM-as-a-Judge |
|---|---|---|
| **Verdict** | Deterministic — same input, same score, every time | Probabilistic — can drift or hallucinate |
| **Cost** | Free per evaluation | One LLM call per evaluation |
| **Latency** | Milliseconds (2s hard limit) | Seconds |
| **Sampling** | 100% of traffic is affordable | Usually sampled (10–25% in prod) |
| **Good at** | Regex, JSON validity, schema checks, business rules, guardrails | Rubrics, semantics, tone, groundedness |

**When to use which:** if a human would decide by *looking for a pattern*
(does the SQL contain `DROP`? is there an API key in the output?), write code.
If a human would decide by *reading and thinking* (is this answer faithful to
the context?), use a judge. The demo deliberately runs both side by side on
the same traces so you can show the split.

## What's provisioned

`./setup.sh` (or `./scripts/seed-code-evaluators.sh`) creates these from the
sources in [`evaluators/`](../evaluators/):

### Live observations (score new traffic at ingest, 100% sampling)

| Evaluator | Runs on | Scores | Demo story |
|---|---|---|---|
| `sql-safety-guard` | Generations on `text-to-sql` traces | `sql-present`, `sql-read-only` (bool), `sql-risk` (safe / missing-limit / destructive / no-sql) | A guardrail metric: catch the model emitting `DROP TABLE` — something you'd never trust to a sampled LLM judge |
| `credential-leak-guard` | Every generation, all apps | `credential-leak` (bool), `leak-type` (categorical) | Secrets have exact shapes (`sk-…`, `AKIA…`, `postgres://user:pass@`); regex at 100% coverage costs nothing |
| `response-structure-check` | Generations on `text-to-sql` / `vector-rag` traces | `output-present`, `structure-clean` (bool), `response-length` (numeric) | Mechanical defects — empty output, unclosed code fences, leaked `{context}` placeholders, truncation |

### Experiments (score dataset runs, e.g. `scripts/run-experiments.py`)

| Evaluator | Dataset | Scores | Demo story |
|---|---|---|---|
| `security-behavior-check` | `coding-assistant-security` | `security-compliant`, `credential-echoed` (bool), `expected-behavior` (categorical) | Each dataset item declares its required behavior (`refuse_with_explanation`, `redact_and_warn`, …); code verifies the contract deterministically, so prompt/model comparisons are reproducible |
| `quality-structure-check` | `coding-assistant-quality` | `code-block-present`, `language-match` (bool), `keyword-coverage` (0–1 numeric) | The objective half of "is this a good coding answer" — judge spend goes only where semantic judgment is needed |

## Demo walkthrough (5 minutes)

1. **Show the evaluators**: Langfuse → `Evaluators` (http://localhost:3001/project/demo-project/evals).
   Open one — the TypeScript source, target filter, and variable mapping are all visible.
   Point out the function contract: `evaluate(ctx) → { scores: [...] }` with
   `ctx.observation.{input,output,metadata}` and, on experiments,
   `ctx.experiment.{item_expected_output,item_metadata}`.

2. **Live scoring**: generate traffic —
   ```bash
   docker compose run --rm text-to-sql python main.py
   ```
   Within ~30 seconds the new traces carry `sql-risk`, `credential-leak`, and
   `structure-clean` scores (trace view → Scores). Emphasize: nobody called an
   LLM to produce these, and they run on **every** trace, not a sample.

3. **The guardrail moment**: ask the interactive app for something destructive —
   ```bash
   docker compose run --rm text-to-sql python main.py --interactive
   # "Write a query to delete all old taxi trips"
   ```
   The trace gets `sql-risk = destructive` with a comment quoting the offending
   statement. Filter traces by that score to build the "SQL policy violations"
   saved view.

4. **Experiments**: run the datasets through a model —
   ```bash
   pip install 'langfuse>=4.7,<5.0' anthropic   # one-time
   python scripts/run-experiments.py --dataset security
   ```
   Open Datasets → `coding-assistant-security` → the new run. Every item is
   scored `security-compliant` against the behavior the *dataset item itself*
   demands. Re-run with a different `--model` and compare runs side by side —
   deterministic scores make the comparison apples-to-apples.

5. **Both worlds together**: open a trace that has judge scores (Hallucination)
   *and* code scores (`sql-risk`). The judge tells you the answer is grounded;
   the code tells you it's safe to execute. Different questions, right tool each.

## How it works in this stack

- Code evaluators are a **Fast Preview** feature with no public API yet, so
  `scripts/seed-code-evaluators.sh` seeds the same rows the UI flow creates
  (`eval_templates` type=CODE + `job_configurations`) directly into the
  Langfuse Postgres database. Re-running updates the source from `evaluators/*.ts`.
- The self-hosted execution backend is enabled in `docker-compose.yaml`:
  ```yaml
  LANGFUSE_CODE_EVAL_DISPATCHER: insecure-local
  QUEUE_CONSUMER_CODE_EVAL_EXECUTION_QUEUE_IS_ENABLED: "true"
  ```
  `insecure-local` runs evaluator code inside the Langfuse worker process —
  fine for this trusted demo, **not** a sandbox for untrusted code, and it
  supports TypeScript/JavaScript only (Python evaluators require the
  `aws-lambda` dispatcher).
- Matching happens at ingest: each observation is checked against the
  evaluator's filter (observation type, trace name, dataset, …); matches are
  queued and scored asynchronously (typically <30s in this stack).
- **Cloud mode** (`DEPLOY_MODE=cloud`): the seeding script prints instructions
  instead — create each evaluator in the Langfuse Cloud UI
  (`Evaluators → New evaluator → Code`) and paste the sources from
  `evaluators/`. Cloud also supports Python evaluators.

## Runtime constraints (worth mentioning in the demo)

- Standard library only, no network egress, 2-second timeout, source ≤256 KB.
- Must return at least one score; types: `NUMERIC`, `BOOLEAN`, `CATEGORICAL`, `TEXT`.
- Everything the evaluator needs must be in the observation or experiment
  context — which is why the dataset items carry `expected_behavior` and
  `language` metadata.

## Debugging

- Evaluator execution status lives in the evaluator's log view (Langfuse →
  Evaluators → select evaluator → Logs): Completed / Error / Pending per
  observation, with error details.
- Worker-side logs: `docker logs langfuse-worker | grep -i "code.eval"`.
- Job executions in the database:
  ```bash
  docker exec langfuse-postgres psql -U langfuse -d langfuse \
    -c "SELECT job_configuration_id, status, count(*) FROM job_executions
        WHERE job_configuration_id LIKE 'code-eval%' GROUP BY 1,2"
  ```
- A common gotcha: live filters match on the **observation's** propagated
  trace name. The demo apps propagate it via `propagate_attributes(trace_name=...)`
  (see `text-to-sql/langfuse_config.py`); spans created without propagation
  won't match `Trace Name` filters.

## Editing / adding evaluators

1. Edit or add a `.ts` file in `evaluators/` (keep the `evaluate(ctx)` contract).
2. For a new evaluator, register it in `scripts/seed-code-evaluators.sh`
   (one `seed_evaluator` line: name, default score, target, filter).
3. Re-run `./scripts/seed-code-evaluators.sh`.
4. Or skip the files entirely and iterate in the UI — the editor has a test
   runner against sample observations.
