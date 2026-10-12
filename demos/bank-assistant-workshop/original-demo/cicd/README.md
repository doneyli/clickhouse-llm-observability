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

## Wiring (live)

```
Langfuse: save a version / move a label
  └─► Automation "GitHub Repository Dispatch" (event northwind-prompt-update)
        └─► .github/workflows/northwind-prompt-gate.yml
              1. Decide: read prompt name, version, labels from the payload.
                 Skip (with the reason in the job summary) if it is another prompt,
                 an unlabelled version, or a label the gate can't resolve.
              2. Gate EN + ES golden sets on that label (unique run name per CI run).
                 Job summary: verdict + metric table + link to the Langfuse run.
              3. "Ship it" runs only if it passed AND the version carries `production`.
```

Setup: repo secrets `NORTHWIND_LANGFUSE_BASE_URL`, `NORTHWIND_LANGFUSE_PUBLIC_KEY`,
`NORTHWIND_LANGFUSE_SECRET_KEY`, `ANTHROPIC_API_KEY`; Langfuse project → Prompts →
Automations → Create Automation → events *Created* + *Updated*, optional filter
`northwind-assistant-system` → action **GitHub Repository Dispatch** (URL
`https://api.github.com/repos/<owner>/<repo>/dispatches`, event type
`northwind-prompt-update`, a GitHub token with Actions + Contents read/write on the
repo). There is no public API for automations — it is a UI step.

Manual: Actions → *Northwind prompt gate* → Run workflow → `development`
(blocked) or `staging` (passes). Langfuse also provides `langfuse/experiment-action`;
this demo keeps its own gate script so hard vs soft thresholds stay explicit.

## Local

```bash
.venv/bin/python scripts/prompt_gate.py --prompt-label development                                   # exit 1
.venv/bin/python scripts/prompt_gate.py --prompt-label development --dataset northwind-golden-qa-es-v1 # exit 1
.venv/bin/python scripts/prompt_gate.py --prompt-label staging --warn-only                           # report only
```
