# Trace Governance (RLS) — Spec

**Status:** built (July 2026); written up as the design record on 2026-11-27 ·
**Demo:** `demos/langfuse-rls`

The demo was built before the demo standard. This file records what it proves and why it is
built this way, from the code as it stands. The long-form design document for customers and
Langfuse engineering lives in the app itself, at `/design` (`app/design/page.tsx`).

## 1. The question it answers

"Who can see which of our LLM traces, and can one Langfuse project hold data at different
clearances?" LLM traces carry customer data, unreleased strategy and executives' verbatim
questions. Langfuse's project roles control what a member can *do*; every member of a project
sees every trace in it. For a bank that wants executive, compliance and analyst teams on one
observability platform, that is a blocker. The demo gives the requirement a concrete,
clickable shape.

## 2. Audience and setting

Security, platform and governance leads at a regulated company, usually a bank, during a
Langfuse evaluation; and Langfuse engineering, as a prototype of the model. 8–12 minutes, as a
rider on a broader Langfuse demo or as its own discovery call. The neighbouring demos cover
tracing, evaluation and prompt management; none covers access control.

## 3. What the demo must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | Native roles do not limit row visibility | Opening: Langfuse's own trace list shows all 30 | `verify.sh`: the project holds exactly 30 traces |
| 2 | One project can serve three clearances | Act 1: the persona switch, 30 → 20 → 10 | `verify.sh`: `/api/traces?persona=` returns 30 / 20 / 10 of 30 |
| 3 | Each decision is explainable | Act 1: matched rule on each card, the denied banner | Visual; the route returns `matchedRule` and `reason` per trace |
| 4 | The model is defensible: clearance ceiling, default deny, team never widens | Act 2: `/policies` | `tests/rls-policy.test.ts`: the 3 × 3 matrix and the team cases |
| 5 | The mock is not the answer; native push-down is | Act 3: `/design` gap analysis and horizons | Said by the presenter; no check |

## 4. The fictional company and data

A fictional bank; the traces name it in their text. 30 synthetic traces in
`scripts/fixtures/traces.json`: a 3 × 3 grid of classification (`ceo-only`, `restricted`,
`general`) by owning team (`executive`, `compliance`, `analyst`), 10 per classification, with
bank topics such as earnings narratives, M&A, AML reviews, SAR filings, sanctions screening,
credit risk and churn. Inputs and outputs are pre-written; no model is called.

## 5. Architecture

```
 browser ── Next.js :3000
              app/api/traces/route.ts
                listTraces()      lib/langfuse-client.ts   60 s cache
                readTraces()      lib/trace-read.ts        GET /api/public/v2/observations, root rows,
                                                           cursor-paged; legacy GET /api/public/traces
                                                           only when v2 answers 404 (v3 server)
                evaluateBatch()   lib/rls-policy.ts        evaluate() per trace, in-process
              returns {visible[], denied{count, samples[≤5]}, persona}
 Langfuse :3001 (self-hosted: 4.79.0 on the live stack; 3.221.1 where the committed compose pins it)
```

One process. The seeder (`scripts/seed-traces.ts`) is a separate one-shot run: it reads the
project's `rls-demo` traces through the same `readTraces()`, then sends a `trace-create` and a
root `span-create` only for fixtures that are missing or have drifted.

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace | 30 fixture names, such as `Q4 earnings narrative generation` | One per fixture, under a stable id `rls-demo-01` … `rls-demo-30`; re-seeding upserts, never appends |
| Tag | `rls-demo` | On every seeded trace |
| `userId` | `alice`, `bob`, `carol` | The persona who owns the topic; 10 each |
| Metadata | `classification`, `team`, `topic` | Read by the policy engine; `team` is display and audit only |
| Root observation | `rls-demo-NN-root`, a span named like the trace | One per trace, at the trace's timestamp; carries `input`, `output` and the RLS `metadata`, which v4 keeps only on observations |
| Child observations, scores, sessions | none | The demo is about row visibility, not tracing |

## 7. Act plan

| Act | Minutes | What it shows | Depends on |
|---|---|---|---|
| Opening | 2 | The gap: roles ≠ row visibility | The 30 traces in Langfuse |
| 1 | 3 | Persona switch and explainable denials | The app up; 30 / 20 / 10 (`verify.sh`) |
| 2 | 2 | The policy model on `/policies` | Nothing beyond the app |
| 3 | 3–4 | Architecture, gaps and the ClickHouse path on `/design` | Nothing beyond the app |
| Close | 1 | Three takeaways | — |

## 8. Measurements

None. The persona counts are deterministic consequences of the fixtures and the policy, pinned
by `npm test` and checked live by `bash scripts/verify.sh`. The run length (8–12 minutes)
predates the standard and has not been re-measured.

## 9. Risks and open questions

- The seeder writes through the batch ingestion API, which Langfuse Cloud deprecates, and a v4
  server can take up to 15 minutes to show those events in Observations v2. Moving the seeder to
  OTLP with the `x-langfuse-ingestion-version: 4` header would make them real-time, but OTel trace
  ids are 32 hex characters, so the stable `rls-demo-NN` ids would have to change.
- The legacy trace-list fallback (D8) exists only for a v3 server and goes when the repo stops
  pinning 3.221.1.
- Projects seeded before the stable ids hold copies under random ids. `npm run seed` reports them;
  `--prune-legacy` deletes only those, after the operator asks for it.
- An unknown classification ranks below `general` and fails open; a production policy would
  deny it.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Where enforcement runs | In the Next.js API route, after a full fetch | No forked Langfuse and no direct ClickHouse access: the prototype changes nothing about the stack it critiques. The gap is the point of Act 3 |
| D2 | Policy model | Clearance rank ceiling plus default deny | Easy to review and argue about; maps to attribute-based access control |
| D3 | Team-based allow | Removed | `allow-own-team` let a general-clearance analyst read `ceo-only` traces their team owned |
| D4 | Traces without RLS metadata | Public | Keeps unrelated traces visible; recorded as a limit, since it fails open |
| D5 | Readiness check | `scripts/verify.sh` wraps `scripts/verify_demo.ts`; the shared `readTraces()` for the Langfuse count and the root-observation check, the app's own route for the persona counts | One read path for app, seeder and check, so the check cannot pass on a read the app does not make. Reading through the app checks the path the audience sees |
| D6 | Seeding | Stable fixture ids; read, compare, write only what is missing or drifted; raw ingestion API; a trace plus a root observation `rls-demo-NN-root` per fixture | An unchanged run writes nothing. The 207 response names each failed event, which the legacy SDK's queue swallowed. A trace update keeps its timestamp so the v3 table does not keep a second row. v4 has no trace-level input, output or metadata, so the root observation carries them; a trace without it is drift and is repaired in place |
| D7 | Old-seeder duplicates | Reported by default, deleted only with `--prune-legacy`, and only tagged traces that copy a fixture's name under another id | Never wipe a project or delete what the seeder does not own |
| D8 | Which read API | Observations v2 (`fields=core,basic,io,metadata,trace_context`, `isRootObservation`, tag and time bounds in `filter`, `meta.cursor` followed through empty pages, bounded, repeated cursor is an error), grouped by `traceId`; the legacy `GET /api/public/traces` only when v2 answers the first request with 404 | v2 is the current read on v4 and Cloud, and the live stack runs 4.79.0. The repo's committed compose still pins 3.221.1, where v2 404s, so that server keeps working; any other v2 failure is an error, never a silent fallback |
