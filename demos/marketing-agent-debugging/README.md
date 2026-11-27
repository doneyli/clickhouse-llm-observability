# Marketing Agent Debugging Workshop — Two-hour workshop where PMs and deployment teams debug a tool-using marketing agent from the Langfuse UI, through four planted faults with live fixes

A Claude tool-use agent that does AI-search marketing work for three fictional
customers (Contoso, Fabrikam Running and Tailspin Travel), traced end to end in
Langfuse, with four realistic failures planted in its environment. It's built for one
job: teaching product managers and deployment teams to debug a **probabilistic** agent
(one that picks its own tools) from the Langfuse UI, without an engineer.

The model is real Claude, so the agent's decisions are real and vary from run to run.
The customers, their data and the tools are synthetic.

**The question it answers:** when a customer says the agent did something wrong, slow
or strange, how does a PM find out why without waiting for an engineer?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Evaluate · Deploy |
| **Stack** | Python · Anthropic SDK · FastAPI |
| **Runs as** | Workshop kit: facilitator-led, own toolchain and `.env` |
| **Langfuse** | Cloud · project `marketing-agent-debugging` |
| **Length** | 120 min |
| **Present it** | [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md) |
<!-- demo-card:end -->

- **Present it:** [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md), the facilitator's run of show (120 minutes, four case files). [LIVE_SCRIPT.md](LIVE_SCRIPT.md) is the one-page version to keep open while presenting
- **Participant handout:** [CHEAT_SHEET.md](CHEAT_SHEET.md), a one-page "where do I click"
- **Design record:** [SPEC.md](SPEC.md), with the gap analysis against the other demos in [ASSESSMENT.md](ASSESSMENT.md)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Agent tracing with typed observations (Langfuse SDK v4) | One `marketing-agent` trace per run: an `agent` root, a `generation` per model call, a `tool` or `retriever` per tool call with WARNING and ERROR levels, and a `guardrail` (`agent/loop.py`) |
| Reading one decision | Every `plan-next-step` generation carries the tool menu, the messages, the tool calls, the **summarized thinking** and the linked instructions version (module 2) |
| Finding runs by customer, person or symptom | Customer, task type and instructions version are metadata on every observation; users are marketers; multi-turn chats are sessions (module 1) |
| Retrieval debugging | `retrieve_brand_context`: the output is what the model saw, the metadata is what the index knew (Case 1) |
| Latency and cost | Timeline, and token usage with cache reads and writes on each generation (Case 2) |
| Loops and skipped steps | The agent graph's counters and cycles, and the guardrail observation (Cases 3 and 4) |
| Prompt management | `marketing-agent/<customer>` served by the `production` label; version history, diff, and a label move as deploy or rollback (Cases 3 and 4) |
| Scores | `reviewer-decision` (categorical, with a reason), `agent-steps`, and `publishing-policy` on the guardrail observation |
| Managed LLM judges | `request-fulfilled` and `retrieval-relevance` score every new run (module 4) |
| Monitoring | The `Agent health — deployment view` dashboard and the Observations table as an analysis tool (module 4) |
| Human review | Comments, the `PM review — rejected agent runs` annotation queue with a `root-cause` label, and the `regression-cases` dataset (module 5) |

## Who it's for

Product managers and deployment teams who run one agent for many customers and want to
answer "why did the agent do that?" themselves. Nobody in the room needs an account or
writes code. Pick [bank-assistant](../bank-assistant/) instead for a capability tour for
engineers, and [bank-assistant-workshop](../bank-assistant-workshop/) when learners will
instrument an agent themselves. [ASSESSMENT.md](ASSESSMENT.md) compares the alternatives.

## What runs where

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

The agent runs in one Python process, started by the presenter portal
(`webapp/server.py`, FastAPI on http://localhost:8097), by `scripts/run_agent.py` or by
`scripts/seed_traffic.py`. The tools are in-process and synthetic. There is no Docker and
no other service; everything is written to one Langfuse Cloud project.

What lands in Langfuse: trace `marketing-agent`, environment `production` (rehearsals
use `development`). Multi-turn chats and every portal run get a session named
`<customer>-chat-<8 hex>`. Tags are `customer:<id>`, `task:<scenario>` and
`surface:<ai-marketer-chat|portal>`; metadata is `customer`, `task_type`, `instructions`
and `fixes`.

## Set up from scratch

Prerequisites: Python 3.10 or later, [uv](https://docs.astral.sh/uv/), an Anthropic API
key and a Langfuse Cloud project named `marketing-agent-debugging`.

```bash
cd demos/marketing-agent-debugging
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
cp .env.example .env                       # then fill in the project's keys and ANTHROPIC_API_KEY
.venv/bin/python scripts/seed_all.py       # on a fresh project: ~26 real agent runs, about $6-8, ~5 min
.venv/bin/python scripts/verify_demo.py    # should print READY, plus the hero trace for each case
```

To have the project created for you, skip the `cp` line: put an organization API key in
`.env.cloud-org` (`LANGFUSE_ORG_PUBLIC_KEY`, `LANGFUSE_ORG_SECRET_KEY`,
`LANGFUSE_BASE_URL`) and run this. It writes `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/provision_cloud.py
```

The `.env` is the only source of `LANGFUSE_*`: its values override shell-exported keys,
and `agent/config.py` refuses to run if the keys belong to another project. The one
setting meant to come from the shell is `LANGFUSE_TRACING_ENVIRONMENT`, for rehearsals.

`seed_all.py` is the one seed entry point. It runs `seed_prompts.py`,
`seed_project.py all`, `seed_evaluators.py`, `seed_traffic.py` and
`seed_project.py fill-queue`, in that order. Every step but traffic is free and safe to
repeat. Traffic makes model calls, so it runs only when the project has fewer than 20
agent runs; `--no-traffic` skips it, `--traffic` forces it, `--dry-run` prints the plan.
`./setup.sh` does not run this demo's seed.

## Before a session

| When | Do | Why |
|---|---|---|
| The day before | `.venv/bin/python scripts/seed_all.py`, then `.venv/bin/python scripts/verify_demo.py` | Seeded artifacts exist, and the corpus has not aged out of the project's 90-day retention |
| 30 minutes before | `.venv/bin/python scripts/seed_prompts.py --reset-tailspin`, then `verify_demo.py` again | Puts `production` back on Tailspin v3 in case a rehearsal fixed it; prints READY and the hero-trace links |
| 30 minutes before | Start the portal and run Fabrikam `social-posts` once (about 20 s) | The first run on stage is not a cold start |
| 5 minutes before | Open the hero-trace links; set the Tracing time range to the last 7 days | The default range is one day, and seeded runs are older |

The full checklist, including the Langfuse UI settings the session needs, is in the
guide's [Before the session](WORKSHOP_GUIDE.md#before-the-session).

## Run it

```bash
.venv/bin/python webapp/server.py        # presenter portal on http://localhost:8097
```

The portal is the marketer's workspace in front of the agent, with a presenter console
for the fault switches and the instructions version. Each run streams its tool calls
and ends with an "Open trace in Langfuse" button. The same runs from the terminal, each
printing its trace URL ($0.05–0.45 and 15–90 s per run on Opus 5.5):

```bash
.venv/bin/python scripts/run_agent.py --scenario pricing-check
.venv/bin/python scripts/run_agent.py --scenario pricing-check --fix exclude-archived
.venv/bin/python scripts/run_agent.py --scenario page-refresh --fix parallel-tools --fix clean-html
.venv/bin/python scripts/run_agent.py --scenario weekly-report --fix terminal-pending
.venv/bin/python scripts/run_agent.py --scenario faq-page --prompt-version 2
.venv/bin/python scripts/run_agent.py --scenario content-brief --all-turns     # one session, three traces
.venv/bin/python scripts/run_agent.py --customer contoso --task "How visible are we on Gemini?"
```

Rehearse with `LANGFUSE_TRACING_ENVIRONMENT=development` in front of a command, so
rehearsal runs stay out of the production view and out of the readiness count.

**What you see in Langfuse:** one `marketing-agent` trace per run. Under the `agent`
root there is a `plan-next-step` generation per model call, with the tools it called as
siblings, a `verify-claims` generation under `check_claims`, and the
`check-publishing-policy` guardrail last. The root carries the marketer's task as input
and the agent's reply as output.

## Evaluation

| What | Command | Notes |
|---|---|---|
| `reviewer-decision`, `agent-steps`, `publishing-policy` scores | Written by every run | The reviewer is a rule-based stand-in (`agent/review.py`); `publishing-policy` sits on the guardrail observation |
| LLM judges `request-fulfilled` (root observation) and `retrieval-relevance` (retriever) | `.venv/bin/python scripts/seed_evaluators.py` | Stores `ANTHROPIC_API_KEY` in Langfuse as an LLM connection. The rules score new runs only; backfill older ones from the UI with **Evaluate**. Judge cost per run was not measured |
| Annotation queue and the `root-cause` score config | `.venv/bin/python scripts/seed_project.py all`, then `seed_project.py fill-queue` | The queue holds every rejected run |
| Dataset `regression-cases` | `.venv/bin/python scripts/seed_project.py all` | Seeded empty; the room adds a run to it in module 5 |

`seed_all.py` runs all of these. The workshop shows the judges and their blind spot
(relevance is not freshness); it has no experiments and no CI gate.

## The four planted faults

| Customer | Task you'll debug | Planted fault (in the data or tools, not the model) | Question it teaches |
|---|---|---|---|
| Contoso (agency software) | `pricing-check` | the Context Manager still serves an archived 2024 pricing FAQ, and passages carry no dates | did it retrieve the right info? |
| Fabrikam Running | `page-refresh` | uncached CMS (4–6 s per page), the harness runs parallel tool calls one by one, one page returns raw HTML (~45k tokens), one URL 404s | why is it slow and expensive? |
| Tailspin Travel | `weekly-report` | Copilot capture climbs to 99% and never finishes, and the instructions say "retry until you have it" | why does it keep going? |
| Tailspin Travel | `faq-page` | instructions v3 ("Speed up drafts") removed the fact-check rule | why did it skip a step? |

Each fault has a switch so you can show the fix live: `--fix exclude-archived`,
`--fix parallel-tools --fix clean-html`, `--fix terminal-pending`, or a prompt-label
change for the instructions cases.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | blank | Keys for the project |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` | Langfuse region or host |
| `LANGFUSE_PROJECT_NAME` | `marketing-agent-debugging` | The project the keys must map to; every script stops if they map to another |
| `LANGFUSE_PROJECT_ID` | blank | Used to build links; looked up from the keys when blank |
| `ANTHROPIC_API_KEY` | blank | The agent, its fact-checker, and the judges' LLM connection |
| `AGENT_MODEL` | `claude-opus-5-5` | Model for the agent loop |
| `FACTCHECK_MODEL` | the value of `AGENT_MODEL` | Model for the fact-checker's `verify-claims` call |
| `AGENT_EFFORT` | `medium` | Effort for the agent's model calls |
| `AGENT_MAX_STEPS` | `10` | Step limit; reaching it is a guardrail violation |
| `AGENT_FALLBACKS` | `default` | Server-side refusal fallback (Claude API beta); `off` disables it |
| `JUDGE_MODEL` | `claude-opus-5-5` | Model for the two judges; `claude-sonnet-5-5` is tried if it is rejected |
| `LANGFUSE_TRACING_ENVIRONMENT` | `production` | Set it in the shell, never in `.env`, to `development` for rehearsal runs |

## Layout

```
demos/marketing-agent-debugging/
├── demo.yaml               manifest: profile, coverage, commands (feeds the index)
├── README.md               this file
├── WORKSHOP_GUIDE.md       the facilitator's run of show
├── LIVE_SCRIPT.md          one page to present from
├── CHEAT_SHEET.md          participant handout
├── SPEC.md                 design record
├── ASSESSMENT.md           why this demo exists: what the other demos do and don't cover
├── .env.example            copy to .env
├── agent/
│   ├── loop.py             the agent loop and all Langfuse instrumentation
│   ├── tools.py            tool schemas, synthetic backends, fault switches
│   ├── world.py            the three customers: Context Manager docs, answer-engine snapshots, pages
│   ├── instructions.py     per-customer instructions and Tailspin's version history
│   ├── review.py           publishing-policy guardrail and simulated reviewer
│   ├── scenarios.py        the marketer tasks
│   └── config.py           .env loading, clients, the project check
├── webapp/                 presenter portal (FastAPI + one static page, port 8097)
├── scripts/
│   ├── seed_all.py         the one seed entry point; runs the seed_*.py scripts in order
│   ├── verify_demo.py      readiness check: READY or NOT READY, plus hero traces
│   ├── run_agent.py        one live run from the terminal
│   └── provision_cloud.py  create the Cloud project and write .env
├── tests/                  offline: no keys, no network, no model calls
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

## Tests

```bash
cd demos/marketing-agent-debugging
.venv/bin/python -m pytest -q        # about a second; no keys, no network, no model calls
```

They pin the planted faults, what each fix changes in the tools, the reviewer and
guardrail rules, the sequential and parallel tool paths, the trace message shape, and
`seed_all.py`'s order, its traffic decision and its failure handling.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Langfuse keys belong to project '…', expected 'marketing-agent-debugging'` | The keys in `.env` are for another project. Put this project's keys there, or set `LANGFUSE_PROJECT_NAME`; shell exports are ignored |
| `LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY missing` | No `.env` yet, or the keys are blank: copy `.env.example`, or run `scripts/provision_cloud.py` |
| `.env already has project keys — delete them to re-provision` | `provision_cloud.py` found nonempty public and secret keys. Keep them to reuse that project, or clear both values to provision again; blank template assignments are filled in place |
| `verify_demo.py`: `prompt marketing-agent/tailspin production = v4` fails | A rehearsal moved the label. Run `scripts/seed_prompts.py --reset-tailspin` |
| `verify_demo.py`: `N agent runs in the project` fails | The corpus is missing or has aged out of the 90-day retention: run `scripts/seed_all.py`. Runs made with `LANGFUSE_TRACING_ENVIRONMENT=development` are not counted |
| The Tracing table looks empty | The default time range is one day. Set it to the last 7 days |
| `metadata.customer:contoso` does nothing in the search bar | Turn on the Langfuse v4 preview in the UI |
| Halo or Ask AI is missing | An organization owner turns on AI features under Organization Settings |
| The judges did not score the seeded runs | Their rules only score runs made after they exist. Select the traces and choose **Evaluate**, or run new traffic |
| `ANTHROPIC_API_KEY missing from .env` | `seed_evaluators.py` stores the key as the judges' LLM connection. Add it to `.env` |

## Known limits

- **Real agent, synthetic world.** Claude makes every decision; the tools return
  deterministic synthetic data with simulated latency (`agent/world.py`, `agent/tools.py`).
  All brands, people and domains are fictional (`.example`).
- **The reviewer is simulated.** `reviewer-decision` comes from rules in
  `agent/review.py` that encode what each customer's marketer would object to. In
  production these are your users' approve and reject clicks.
- **Outcomes vary on purpose.** Opus usually notices the stale Contoso pricing and
  checks the live page; in 2 of 5 seeded runs it asked the marketer instead. The
  fact-checker is fooled on every run. Cases 3 and 4 reproduced in 3 of 3 seeded runs.
- **Cost.** A run costs $0.05–0.45 on Opus 5.5. The Fabrikam raw-HTML page is the
  expensive one, which is the point of Case 2. Set `AGENT_MODEL` to try another model.
- **Evaluation is shallow by design.** Two judges are seeded so the scores exist. The
  workshop does not cover designing evaluators, experiments or CI gates.
- **The seeders create, they mostly do not reconcile.** Score configs, the queue, the
  judges and prompt versions are skipped when something with that name exists, so a
  changed definition in the code is not pushed to an existing project. Only the
  dashboard widgets are updated in place.
- **`seed_all.py` has not been run against a live project yet.** It was added with the
  demo standard and is covered by offline tests; the scripts it calls are the ones the
  project was seeded with.
- **`LIVE_SCRIPT.md` links to the project it was rehearsed on.** For another project, use
  the links `verify_demo.py` prints.
- **The Anthropic SDK emits its own OpenTelemetry spans.** `agent/config.py` filters
  them out so model cost isn't counted twice. If you adapt this code, keep that filter.
