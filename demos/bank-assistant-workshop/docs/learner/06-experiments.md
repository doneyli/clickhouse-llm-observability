**DRAFT teaching material.**

# 06 Experiments

Start `06-experiments`. The agent works, but `experiment_hooks.make_task` is deliberately unfinished.

**Goal:** test the actual bank agent, including RAG and guardrails, with repeatable item scores.

Verify that module 03's baseline prompt and module 05's reviewed dataset exist. If you skipped ahead, complete those Cloud helper steps first. Stop a previous checkpoint's processes and start this checkpoint's services.

Open `src/northwind/experiment_hooks.py`. Implement `make_task` as an async task accepting `item`; read `item.input["question"]`, then call the same `agent.run_turn` used by the portal. Pass customer ID, a unique experiment session, `channel="experiment"`, model, requested prompt label and the strictly resolved prompt version. The label records the requested comparison; the version pins what runs. Return the answer plus source/tool/blocked fields. Raise on application errors or a fallback prompt.

Then add the callback evaluator implementation from the package root's reference `experiment_hooks.py`. Read it before copying. It provides:

| Score | What it measures | Limit |
| --- | --- | --- |
| `must-include` | Fraction of reference keywords present. | Negated or wrong-context numbers can pass. |
| `cites-expected-source` | The answer contains an expected KB ID. | A citation does not prove the claim is supported. |
| `language-match` | Heuristic question/answer language match. | Short or mixed-language text can misclassify. |
| `refused-safely` | Refusal on expected-blocked items. | Restricted to those test cases. |
| `correctness` | LLM judgment against reference facts. | Requires calibration; not a release gate. |

Run with one concurrent task:

```bash
../../.venv/bin/python workshop.py --env-file ../../.env experiment --prompt-label workshop-baseline
```

This costs model calls for the agent plus a judge call per item. `--no-judge` runs deterministic callbacks only when the workshop budget or model access is limited. Application signals are not emitted again for experiment turns.

The runner strictly resolves the prompt label once, freezes that version, selects ACTIVE dataset items and calls `dataset.run_experiment`. An empty active dataset or malformed reference metadata stops the run before tasks execute. Task failures, missing dataset-run links or missing applicable callback scores produce an incomplete run and a non-zero command exit, including deterministic-only runs. A recorded score of zero is a completed evaluation, not an infrastructure error.

**Verify:** all eight items complete; both deterministic and judge scores appear; tool/retrieval traces are present; each item used the recorded prompt version; judge failures are missing evidence and visible for rerun.

A prompt-only UI experiment is a different path: it does not execute this LangGraph/MCP/RAG app. Use the SDK runner for this workshop's agent comparison.

Source: [MCP experiment SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk), [MCP LangGraph integration](https://langfuse.com/integrations/frameworks/langchain). HIGH confidence mechanics, level 1. Score suitability is MEDIUM, Zone B: review before operational use.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
