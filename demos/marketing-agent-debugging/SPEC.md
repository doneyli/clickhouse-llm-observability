# Marketing Agent Debugging Workshop — Spec

**Status:** built · **Written:** 2026-11-27, after the build, from the code and the records it links

This workshop was built before the demo standard, so this spec was written afterwards as
the design record. The decision to build it is in [ASSESSMENT.md](ASSESSMENT.md): the
brief, ten requirements, and how far each existing demo in this repo covers them. The
numbers below come from the seeding and rehearsal of 2026-11-02 and 2026-11-03 as
recorded in [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md) and [LIVE_SCRIPT.md](LIVE_SCRIPT.md).
Nothing was re-measured when this page was written.

## 1. The question it answers

When a customer says the agent did something wrong, slow or strange, how does a product
manager find out why without waiting for an engineer? The workshop breaks that into four
questions a trace can answer: why did it do that, did it retrieve the right information,
why is it slow or expensive, and why did it keep going or skip a step.

## 2. Audience and setting

Product managers and deployment (forward-deployed) teams who run one agent for many
customers. Two hours, presenter-led: the presenter drives Langfuse on screen and the room
follows along, with no accounts and no code. It also works recorded. The neighbouring
choice is [bank-assistant](../bank-assistant/), the closest fit among the existing demos
and the fallback if this one cannot be used; [ASSESSMENT.md](ASSESSMENT.md) has the full
comparison.

## 3. What the workshop must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | Any customer's runs are findable from a complaint, not a trace id | Module 1: filters on customer, user and reviewer decision | Customer, task type and instructions version are on every observation (`propagate_attributes` in `agent/loop.py`); `verify_demo.py` needs 20 runs and 4 rejected ones |
| 2 | Each decision can be read: tool menu, tool calls, thinking, instructions version | Module 2: one `plan-next-step` generation | `tests/test_demo.py` pins the message shape Langfuse parses for tools, tool calls and thinking |
| 3 | Wrong context is visible as what the model saw against what the index knew | Case 1, Contoso `pricing-check` | Tests pin that the archived FAQ outranks the current price list, and that passages carry no status or date while the retriever metadata does |
| 4 | Slowness and cost are explained from the timeline | Case 2, Fabrikam `page-refresh` | Tests pin the raw-HTML page (over 50,000 characters, under 1,000 with `clean-html`) and the 404 as an ERROR node |
| 5 | A loop is explained by a tool contract plus an instruction | Case 3, Tailspin `weekly-report` | Tests pin the capture climbing to 99% and staying there, and the `terminal-pending` fix |
| 6 | An instruction regression can be found and rolled back by a PM | Case 4, Tailspin `faq-page` | Tests pin that v3 drops the fact-check rule; `verify_demo.py` needs `production` on Tailspin v3 |
| 7 | Every fix is verified with a live re-run | The ⚡ steps in module 3 | Each fault has a switch (`agent/tools.py` `FIXES`) or a prompt-label change |

## 4. The fictional company and data

Three fictional customers of a fictional AI-search marketing product: Contoso (agency
software), Fabrikam Running (running shoes) and Tailspin Travel (travel booking), each
with one or two marketers on `.example` domains. Their Context Manager documents,
answer-engine snapshots, pages and visibility numbers are synthetic and deterministic
(`agent/world.py`, `agent/tools.py`), with simulated latency. The model is real Claude,
so its decisions vary from run to run. The reviewer who approves or rejects each run is a
rule-based stand-in (`agent/review.py`).

## 5. Architecture

```
marketer task ──► run_agent()  (agent/loop.py: a Claude tool-use loop, at most 10 steps)
   from the CLI (scripts/run_agent.py), the seeder (scripts/seed_traffic.py)
   or the presenter portal (webapp/server.py, FastAPI, 127.0.0.1:8097)
        │   seven in-process tools over the synthetic world; check_claims makes its own model call
        ▼   one Langfuse client per process; the Anthropic SDK's own spans are filtered out
Langfuse Cloud project `marketing-agent-debugging`
```

Nothing else runs: no Docker, no database, no second service.

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace | `marketing-agent` | One per run; the `agent` root carries the task as input and the reply as output. Environment `production`; rehearsals set `development` |
| Session | `<customer>-chat-<8 hex>` | Multi-turn chats and every portal run; a single CLI run has none |
| Observations | `plan-next-step`, the seven tools, `verify-claims`, `check-publishing-policy` | A `generation` per model call, `tool` or `retriever` per tool call (WARNING or ERROR on failure), one `guardrail` per run |
| Tags and metadata | `customer:`, `task:`, `surface:` tags; `customer`, `task_type`, `instructions`, `fixes` metadata | Set once per run, so every observation carries them |
| Prompts | `marketing-agent/contoso`, `/fabrikam`, `/tailspin` | Served by label `production`. Tailspin has three versions; v3 is the regression and must hold `production` before a session |
| Scores | `reviewer-decision`, `agent-steps` on the trace; `publishing-policy` on the guardrail observation | Written by `run_agent()` after each run |
| Evaluators | `request-fulfilled`, `retrieval-relevance` | Managed LLM judges; they score runs made after their rules exist |
| Score configs | `reviewer-decision`, `root-cause`, `publishing-policy` | `root-cause` is what the room labels in module 5 |
| Annotation queue | `PM review — rejected agent runs` | Filled from rejected runs |
| Dataset | `regression-cases` | Seeded empty; the room adds a run to it in module 5 |
| Dashboard | `Agent health — deployment view` | Ten widgets over observations and scores |

## 7. Module plan

[WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md) is the run of show. With a 5-minute break and 10
minutes of questions the modules add up to 120 minutes.

| Module | Minutes | What it shows | Depends on |
|---|---|---|---|
| 0 Frame | 5 | The four questions | — |
| 1 Find your way around | 15 | Filters, saved views, sessions | Seeded traffic with rejected runs and one multi-turn session |
| 2 Read one agent run | 20 | Tree, graph, one generation, timeline, log view | An approved Contoso `pricing-check` run |
| 3 Case files | 40 | The four planted faults, each with a fix | The hero traces `verify_demo.py` lists; `production` on Tailspin v3 |
| 4 From one trace to a pattern | 15 | Dashboard, observation filters, the two judges | The dashboard and both evaluators |
| 5 Close the loop without engineers | 10 | Comments, the annotation queue, the dataset, prompt labels | The queue, the `root-cause` config, the dataset |

## 8. Measurements

From the 2026-11-02 seeding (`scripts/seed_traffic.py`) and the rehearsal that followed
(`scripts/run_agent.py` with the fix switches), on Claude Opus 5.5:

- The full seed is about 26 agent runs, about 5 minutes with 4 workers, and about $6-8.
- One run costs $0.05 to $0.45 and takes 15 to 90 seconds.
- Case 1: the agent asked the marketer instead of resolving the conflict in 2 of 5 seeded
  runs. Cases 3 and 4 reproduced in 3 of 3.
- Case 2 with `parallel-tools` and `clean-html`: about $0.42 down to $0.15, and the
  largest single-step cache write from about 45,000 tokens to about 2,000.
- Case 3 with the instructions edited to "retry at most twice": 9 steps down to 4.

## 9. Risks and open questions

- Outcomes vary by design. A live run can go differently from the seeded ones; the guide
  tells the presenter to debug that run instead.
- The Cloud project is created with 90-day retention (`scripts/provision_cloud.py`), so
  the seeded corpus ages out and has to be seeded again.
- The filters in the guide need the Langfuse v4 preview UI, and Halo and Ask AI need an
  organization owner to turn on AI features.
- `scripts/seed_all.py` was added with the demo standard and is covered by offline tests
  only; it has not yet been run against a live project.
- The module minutes are the presenter's plan; a timed run with a real room has not been
  recorded.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Reuse an existing demo or build one | Build | No existing demo covers a marketing agent, customer as a filter, a switchable latency fault and symptom-first case files together ([ASSESSMENT.md](ASSESSMENT.md)) |
| D2 | Where the faults live | In the data, the tools and the instructions, never in the model or the task text | The agent reacts to them for real, so the same task can go right on one run and wrong on the next |
| D3 | Reviewer feedback | A rule-based stand-in | Every seeded run needs feedback that is realistic and reproducible |
| D4 | Deployment target | Langfuse Cloud, Python only | The room follows along without accounts; nothing local to break |
| D5 | Generation input shape | OpenAI chat shape with `tools` and `messages` | It is what Langfuse parses for the tool menu, tool-call cards and thinking |
| D6 | The Anthropic SDK's own spans | Filtered out in `agent/config.py` | Each model call would otherwise be recorded, and its cost counted, twice |
| D7 | Evaluation depth | Two managed judges, seeded so the scores exist | Designing evaluators, experiments and CI gates is the follow-up session |
| D8 | Paid seeding | `seed_all.py` runs the agent traffic only when the project has fewer than 20 runs | Every other seed step is free and safe to repeat; traffic costs money and adds runs each time |
