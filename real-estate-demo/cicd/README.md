# Deploy as a real CI/CD pipe — GitHub integration

> **Reference material, not wired into this repo's CI.** These files show how the
> **Deploy** node of the [AI Engineering loop](../AI_ENGINEERING_LOOP.md) becomes a
> true CI/CD pipeline. They can't run on the localhost demo stack (they need a
> real repo, a GitHub PAT, and a public webhook endpoint), so they live here as a
> copy-paste starting point.

## Why this is part of the loop

In this demo the agent fetches its system prompt from Langfuse **by label** at
runtime (`property-concierge-agent` @ `production`). So "shipping a better
prompt" = **moving the `production` label to a new version** — no app redeploy.
That is already a deployment step. The GitHub integration adds *automation and a
gate* around it, turning "someone clicked promote in the UI" into "promotion runs
the evals, then ships on the `production` label" (the example wires the label gate
and runs the eval; add a score-regression threshold to make it a hard quality gate
— see the TODO in the workflow):

```
Experiment / Evaluate  ──►  promote version to `production` in Langfuse
        ▲                              │  (webhook / repository_dispatch)
        │                              ▼
   new traces  ◄── Deploy ◄──  GitHub Actions: run eval → (add threshold) → deploy on label
```

## Two mechanisms (from the Langfuse docs)

1. **Repository Dispatch** — *trigger CI/CD when a prompt changes.* Langfuse fires
   a `repository_dispatch` event to the GitHub API on prompt change; a workflow
   runs your tests/evals and deploys only when the version carries the
   `production` label. No extra infrastructure — just a PAT.
   → see [`langfuse-ci.yml.example`](langfuse-ci.yml.example)

2. **Sync-to-repo** — *version-control prompts in git.* A small webhook server
   receives Langfuse prompt-version events and commits each one to a file in your
   repo, so prompt changes get PR review + full git history.

## Set up mechanism #1 (Repository Dispatch)

1. Copy `langfuse-ci.yml.example` → `.github/workflows/langfuse-ci.yml` in the
   repo that owns your prompts. Add repo **secrets** for `LANGFUSE_HOST`,
   `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `ANTHROPIC_API_KEY`.
2. Create a GitHub **Personal Access Token** with `repo` (or fine-grained
   `actions: read+write`) scope.
3. In Langfuse: **Prompts → Automations → Create Automation → GitHub Repository
   Dispatch**:
   - Dispatch URL: `https://api.github.com/repos/<owner>/<repo>/dispatches`
   - Event Type: `langfuse-prompt-update` (must match the workflow's `types:`)
   - GitHub Token: paste the PAT (stored encrypted).
4. Promote a prompt to `production` in Langfuse → watch the workflow run in the
   Actions tab. The `deploy` job runs only for `production`-labelled versions.

Full guide, payload schema, and the sync-server code:
<https://langfuse.com/docs/prompt-management/features/github-integration>
