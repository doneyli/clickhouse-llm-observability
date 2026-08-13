# Deploy as a real CI/CD pipe — GitHub integration

> **This is live now.** The workflow at
> [`.github/workflows/langfuse-prompt-ci.yml`](../../../.github/workflows/langfuse-prompt-ci.yml)
> really runs: a prompt change in Langfuse fires it, it re-runs the eval dataset
> against the changed version, and it **fails the build** if the quality bar in
> [`thresholds.json`](thresholds.json) is missed. It needs three one-time setup
> steps (secrets, a PAT, a Langfuse automation) — see [Setup](#setup) below.

## Why this is part of the loop

The agent fetches its system prompt from Langfuse **by label** at runtime
(`property-concierge-agent` @ `production`). So "shipping a better prompt" =
**moving the `production` label to a new version** — no app redeploy. That is
already a deployment step. CI adds the *gate*, turning "someone clicked promote
in the UI" into "the eval suite ran and agreed":

```
Experiment / Evaluate  ──►  promote version to `production` in Langfuse
        ▲                              │  (repository_dispatch)
        │                              ▼
   new traces  ◄── Deploy ◄──  GitHub Actions: run eval → enforce thresholds → ship
```

## The three pieces

| File | Role |
|---|---|
| [`../scripts/prompt_gate.py`](../scripts/prompt_gate.py) | Runs `property-concierge-eval` against one prompt label, compares run-level means to the thresholds, exits non-zero on a miss, and writes a verdict table to the Actions job summary. |
| [`thresholds.json`](thresholds.json) | **The quality bar as code.** Deterministic code evaluators gated hard; LLM judges gated loosely (they carry ±0.05 run-to-run noise, so a tight judge threshold makes builds flaky). |
| [`.github/workflows/langfuse-prompt-ci.yml`](../../../.github/workflows/langfuse-prompt-ci.yml) | Wires it to GitHub: `repository_dispatch` from Langfuse, plus a `workflow_dispatch` dropdown for demoing on demand. Deploy job runs only for `production`-labelled versions that passed. |

## Why it targets Langfuse Cloud

A GitHub runner cannot reach `localhost:3001`. The gate has to talk to Langfuse
to fetch the dataset and the prompt version, so **the demo must run against
Langfuse Cloud** for the CI half to be real. That is why
`demos/real-estate/.env` points at `us.cloud.langfuse.com`
(`.env.selfhosted.bak` holds the self-hosted config if you want to switch back).

On a purely self-hosted stack you can still demo the **trigger** — Langfuse
makes an *outbound* POST to api.github.com, so the workflow fires and you can
watch the run appear — but the eval step will fail to reach Langfuse. Use
`--warn-only` or expect a red build if you go that route.

## Setup

Three one-time steps. **You have to do these yourself** — they involve creating
credentials.

**1. Repo secrets** (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `LANGFUSE_HOST` | `https://us.cloud.langfuse.com` |
| `LANGFUSE_PUBLIC_KEY` | the Cloud project's public key (`pk-lf-…`) |
| `LANGFUSE_SECRET_KEY` | the Cloud project's secret key (`sk-lf-…`) |
| `ANTHROPIC_API_KEY` | agent + judge model calls |

If your Cloud project isn't named `real-estate`, also add a repo **variable**
`LANGFUSE_PROJECT_NAME` — `agent/config.py` refuses to run against an unexpected
project on purpose, so the name has to match.

**2. A GitHub PAT** with fine-grained `actions: read+write` (or classic `repo`)
scope, so Langfuse is allowed to trigger the workflow.

**3. The Langfuse automation** — Prompts → **Automations** → Create Automation →
**GitHub Repository Dispatch**:

- Dispatch URL: `https://api.github.com/repos/doneyli/clickhouse-llm-observability/dispatches`
- Event Type: `langfuse-prompt-update` (must match the workflow's `types:`)
- GitHub Token: the PAT from step 2 (stored encrypted)

Then promote a prompt version to `production` in Langfuse and watch the run
appear in the Actions tab.

## What the automation actually fires on (and why the workflow skips things)

Langfuse sends a `repository_dispatch` on **every** prompt-version event in the
project — `created`, `updated` and `deleted` — and automations **cannot** be
filtered by prompt name or label ([open feature
request](https://github.com/orgs/langfuse/discussions/9268)). So the workflow
receives events it has nothing useful to say about, and decides for itself.

It **skips with a stated reason** in three cases:

| Case | Why skip |
|---|---|
| A different prompt changed (e.g. `property-concierge-plan`) | The eval dataset only exercises `property-concierge-agent`; a run is ~12 min of real LLM spend. |
| The version carries no deployable label (only `latest`, or none) | This is what a version created in the UI *without* promoting it looks like. |
| The label isn't `production` / `candidate` / `first-draft` | The gate resolves prompts **by label**, and `agent/prompts.py` falls back to a hard-coded baseline for a label it can't find. |

That third reason is the important one, and it's why skipping beats running.
`prompt_gate.py` asks for a prompt *by label*; if the label doesn't resolve, the
SDK hands back the local fallback and the eval scores **that** — producing a green
build for a version it never looked at. A skipped run says so explicitly:

> ⏭️ Skipped — nothing to evaluate. This is a deliberate decision, not a passing
> gate. No prompt version was evaluated, so this run makes **no claim** about quality.

A skipped gate *succeeds*, so the deploy job carries a third condition
(`skip == ''`) on top of "eval passed" and "labelled production".

**Consequence worth knowing:** creating a new version in the UI does **not** get
you a quality signal until you label it. Promote it to `candidate` (validated, not
deployed) or `production` (validated and deployed). The proper fix is to gate the
exact `prompt.version` from the payload rather than a label — that needs version
support in `agent/prompts.py`, and is the natural next change here.

## Demoing it without promoting a prompt

Promoting a prompt to trigger CI is a slow, one-shot beat. For a live demo use
the **workflow_dispatch** dropdown in the Actions tab instead — it takes a
`prompt_label`:

- `first-draft` → **the gate blocks it.** `budget-adherence` and `language-match`
  fall below the bar, the build goes red, and the deploy job never runs. This is
  the beat worth showing.
- `candidate` → passes the bar, but the deploy job is skipped because the version
  isn't labelled `production`. Validated ≠ deployed.
- `production` → passes and deploys.

Run the exact same gate locally to rehearse:

```bash
./.venv/bin/python scripts/prompt_gate.py --prompt-label first-draft
```

## The other mechanism: sync-to-repo

Separately from triggering CI, Langfuse can **version-control prompts in git**: a
small webhook server receives prompt-version events and commits each one to a
file, so prompt changes get PR review and full git history. That one does need a
publicly reachable endpoint, so it stays reference-only here. Full guide and the
receiver code: <https://langfuse.com/docs/prompt-management/features/github-integration>

## Upgrade path: the official action

Langfuse ships [`langfuse/experiment-action`](https://github.com/langfuse/experiment-action),
which wraps this pattern — you export an `experiment(context)` function and raise
`RegressionError` instead of hand-rolling threshold checks. It requires the **v4**
Python SDK — which this demo now uses (`langfuse>=4.7,<5.0`), so the action is
unblocked and both `RunnerContext` and `RegressionError` are importable from
`langfuse`. `prompt_gate.py` is still the gate today because it renders the
per-metric threshold table the demo talks through; the threshold logic in
`evaluate_gate()` is the part to lift across when switching to the action.

[`langfuse-ci.yml.example`](langfuse-ci.yml.example) is kept as a minimal,
repo-agnostic starting point to hand to a customer whose layout differs from
ours — the live workflow above is the one to look at first.
