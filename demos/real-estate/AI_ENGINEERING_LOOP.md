# The AI Engineering Loop — mapped to this demo

This demo is built to showcase the **entire [AI Engineering
loop](https://langfuse.com/academy/ai-engineering-loop)** for one real use case —
a property-search concierge for an online real-estate marketplace — not just a
technical tour of Langfuse features.

> The loop is how teams continuously improve an AI system: you **can't unit-test
> your way to confidence** with probabilistic LLM outputs, so you observe
> production, learn from it, and improve through experiments — then ship and
> repeat.

## The loop

```
                            ┌─────────────────────────────────────┐
                            │               DEPLOY                │
                            │   prompt labels · GitHub CI/CD      │
                            └───────────────▲───────────┬─────────┘
   ── ONLINE (understand production) ──     │           │   ── OFFLINE (improve) ──
                                            │           ▼
   ┌───────────┐     ┌───────────┐     ┌────┴──────┐  ┌────────────┐  ┌───────────┐
   │  1 TRACE  │ ──► │ 2 MONITOR │ ──► │ 3 DATASETS│─►│4 EXPERIMENT│─►│ 5 EVALUATE│
   │ traces·   │     │ dashboards│     │ real +    │  │ prompts·   │  │ judges·   │
   │ sessions· │     │ LLM-judge·│     │ designed  │  │ models·    │  │ code evals│
   │ agents·   │     │ feedback  │     │ cases     │  │ code       │  │ ·annotate │
   │ prompts   │     │           │     │           │  │ variants   │  │           │
   └───────────┘     └───────────┘     └───────────┘  └────────────┘  └───────────┘
        ▲                                                                    │
        └──────────────  ship a change → new traces → repeat  ◄─────────────┘
```

The loop clusters into two areas of work:
- **Online — understand what's happening in production:** Trace + Monitor.
- **Offline — improve systematically during development:** Datasets + Experiment + Evaluate.
- **Deploy** connects the two: a validated improvement (usually a prompt) goes
  live, produces new traces, and the loop turns again.

## Where each step lives in this demo

| # | Loop step | What it is | In this demo | Where to see it |
|---|-----------|-----------|--------------|-----------------|
| 1 | **Trace** | Full path of each request: prompts, tools, outputs, latency, cost | `agent/concierge.py` emits `plan → agent-turn → tool:* → synthesis`; multi-turn = one trace; sessions; the **prompt version is linked to each generation** | Langfuse **Tracing** / **Sessions**; DEMO_SCRIPT Act 2 |
| 2 | **Monitor** | Surface the traces that deserve attention over time | Managed LLM-as-a-Judge (auto) + custom SDK judges + code scores + **👍/👎 user feedback** from the portal; **Dashboards** for cost/latency/score trends | Langfuse **Evaluators**, **Dashboards**; DEMO_SCRIPT Acts 1, 3 / close |
| 3 | **Build datasets** | Turn real + designed scenarios into repeatable test cases | `data/dataset.py` → `property-concierge-eval` (18 curated items across Europe, incl. an impossible one); production traces can be added to it from the UI | Langfuse **Datasets**; `scripts/seed_dataset.py`; DEMO_SCRIPT Act 5 |
| 4 | **Experiment** | Change one variable, compare vs a baseline | Same agent + dataset + evaluators across **models** (Claude vs GPT-4o) **and prompt versions** (production vs candidate) | `scripts/run_experiment.py --model / --prompt-label`; Langfuse **Datasets → Runs → Compare** |
| 5 | **Evaluate** | Decide if it's good enough to ship | Deterministic **code** evals, **LLM-as-a-Judge** (managed + custom), **human annotation** queue | `agent/scoring.py`, `evaluators/`, `scripts/seed_annotation_queue.py`; DEMO_SCRIPT Acts 3–4 |
| ⟳ | **Deploy** | Ship the change; it becomes new production traffic | Prompts fetched **by label** at runtime → promoting a version ships it; **GitHub CI/CD** gates + automates promotion | `agent/prompts.py`, `scripts/seed_prompts.py`, [`cicd/`](cicd/); section below |

## The Deploy node (the piece that makes it a *loop*, not a pipeline)

The agent no longer hard-codes its system prompt. `agent/prompts.py` fetches
`property-concierge-agent` **by label** (`production` by default) at runtime,
with a hard-coded fallback so the demo still runs if Langfuse is unreachable.
Two consequences:

- **Deploying a prompt = moving the `production` label** to a new version in
  Langfuse. The app picks it up on the next fetch — no code change, no redeploy.
- Because the fetched version is **linked to every generation**, you get
  per-prompt-version metrics (latency, cost, scores) in the Prompts → Metrics
  tab — so you can tell whether the version you shipped actually helped.

**As a true CI/CD pipe:** the [Langfuse GitHub
integration](https://langfuse.com/docs/prompt-management/features/github-integration)
turns promotion into an automated, gated deploy. A prompt change fires a
`repository_dispatch` event; a GitHub Actions workflow runs the eval dataset
against the new version and only ships if it passes and carries the `production`
label. See [`cicd/`](cicd/) for a ready-to-copy workflow and setup steps.

## Closing the loop — the demonstrable cycle

This is the money path (DEMO_SCRIPT Act 6). Every step is real and runnable:

1. **Monitor finds headroom.** Even on good traffic the *subjective* metrics sit
   below 1.0 — on the 18-item eval set, `production` scores helpfulness 0.90,
   relevance 0.89, groundedness 0.92 (the deterministic code checks are already
   1.00). That gap is the improvement target. *(Separately, Act 3's fault-injected traces show
   evals catching hard failures — but those faults are injected in code, not
   caused by the prompt, so they belong to "evals catch problems", not here.)*
2. **Lock the scenarios as tests.** The `property-concierge-eval` dataset already
   encodes the buy/rent, EN/ES, multi-city and impossible-request cases; any real
   production trace worth guarding against can be added to it from the UI.
3. **Hypothesize a fix — a new prompt.** The `candidate` version
   (`agent/prompts.py`) tightens grounding, adds budget discipline, enforces the
   user's language, and imposes a scannable format.
4. **Experiment to prove it** — run the *same* dataset + evaluators on
   `production` vs `candidate`; only the prompt changed:
   ```bash
   ./.venv/bin/python scripts/run_experiment.py --prompt-label production
   ./.venv/bin/python scripts/run_experiment.py --prompt-label candidate
   # Langfuse → Datasets → property-concierge-eval → Runs → select both → Compare
   ```
   **Measured result (Claude, 18 items) — a marginal edge, and a lesson in judge
   noise:** on a matched run the candidate nudged the metrics it targeted —
   **relevance 0.89 → 0.91, helpfulness 0.90 → 0.91** — held **groundedness
   (0.92 → 0.91)** and **tone** (*good ×14 / poor ×4* → *good ×13 / poor ×4 /
   excellent ×1*), and kept **every deterministic code metric at 1.00**. But those
   judge deltas (±0.01–0.02) sit *inside* the run-to-run noise: repeating the
   **same** production prompt swung groundedness across 0.89–0.96. So the honest
   read isn't "candidate wins by X" — it's that the change **regressed nothing**,
   and the **deterministic code evals (rock-steady at 1.00) are what you gate on**,
   not a single noisy judge number.
   *(`tone` is categorical — a label distribution in the compare view, not a mean.
   The discipline the loop teaches: re-run and compare; don't ship on one number.)*
5. **Decide with the data — deploy, or iterate.** This is a judgment call, and
   Langfuse gave you the evidence to make it instead of guessing:
   - **Ship it (carefully)** — the candidate regressed nothing and held every code
     metric at 1.00, so it's safe to promote `candidate` to the `production` label
     (Langfuse UI, or via the GitHub CI/CD gate); the app fetches `production`, so it
     serves the new prompt with no redeploy. Confirm with a repeat run first — the
     judge edge is within noise.
   - **Or keep iterating** — because a single run's judge delta isn't signal, the
     honest move is to re-run, widen the dataset, or design a sharper `candidate-v2`.
     The loop is a loop precisely because the first fix is rarely the last.
6. **New traces** flow under whatever you shipped → back to step 1.

## What's live vs. documented-only (honest scope)

| Capability | Status in this demo |
|---|---|
| Prompt management, versioning, label-based fetch, prompt↔trace link | **Live** — runs on the local stack |
| Prompt-variant experiment + compare | **Live** — `run_experiment.py --prompt-label` |
| Promote a label to deploy | **Live** — do it in the Langfuse UI |
| GitHub repository-dispatch CI/CD + sync-to-repo | **Documented** — needs a real repo, PAT, public webhook; see [`cicd/`](cicd/) |
| Add production trace → dataset | **Live in UI** — one click on a trace |
| Explicit **user feedback** as a Monitor signal | **Live** — 👍/👎 in the portal writes a `user-feedback` score onto the conversation's trace (`webapp` `/api/feedback`) |
