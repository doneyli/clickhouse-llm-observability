# Grocery Assistant — Spec

**Status:** built · **Written:** 2026-11-27, after the build (PRs #69, #75, #76, #77, #92), from
the code and the docs in `docs/`

This is the design record for a demo that predates the standard: what it claims, how each
claim is shown, and the decisions behind its shape. The long-form reasoning lives in
[docs/GOOD_TRACE.md](docs/GOOD_TRACE.md) and [docs/FIRST_EVALUATOR.md](docs/FIRST_EVALUATOR.md).

## 1. The question it answers

A team that already traces its assistant in Langfuse asks two things: *is our tracing set
up right?* and *which evaluator should we build first?* Both matter because every
evaluator, dataset run and judge reads the trace. An app can answer well and still be
impossible to measure, and teams usually find that out weeks later.

## 2. Audience and setting

Engineering teams putting a conversational agent with tools into production. 25 to 30
minutes in a first technical conversation; 12 minutes for the trace-shape act alone. Pick
it over the bank or real-estate demos when the question is instrumentation quality or
evaluator choice rather than a full evaluation loop.

## 3. What the demo must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | The same app, badly instrumented, answers as well and cannot be measured | Act 1: `npm run compare`, broken vs good, counted from the API | `verify_demo.ts`: a `cmp-good-*` session and `compare:broken` turns exist; broken generations have null input |
| 2 | A flattened agent loop passes review and loses per-step reasoning and cost | Act 2: `npm run compare:loop` | `verify_demo.ts` warns when no `cmp-collapsed-*` session exists |
| 3 | Deterministic checks on system state come before any judge | Act 3: `npm run chat -- --conversation unverified-cart-claim`, then the evaluator table | Unit tests in `src/evaluators/deterministic.test.ts`; `verify_demo.ts` requires a `deterministic-board-*` run covering all five items, with evaluator scores on its own items on both API paths (`--quick` makes it a warning) |
| 4 | A conversation-level number comes from your code, not a managed rule | Act 4: `npm run evaluate:live` | `verify_demo.ts` warns when the good session has no `conversation-cart-integrity` score |

## 4. The fictional company and data

Northwind Grocers, a fictional regional grocery chain. The catalog, offers, order history
and shoppers are invented in `src/catalog.ts` and `src/conversations.ts`. No real customer
is named anywhere in the folder.

## 5. Architecture

```
 npm scripts (Node 22+, tsx) ── runTurn() in src/assistant.ts ── Anthropic API (AGENT_MODEL)
        │  one NodeSDK + LangfuseSpanProcessor per process (src/instrumentation.ts), flushed before exit
        │  one LangfuseClient for datasets and scores
        ▼
 Langfuse project `grocery-assistant` (self-hosted :3001 or Cloud)
```

No servers and no ports of its own. Every script is a short-lived process.

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace (good, collapsed) | `handle-chat-message` | One per turn; root carries the turn's input and output |
| Trace (broken) | `chat: <first 60 characters of the message>` | One per turn; no root I/O, no session (defects 2, 3, 5) |
| Session | `cmp-good-<ms>`, `cmp-broken-<ms>` or `cmp-collapsed-<ms>`; `<mode>-<conversation>-<ms>`; `exp-<run>-<conversation>` | One per conversation run. The broken run's id is written to metadata only, so no observation carries it |
| Tags | `grocery-assistant`, `shopping-assistant`, `compare:<mode>`, `conversation:<id>`, `conversation_end` on the last turn | Propagated to every observation |
| Observation | `conversation-snapshot` | Last turn only; metadata `langfuse_eval_target = conversation-transcript` is the selector for a conversation rule |
| Dataset | `grocery-conversations` | Five items, upserted on `grocery-<conversation id>` by `npm run seed:dataset` |
| Dataset run | `deterministic-board-<timestamp>` | `npm run experiment`; one boolean score per applicable evaluator |
| Score | `conversation-cart-integrity` | NUMERIC, on a session (no trace id), by `npm run evaluate:live` |

## 7. Act plan

| Act | Minutes | What it shows | Depends on |
|---|---|---|---|
| Opening | 1 | The problem, no screen | — |
| 1 | 11 | Five defects, broken vs good | `npm run demo` (comparison runs), checked by `verify_demo.ts` |
| 2 (optional) | 5 | Defect 6, the collapsed loop | `npm run compare:loop` |
| 3 | 12 | Evaluator order | A live `npm run chat`; the dataset run from `npm run demo` |
| 4 (optional) | 5 | Session-level score | `npm run evaluate:live` |
| Close | 1 | Three takeaways | — |

The minutes are the pre-standard script's estimates (12 + 13 for the two core acts), split
to make room for the Opening and Close. They have not been re-measured in a rehearsal.

## 8. Measurements

The script quotes counts that `npm run compare` and `npm run compare:loop` print on the
day. The figures it names (7 traces holding 7 generations for 17 model calls; 17 empty
`step N` spans out of 67 observations) come from the default 7-turn conversation when the
collapsed mode landed (PR #75). Re-run the commands before quoting them. Error-analysis
numbers and their commands are in [docs/ERROR_ANALYSIS.md](docs/ERROR_ANALYSIS.md).

## 9. Risks and open questions

- Each conversation makes a failure reachable, not certain: a run may not trip it.
- The LLM judge (`src/evaluators/judge.ts`) is written but no script calls it, and the
  project has no managed conversation rule.
- Read-back code branches on the server version (src/readback.ts): Observations v2 on
  Cloud and v4, the v1 observation list on a self-hosted 3.x server (the repo's stack pins
  3.221.1). The legacy `/traces` endpoints are not used.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Language | TypeScript, Vercel AI SDK 7, `@langfuse/*` v5 | The audience builds on TypeScript, and the repo's other agent demos are Python |
| D2 | How to show bad tracing | Three modes of the same `runTurn()` | Same model, tools and answers make instrumentation the only variable |
| D3 | Defect 6 | Its own `collapsed` mode, correct in every other respect | Defect 1 needs a generation per call; defect 6 is their absence, so they cannot share a trace |
| D4 | Defect 1 | The AI SDK's real `recordInputs` / `recordOutputs: false` | It is how the defect usually happens: switched off for PII and never switched back on |
| D5 | Evaluator order | Deterministic checks on system state before one judge | "Grade the outcome in the environment, not the claim in the transcript" |
| D6 | Dataset source | Fixtures in `src/conversations.ts`; the dataset is derived and upserted on stable ids | Fixture changes show up in a diff; re-seeding never doubles the denominator |
| D7 | Not-applicable verdicts | Not written as scores | A pass rate over turns that never exercised the check is inflated and insensitive |
