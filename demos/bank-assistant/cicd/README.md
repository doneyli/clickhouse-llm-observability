# Prompt quality gate in CI (EXP-06)

`scripts/prompt_gate.py` runs a golden dataset against a **labelled** prompt
version and compares the run-level averages with `thresholds.json`:

- **hard** thresholds (deterministic metrics: must-include facts, source recall,
  language match, no unsolicited upsell, formal Spanish register) fail the build;
- **soft** thresholds (LLM-judge averages such as correctness) only warn —
  re-running an unchanged prompt moves a judge average by a few points.

A missing metric counts as a failure. A label that does not resolve to a real
prompt version aborts the run (otherwise the app's fallback prompt would be
graded and reported as the candidate).

Per-dataset thresholds live under `per_dataset` (the Spanish golden set adds
`avg-formal-register` and requires `avg-language-match = 1.0`).

## Wiring

```
Langfuse prompt change ──► Automation: GitHub Repository Dispatch (event northwind-prompt-update)
                       ──► .github/workflows/northwind-prompt-gate.yml
                       ──► prompt_gate.py (EN + ES golden sets) ──► exit 1 blocks promotion
```

Secrets: `NORTHWIND_LANGFUSE_BASE_URL`, `NORTHWIND_LANGFUSE_PUBLIC_KEY`,
`NORTHWIND_LANGFUSE_SECRET_KEY`, `ANTHROPIC_API_KEY` (+ `OPENAI_API_KEY`).
Langfuse also provides `langfuse/experiment-action` for GitHub Actions; this demo
uses its own gate script to keep hard vs soft thresholds explicit.

## Local

```bash
.venv/bin/python scripts/prompt_gate.py --prompt-label development                       # exit 1
.venv/bin/python scripts/prompt_gate.py --prompt-label development --dataset northwind-golden-qa-es-v1   # exit 1
.venv/bin/python scripts/prompt_gate.py --prompt-label staging                           # promotable?
```
