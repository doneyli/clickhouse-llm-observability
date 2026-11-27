# Grocery Assistant — a retail-grocery demo about trace shape and evaluator choice

A conversational shopping assistant for **Northwind Grocers**, a fictional
regional grocery chain, instrumented with Langfuse. TypeScript, Vercel AI SDK 7,
`@langfuse/*` v5. The catalog, offers, order history and shoppers are invented.

The demo ships the *same assistant* instrumented three ways: one with five real
defects, one with a sixth that looks fine, one correct. Same model, same tools,
same answers — only one of them is measurable.

**The question it answers:**

1. **[What does a good trace look like?](docs/GOOD_TRACE.md)** — and what do you do
   when yours does not.
2. **[Which evaluator should I build first?](docs/FIRST_EVALUATOR.md)** — the answer
   here is four deterministic checks before the first model call, and the reasoning
   generalises well beyond groceries.

> **Not grocery-specific.** The domain is a shopping cart, but every lesson here is
> about conversational agents with tools and multi-turn state. A support assistant,
> a booking agent, or a banking assistant hits the same six defects and benefits
> from the same evaluator order.

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Datasets · Experiment · Evaluate |
| **Stack** | TypeScript · Vercel AI SDK · Anthropic SDK |
| **Runs as** | Standalone: own toolchain and `.env` |
| **Langfuse** | Cloud or self-hosted · project `grocery-assistant` |
| **Length** | 25–30 min · short path 12 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (25 minutes, 30 with the optional Act 2; 12-minute short path)
- **Error analysis:** [docs/ERROR_ANALYSIS.md](docs/ERROR_ANALYSIS.md), the step before picking an evaluator, and [ERROR_ANALYSIS_RUNBOOK.md](ERROR_ANALYSIS_RUNBOOK.md) to present it (20 minutes, five stops)
- **Customer questions:** [docs/CUSTOMER_QUESTIONS.md](docs/CUSTOMER_QUESTIONS.md), the questions teams ask, with worked answers and code
- **Design record:** [SPEC.md](SPEC.md)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| A well-shaped trace for a conversational agent | `good` mode of `runTurn()` in `src/assistant.ts`; Act 1 |
| Five instrumentation defects, reproduced faithfully | `broken` mode; `npm run compare` counts each one from the API; Act 1 |
| Defect 6: the agent loop collapsed into one generation | `collapsed` mode; `npm run compare:loop`; Act 2 |
| Sessions and tags that make every run findable | `propagateAttributes` in `src/assistant.ts`; see [Telling the runs apart](#telling-the-runs-apart-in-the-langfuse-ui) |
| Deterministic evaluators on system state, before any judge | `src/evaluators/deterministic.ts`; printed per turn by `npm run chat`; Act 3 |
| A dataset derived from code fixtures, and a dataset run | `npm run seed:dataset`, `npm run experiment`; Act 3 |
| A session-level score written from your own code | `npm run evaluate:live`; Act 4 |
| A selector for a conversation-level judge | the `conversation-snapshot` observation, matched by metadata, not by name |

## Who it's for

An engineering team that already traces an assistant in Langfuse and is now asking
"is this set up right, and what do we measure?" It works for anyone building a
conversational agent with tools; the cart is incidental. Pick it over the bank or
real-estate demos in [the demo index](../README.md) when the question is
instrumentation quality or evaluator choice, rather than a whole evaluation loop.
Skip Act 2 for single-shot RAG or classification pipelines: there is no loop to collapse.

## What runs where

```
 you ── npm run chat | compare | experiment | evaluate:live   (Node 22+, tsx; short-lived processes)
          │  runTurn() in src/assistant.ts ── Anthropic API (AGENT_MODEL)
          │  one NodeSDK + LangfuseSpanProcessor per process, flushed before exit
          │  one LangfuseClient for datasets and scores
          ▼
 Langfuse project `grocery-assistant` (self-hosted on :3001, or Cloud)
   good / collapsed: trace `handle-chat-message`, one per turn, session per conversation run
   broken:           trace `chat: <first 60 characters>`, no root I/O, no session
   tags: grocery-assistant, shopping-assistant, compare:<mode>, conversation:<id>
```

There are no servers and no ports of the demo's own. Session ids: `cmp-good-<ms>`,
`cmp-broken-<ms>` and `cmp-collapsed-<ms>` for comparisons (one stamp per pair),
`<mode>-<conversation>-<ms>` for a standalone `npm run chat`, and
`exp-<run>-<conversation>` for the experiment. The broken run's session id goes to
metadata only, so no observation carries it — that is defect 5.

## Set up from scratch

Prerequisites: **Node 22+** (the `@langfuse/vercel-ai-sdk` integration requires it;
this was developed on 24), a Langfuse instance, and an Anthropic API key.

```bash
cd demos/grocery-assistant
npm ci
./scripts/provision-project.sh       # self-hosted: creates the 'grocery-assistant' project + keys, writes .env
cp .env.example .env                 # Langfuse Cloud instead: then fill in the project's keys and LANGFUSE_BASE_URL
# add ANTHROPIC_API_KEY to .env
npm run seed:dataset                 # idempotent: upserts the five conversation items, no model calls
npm run demo                         # comparison, experiment and session scores; makes model calls, takes a few minutes
npx tsx scripts/verify_demo.ts       # should print READY
```

`provision-project.sh` targets the **self-hosted** stack from this repo's root
(`docker compose --profile langfuse up -d`, Langfuse on `:3001`). It creates the
project directly in Postgres, mints an API keypair, verifies the keys over HTTP,
and writes `.env` if there is none. It is idempotent.

**Langfuse Cloud instead?** Create a project in the UI, copy `.env.example`, put its
keys, `LANGFUSE_BASE_URL=https://us.cloud.langfuse.com` and
`LANGFUSE_PROJECT_NAME=<your project>` in `.env`, and skip the provisioning script.
Everything else is identical.

The demo's `.env` is the only source of `LANGFUSE_*`: `src/env.ts` loads it with
`override: true` and refuses to run if the keys resolve to an unexpected project, and
`verify_demo.ts` drops shell-exported `LANGFUSE_*` before it reads the file. A stray
export would otherwise send every trace somewhere else, and the resulting 404s look
like application bugs. `./setup.sh` does not run this demo's seed.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `npm run demo`, plus `npm run compare:loop` if you run Act 2 | Fresh comparison sessions, a dataset run and session scores. Don't run `npm run demo` live |
| 30 minutes before | `npx tsx scripts/verify_demo.ts --links` | Prints READY and the link each act opens |
| 5 minutes before | Open Langfuse **Sessions** and **Tracing** for this project, and a terminal in `demos/grocery-assistant` | Act 1 starts on the Traces table filtered to tag `compare:broken` |

## Run it

```bash
npm run demo             # one-shot prep: verify, seed, compare traces, run the experiment, score sessions
npm run demo -- --quick  # skip the experiment (the dataset run's model spend)

npm run chat -- --list                                           # the available conversations
npm run chat -- --conversation dropped-dietary-constraint        # one conversation, good instrumentation
npm run chat:broken -- --conversation dropped-dietary-constraint # the same one, badly instrumented
npm run chat:collapsed                                           # defect 6 on its own: the agent loop as one generation
npm run compare                                                  # broken vs good, side by side, with the numbers
npm run compare:loop                                             # collapsed vs good — only the shape rows move
```

Each conversation is built so a specific failure is *possible* — the assistant may
or may not fall into it on a given run, which is the honest version of a demo. The
evaluators tell you which happened.

**What you see in Langfuse:** one `handle-chat-message` trace per good turn with the
turn's question and answer on the root, an `invoke_agent` tree with one generation per
model call and the tool spans between them, the turn's session and tags on every
observation, and a `conversation-snapshot` observation on the last turn. Broken turns
are named after the shopper's message, have blank Input and Output, empty
generations, and no session. `npm run compare` prints a table counting each
difference: distinct trace names, generations with null I/O, observations missing
`sessionId`, whether the root carries input and output.

## Evaluation

| What | Command | Notes |
|---|---|---|
| Six deterministic evaluators: `unverified-cart-claim`, `fabricated-purchase-history`, `dropped-dietary-constraint`, `stale-discount-quoted`, then `quoted_total_for_empty_cart` and `readback_request_unanswered` from error analysis | `npm run chat -- --conversation unverified-cart-claim` | Run on every turn and printed PASS / FAIL / n/a. The checks make no model calls; the conversation does. The order and the reasons are in [docs/FIRST_EVALUATOR.md](docs/FIRST_EVALUATOR.md) |
| Dataset `grocery-conversations` | `npm run seed:dataset` | Five items derived from `src/conversations.ts`, upserted on `grocery-<conversation id>` |
| Dataset run `deterministic-board-<timestamp>` | `npm run experiment` | Plays all five conversations (six to seven turns each) and writes one boolean score per applicable evaluator; not-applicable verdicts are not written. Makes model calls; cost not measured here |
| Session score `conversation-cart-integrity` | `npm run evaluate:live` | Reads recent sessions back and writes a NUMERIC score on each session (no trace id). No model calls |
| Evaluator backtest | `npx tsx scripts/backtest-evaluators.ts --tag ea:pass-1` | Replays two evaluators against human labels; see [docs/ERROR_ANALYSIS.md](docs/ERROR_ANALYSIS.md) |
| Judge `unavailability-obscured` | — | Written in `src/evaluators/judge.ts`, but no script calls it yet |

## Why a broken mode

The uncomfortable thing about instrumentation defects is that **the application
works**. The shopper gets a good answer, the cart is correct, latency is fine. The
only thing that is broken is your ability to tell — and you find that out later,
when you try to add an evaluator and discover there is nothing to score.
`npm run compare` makes that concrete. It counts rather than asserts, because that
is the habit worth building.

The six defects, all reproduced faithfully (details and fixes in
[docs/GOOD_TRACE.md](docs/GOOD_TRACE.md)):

| # | Defect | What it costs you |
|---|---|---|
| 1 | Generations with null input/output | No evaluator can read anything. Blocks everything else. |
| 2 | Trace named after the user's message | Nothing groups; no rule can target the endpoint |
| 3 | No input/output on the root observation | Blank Traces table; root-targeted judges see nothing |
| 4 | Conversation history restated on every root | The Sessions view becomes unreadable |
| 5 | `sessionId` on the root only, not propagated | Observation filters miss; cost never rolls up to the session |
| 6 | The agent loop collapsed into one generation | No per-step reasoning, no per-step cost — and the trace looks fine |

Defect 1 is reproduced with the AI SDK's real `recordInputs`/`recordOutputs: false`
switches — the way it usually happens: turned off early for PII, never turned back
on.

Defect 6 lives in its own mode (`collapsed`) rather than in `broken`, because it
and defect 1 cannot coexist in one trace: defect 1's lesson is a generation per
model call with every one of them empty, and defect 6 is the absence of per-call
generations altogether. `collapsed` is therefore instrumented **correctly in every
other respect**, which makes the loop shape the only variable — the defect that
passes code review.

### Telling the runs apart in the Langfuse UI

Every run is tagged, and **the tag is the reliable handle** — start there:

| Filter | Shows |
|---|---|
| tag `compare:good` | the correctly-instrumented comparison run |
| tag `compare:broken` | the five-defect run |
| tag `compare:collapsed` | the flattened-loop run |
| tag `grocery-assistant` | everything this demo has ever emitted |
| tag `conversation:<id>` | one scenario, e.g. `conversation:unverified-cart-claim` |

Each run also gets its own session id, printed at the end of the run and shown in
the Sessions view (see [What runs where](#what-runs-where)).

**Do not try to find the broken run by session.** It carries no `sessionId` at
all — that is defect 5 — so the Sessions view cannot see it and its session link
opens empty. That is the defect, not a broken link. Two more ways to spot broken
traces once you are in the Traces table:

- **the names give it away** — every broken trace is called `chat: <the shopper's
  first 60 characters>`, so the Name column is a list of unique strings, while
  good and collapsed traces are all called `handle-chat-message`
- **the Input and Output columns are blank** for broken traces, and populated for
  the other two

Good and collapsed traces are *deliberately* hard to tell apart from the table —
same name, same populated columns, session grouping working. You have to open a
turn that called tools and count the generations. That is the whole point of
defect 6, and why it needs its own comparison rather than a glance.

## Two details that will bite you in your own build

**`fields` must include `io`.** Reading observations back over the API,
`GET /api/public/v2/observations?...&fields=core,basic,io` — without `io`, `input`
and `output` come back undefined even when populated, which looks exactly like
missing data. And they arrive as **serialized JSON strings**, so `JSON.parse` them.

**AI SDK 7 changed how telemetry is enabled.** It is
`registerTelemetry(new LangfuseVercelAiSdkIntegration())` once at startup, and
telemetry is then on by default. The `experimental_telemetry: { isEnabled: true }`
per-call flag is the **v6** path and does nothing on 7 — which produces no LLM
spans at all and looks identical to a broken exporter.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | none; `src/env.ts` throws without them | Keys for the `grocery-assistant` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Langfuse host: the self-hosted stack, or `https://us.cloud.langfuse.com` |
| `LANGFUSE_PROJECT_NAME` | `grocery-assistant` | The project the keys must resolve to; every script refuses to write elsewhere |
| `ANTHROPIC_API_KEY` | blank | Model key for the assistant and the judge |
| `AGENT_MODEL` | `claude-sonnet-4-6` | The model that plays the assistant |
| `JUDGE_MODEL` | `claude-sonnet-4-6` | The model `src/evaluators/judge.ts` uses |
| `DEMO_LANGFUSE_PROJECT` | `LANGFUSE_PROJECT_NAME`, then `langfuse.project` in `demo.yaml` | Project name `verify_demo.ts` expects |
| `GROCERY_PROJECT_NAME`, `LANGFUSE_HOST`, `LANGFUSE_POSTGRES_CONTAINER`, `LANGFUSE_SALT` | `grocery-assistant`, `http://localhost:3001`, `langfuse-postgres`, the root compose `SALT` | Shell variables read by `provision-project.sh` only |

## Layout

```
demos/grocery-assistant/
├── demo.yaml                  manifest: profile, coverage, commands (feeds the index)
├── README.md                  this file
├── DEMO_SCRIPT.md             the talk track, act by act
├── ERROR_ANALYSIS_RUNBOOK.md  a 20-minute walkthrough of the error-analysis finding
├── SPEC.md                    design record
├── .env.example               copy to .env (or let provision-project.sh write it)
├── src/
│   ├── env.ts                 credentials, key isolation, project verification
│   ├── instrumentation.ts     NodeSDK + LangfuseSpanProcessor + registerTelemetry
│   ├── catalog.ts             the fictional catalog, order history, and offers
│   ├── tools.ts               6 tools; enum'd vocabularies, unsupported filters reported
│   ├── assistant.ts           runTurn(), instrumented three ways on purpose
│   ├── conversations.ts       5 multi-turn shopper fixtures, one per failure mode
│   ├── scoring.ts             verdicts -> Langfuse scores (observation / trace / session)
│   └── evaluators/
│       ├── deterministic.ts   the checks to build FIRST — no LLM in any of them
│       ├── deterministic.test.ts
│       └── judge.ts           the one that needs language understanding
├── scripts/
│   ├── provision-project.sh   dedicated self-hosted Langfuse project + keys, writes .env
│   ├── run-conversation.ts    drive one conversation, any mode
│   ├── compare-traces.ts      the headline: broken (or collapsed) vs good, counted from the API
│   ├── seed-dataset.ts        the dataset (the seed entry point)
│   ├── run-experiment.ts      dataset run + scores + pass rates with denominators
│   ├── score-live-sessions.ts session-level scores
│   ├── backtest-evaluators.ts replay evaluators against human labels
│   ├── run-demo.ts            one-shot prep
│   └── verify_demo.ts         readiness check: READY or NOT READY
├── tests/
│   └── verify_demo.test.ts    the readiness check against a fake Langfuse
└── docs/
    ├── GOOD_TRACE.md          lesson 1
    ├── FIRST_EVALUATOR.md     lesson 2
    ├── CUSTOMER_QUESTIONS.md  the FAQ, with code
    └── ERROR_ANALYSIS.md      reading traces into a failure taxonomy, and the results
```

## Tests

```bash
cd demos/grocery-assistant
npm ci
npm test                     # no services, no model calls, a few seconds
npm run typecheck            # tsc --noEmit
```

`src/evaluators/deterministic.test.ts` covers the deterministic evaluators, with cases
taken from real output. `tests/verify_demo.test.ts` covers the readiness check: results,
summary and exit code, the `.env` override, the manifest parse, run classification, and
full runs against a fake Langfuse on a v4 and a 3.x server. No test reaches a network.
The evaluator tests import `src/env.ts` through `src/assistant.ts`, and it throws at
import without keys, so they need `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set to
any value; CI supplies placeholders. The readiness-check tests need no keys.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Missing LANGFUSE_PUBLIC_KEY. Run ./scripts/provision-project.sh…` | No `.env`, or no keys in it. Provision, or copy `.env.example` and fill it in |
| `These keys resolve to [...], not 'grocery-assistant'` | The keys belong to another project. Use this project's keys, or set `LANGFUSE_PROJECT_NAME` |
| `Langfuse rejected these keys (HTTP 401)` | Wrong key pair, `LANGFUSE_BASE_URL` points at another host or region, or the stack is down |
| `Postgres container 'langfuse-postgres' is not running` | `provision-project.sh` needs the self-hosted stack: `docker compose --profile langfuse up -d` from the repo root |
| `Keys already exist for this project` from `provision-project.sh` | Secrets are hashed and cannot be read back. If `.env` has no working keys, run the `DELETE` it prints, then re-run |
| verify: `dataset grocery-conversations: HTTP 404` or missing items | `npm run seed:dataset` |
| verify: no `cmp-good-*` session or `compare:broken` run | Nothing ran in the last 7 days: `npm run demo` |
| verify: the latest turn has 0 observations | The trace landed before its observations. Wait 30 seconds and re-run |
| The broken run's session link opens empty | By design: it carries no `sessionId` (defect 5). Filter the Traces table by tag `compare:broken` |
| No LLM spans at all in your own build | AI SDK 7 needs `registerTelemetry(...)`; `experimental_telemetry` is the v6 path |
| An error mentioning Node or `@langfuse/vercel-ai-sdk` | The integration needs Node 22 or later |

## Known limits

- Every evaluator here is **reference-free**, so all of them can run on live
  traffic. Reference-based checks belong in the dataset experiment.
- The judge is deliberately singular. Four free checks first is the lesson. It is
  written (`src/evaluators/judge.ts`) but no script calls it, and the project has no
  managed conversation rule; the `conversation-snapshot` selector is there for one.
- Each conversation makes a failure reachable, not certain: a given run may not trip it.
- The time and model cost of `npm run demo` and `npm run experiment` are not measured
  in this folder.
- On a self-hosted 3.x server the read-back scripts use the v1 read endpoints, because
  Observations API v2 does not exist there; on Cloud and v4 they use v2.
- The catalog, the brand, the offers, the order history and the shoppers are all
  invented.
