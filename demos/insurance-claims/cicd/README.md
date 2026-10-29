# CI/CD — prompt quality gate and configuration as code

## The gate

`scripts/prompt_gate.py` runs `claims-golden-v1` (92 claims) against a **labelled** `claims-sop-rules`
version — the rules step only, on claim files a perfect intake would produce, so the run isolates the
change under review — and compares the run averages with `thresholds.json`:

- **hard** (fail the build): critical-rule recall = 1.0 and zero critical misses, rule recall,
  commission-flag accuracy, citation validity, benefit and interest within tolerance — deterministic
  functions of the output against the SME-codified oracle;
- **soft** (warn): precision, decision match, retrieval coverage.

A missing metric fails. A label that does not resolve to a real version aborts (otherwise the app's
fallback prompt would be graded and reported). A model change is a prompt-config change: it goes
through the same gate (`--model` runs the check before you edit the config).

Why recall and not just "critical recall": the 'concise' candidate (v2) keeps every block but drops a
quarter of the other rules — commission chargebacks and state notices. Each one is a revenue or
regulatory leak, and the LLM judge does not see it.

## Wiring

```
Langfuse: save a claims-sop-rules / annuity-service-rules version, or move a label
  └─► Prompts → Automations → GitHub Repository Dispatch (event claims-prompt-update)
        └─► .github/workflows/claims-prompt-gate.yml   (same template as northwind-prompt-gate.yml)
              1. Decide: prompt name, version, labels from the payload; skip other prompts or unlabelled versions
              2. Gate the label on its answer key — claims-sop-rules → claims-golden-v1 (92),
                 annuity-service-rules → annuity-service-golden-v1 (22, `--suite service`, thresholds under
                 `suites.service`) — unique run name per CI run → job summary + run link
              3. "Ship it" only if the gate passed AND the version carries `production`
```

Setup (once): repo secrets `CLAIMS_LANGFUSE_BASE_URL`, `CLAIMS_LANGFUSE_PUBLIC_KEY`,
`CLAIMS_LANGFUSE_SECRET_KEY`, `ANTHROPIC_API_KEY`; then in the Langfuse project: Prompts → Automations →
Create → events *Created* and *Updated*, filter `claims-sop-rules` and `annuity-service-rules` (or no
filter: the workflow skips every other prompt), action GitHub Repository Dispatch
(URL `https://api.github.com/repos/<owner>/<repo>/dispatches`, event type `claims-prompt-update`, a token
with Actions + Contents read/write). The automation has no public API — it is a UI step.

Every label move fires the automation, including housekeeping moves (`previous-production`): expect a
gate run for each, and its dataset run.

Release flow: new version → label `staging` → **gate (this is the check that blocks)** → promote
(`scripts/prompt_label.py --promote staging`). A `production` label event is already live when the gate
runs (agents fetch by label with a 10 s cache), so on that path the gate is a post-deploy confirmation —
a failure there means roll back. Roll back: `--rollback` (moves the labels; no redeploy).

## Terraform

`terraform/main.tf` shows the two layers a platform team runs from the same pipeline:

1. **Infrastructure** — the official module [`langfuse/langfuse-terraform-aws`](https://github.com/langfuse/langfuse-terraform-aws)
   (EKS, Aurora Postgres, ElastiCache, S3, ClickHouse) inside the company's own AWS account; the
   Enterprise license key unlocks Enterprise features in the same containers.
2. **Langfuse configuration as code** — prompts, evaluators and rules, score configs and datasets are
   public-API calls (the `scripts/seed_*.py` seeders, which reconcile rather than skip). There is no
   Terraform provider for these objects; `terraform_data` + `local-exec` re-runs a seeder when its
   definition file changes.
