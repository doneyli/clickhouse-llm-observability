# Marketing agent debugging workshop

A Claude tool-use agent that does AI-search marketing work for three fictional
customers, traced end to end in Langfuse, with four realistic failures planted in its
environment. It's built for one job: teaching product managers and deployment teams to
debug a **probabilistic** agent (one that picks its own tools) from the Langfuse UI,
without an engineer.

- **Presenter script:** [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md), a two-hour run of show with four case files.
- **Participant handout:** [CHEAT_SHEET.md](CHEAT_SHEET.md), a one-page "where do I click".
- **Why this demo exists:** [ASSESSMENT.md](ASSESSMENT.md), what the other demos in this repo do and don't cover for this audience.

## What's in the box

```
marketer task ──► marketing-agent (Claude Opus 5.5, adaptive thinking, 7 tools, ≤10 steps)
                    ├─ get_visibility_report     share of voice per answer engine
                    ├─ search_answer_engine      what ChatGPT / Perplexity / Gemini / AI Overviews / Copilot say
                    ├─ retrieve_brand_context    the customer's Context Manager (retriever)
                    ├─ fetch_page                the customer's CMS
                    ├─ check_claims              fact-checker (its own model call)
                    ├─ queue_correction          correction request, held for approval
                    └─ stage_cms_draft           page draft, held for approval
                  └─ check-publishing-policy     guardrail: fact-check before publishing, step limit
```

| Customer | Task you'll debug | Planted fault (in the data or tools, not the model) | Question it teaches |
|---|---|---|---|
| Contoso (agency software) | `pricing-check` | the Context Manager still serves an archived 2024 pricing FAQ, and passages carry no dates | did it retrieve the right info? |
| Fabrikam Running | `page-refresh` | uncached CMS (4–6 s per page), the harness runs parallel tool calls one by one, one page returns raw HTML (~45k tokens), one URL 404s | why is it slow and expensive? |
| Tailspin Travel | `weekly-report` | Copilot capture climbs to 99% and never finishes, and the instructions say "retry until you have it" | why does it keep going? |
| Tailspin Travel | `faq-page` | instructions v3 ("Speed up drafts") removed the fact-check rule | why did it skip a step? |

Each fault has a switch so you can show the fix live: `--fix exclude-archived`,
`--fix parallel-tools --fix clean-html`, `--fix terminal-pending`, or a prompt-label
change for the instructions cases.

What Langfuse shows: one trace per run (`marketing-agent`, an `agent` observation); a
`generation` per model call with the full tool list, messages, **summarized thinking**
and the linked instructions version; `tool`/`retriever` observations with WARNING/ERROR
levels; a `guardrail`; sessions for multi-turn chats; scores `reviewer-decision`
(categorical + reason), `agent-steps` and `publishing-policy`; customer, task and
instructions version as metadata on every observation; and a dashboard, annotation
queue and dataset seeded around it.

## Run it

Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/). Everything runs against Langfuse
Cloud; no Docker.

```bash
cd demos/marketing-agent-debugging
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt pytest
```

**1. A Langfuse project.** Either put an organization API key in `.env.cloud-org`
(`LANGFUSE_ORG_PUBLIC_KEY`, `LANGFUSE_ORG_SECRET_KEY`, `LANGFUSE_BASE_URL`) and run:

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/provision_cloud.py
```

or copy `.env.example` to `.env` and fill in an existing project's keys plus
`LANGFUSE_PROJECT_NAME`. `agent/config.py` refuses to run if the keys belong to a
different project; shell-exported `LANGFUSE_*` variables are ignored on purpose.

**2. Seed the project** (prompts, score configs, queue, dataset, dashboard, then traffic):

```bash
.venv/bin/python scripts/seed_prompts.py
.venv/bin/python scripts/seed_project.py all
.venv/bin/python scripts/seed_traffic.py          # ~26 real agent runs, ~5 min with 4 workers, ~$6-8
.venv/bin/python scripts/seed_project.py fill-queue
.venv/bin/python scripts/verify_demo.py           # READY + the hero trace for each case
```

**3. Live runs** (each prints the trace URL):

```bash
.venv/bin/python scripts/run_agent.py --scenario pricing-check
.venv/bin/python scripts/run_agent.py --scenario pricing-check --fix exclude-archived
.venv/bin/python scripts/run_agent.py --scenario page-refresh --fix parallel-tools --fix clean-html
.venv/bin/python scripts/run_agent.py --scenario weekly-report --fix terminal-pending
.venv/bin/python scripts/run_agent.py --scenario faq-page --prompt-version 2
.venv/bin/python scripts/run_agent.py --scenario content-brief --all-turns
.venv/bin/python scripts/run_agent.py --customer contoso --task "How visible are we on Gemini?"
```

Rehearse with `LANGFUSE_TRACING_ENVIRONMENT=development` in front of a command, so
rehearsal runs stay out of the production view. After a session where you "fixed"
Tailspin's instructions live, run `scripts/seed_prompts.py --reset-tailspin`.

**Tests** (offline, no keys needed): `.venv/bin/python -m pytest -q`. They pin every
planted fault, every fix, the reviewer rules and the trace message shape.

## Honest notes

- **Real agent, synthetic world.** Claude makes every decision; the tools return
  deterministic synthetic data with simulated latency (`agent/world.py`, `agent/tools.py`).
  All brands, people and domains are fictional (`.example`).
- **The reviewer is simulated.** `reviewer-decision` comes from rules in
  `agent/review.py` that encode what each customer's marketer would object to. In
  production these are your users' approve/reject clicks.
- **Outcomes vary on purpose.** Opus usually notices the stale Contoso pricing and
  checks the live page; once in a while it asks the marketer instead. The fact-checker
  is fooled on every run. Cases 3 and 4 reproduce on every run (3/3 in the seed).
- **Cost.** A run costs $0.05–0.45 on Opus 5.5. The Fabrikam raw-HTML page is the
  expensive one, which is the point of Case 2. Set `AGENT_MODEL` to try another model.
- **The Anthropic SDK emits its own OpenTelemetry spans.** `agent/config.py` filters
  them out so model cost isn't counted twice. If you adapt this code, keep that filter.

## Files

| Path | What |
|---|---|
| `agent/loop.py` | the agent loop and all Langfuse instrumentation |
| `agent/tools.py` | tool schemas, synthetic backends, fault switches |
| `agent/world.py` | the three customers: Context Manager docs, answer-engine snapshots, pages |
| `agent/instructions.py` | per-customer instructions and Tailspin's version history |
| `agent/review.py` | publishing-policy guardrail and simulated reviewer |
| `agent/scenarios.py` | the marketer tasks |
| `scripts/` | provision, seed, run, verify |
| `tests/test_demo.py` | offline tests |
