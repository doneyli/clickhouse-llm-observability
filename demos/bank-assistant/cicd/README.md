# Prompt Quality Gate

The gate evaluates a labelled `northwind-assistant-system` prompt against
the English and Spanish golden datasets. It compares run averages with
[thresholds.json](thresholds.json):

- **Hard thresholds:** deterministic checks; a failed or missing metric exits 1.
- **Soft thresholds:** LLM-judge averages; a failed or missing metric warns.
- **Unresolved label:** aborts rather than evaluating the fallback prompt.
- **Report-only mode:** `--warn-only` returns 0 even when hard checks fail.

Dataset-specific thresholds extend the defaults; the Spanish set adds formal
register and requires perfect language matching.

## Run Locally

Complete the [quick start](../README.md#quick-start) and
[evaluation setup](../README.md#prepare-evaluations). MCP must be running.
Run commands from `demos/bank-assistant`, not `cicd/`.

```bash
.venv/bin/python scripts/prompt_gate.py --prompt-label staging
.venv/bin/python scripts/prompt_gate.py --prompt-label staging \
  --dataset northwind-golden-qa-es-v1
```

Both gates must exit 0 before promoting the candidate. To demonstrate a
regression, substitute `development`; the seeded growth prompt is intended
to fail. Each run consumes model tokens and prints a Langfuse evidence link.

## Set Up GitHub Actions

Use [northwind-prompt-gate.yml](../../../.github/workflows/northwind-prompt-gate.yml)
in your repository.

1. Add repository secrets: `NORTHWIND_LANGFUSE_BASE_URL`,
   `NORTHWIND_LANGFUSE_PUBLIC_KEY`, `NORTHWIND_LANGFUSE_SECRET_KEY`,
   and `ANTHROPIC_API_KEY`. They must point to the same project you seeded.
2. In Langfuse, open **Prompts > Automations > Create Automation**.
3. Select prompt **Created** and **Updated** events. Filter to
   `northwind-assistant-system`.
4. Choose **GitHub Repository Dispatch**. Set the URL to
   `https://api.github.com/repos/<owner>/<repo>/dispatches` and event type
   `northwind-prompt-update`. A fine-grained GitHub token needs repository
   **Contents: write** permission for this endpoint
   ([GitHub requirements](https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event)).
5. In GitHub, open **Actions > Northwind prompt gate > Run workflow**.
   Run `development`, then `staging`. Check the verdict, metric table,
   and Langfuse link in each job summary.

Automation setup is a Langfuse UI step. The workflow also supports manual runs.

## Workflow Behavior

```text
Prompt change -> Repository dispatch -> Select label
  -> English gate -> Spanish gate -> "Ship it" summary (production only)
```

The workflow skips other prompts, unlabelled versions, and unsupported labels.
A skipped run makes no quality claim. A failed gate prevents the dependent
**Ship it** job; that job only writes a summary and does not move prompt labels.

The assistant reads the `production` label at runtime with a 10-second cache.
Moving that label changes the served prompt before a dispatched CI run finishes.
For a release check, evaluate `staging` first, then promote it after both gates
pass. Enforcing this sequence requires label protection and a controlled
promotion process; the demonstration workflow alone does not prevent a direct
`production` label change.
