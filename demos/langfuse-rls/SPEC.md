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
                listTraces()      lib/langfuse-client.ts   GET /api/public/traces, paginated, 60 s cache
                evaluateBatch()   lib/rls-policy.ts        evaluate() per trace, in-process
              returns {visible[], denied{count, samples[≤5]}, persona}
 Langfuse :3001 (self-hosted v3)
```

One process. The seeder (`scripts/seed-traces.ts`) is a separate one-shot run with one
Langfuse client, flushed before exit.

## 6. What lands in Langfuse

| Object | Name | Rule |
|---|---|---|
| Trace | 30 fixture names, such as `Q4 earnings narrative generation` | One per fixture, per `npm run seed`. New ids every run, so seeding twice gives 60 |
| Tag | `rls-demo` | On every seeded trace |
| `userId` | `alice`, `bob`, `carol` | The persona who owns the topic; 10 each |
| Metadata | `classification`, `team`, `topic` | Read by the policy engine; `team` is display and audit only |
| Observations, scores, sessions | none | The demo is about row visibility, not tracing |

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

- The app reads `GET /api/public/traces`, which Langfuse Cloud removed on 2026-11-16 and which
  a v4 server no longer serves. The demo runs only against the self-hosted v3 server until the
  read moves to Observations v2 and the seeder to OTLP with the v5 `@langfuse/*` SDK
  (`docs/LANGFUSE_V4_MIGRATION_SPEC.md` §15).
- The seeder appends. A rehearsal that reseeds doubles every count until the project is wiped.
- An unknown classification ranks below `general` and fails open; a production policy would
  deny it.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Where enforcement runs | In the Next.js API route, after a full fetch | No forked Langfuse and no direct ClickHouse access: the prototype changes nothing about the stack it critiques. The gap is the point of Act 3 |
| D2 | Policy model | Clearance rank ceiling plus default deny | Easy to review and argue about; maps to attribute-based access control |
| D3 | Team-based allow | Removed | `allow-own-team` let a general-clearance analyst read `ceo-only` traces their team owned |
| D4 | Traces without RLS metadata | Public | Keeps unrelated traces visible; recorded as a limit, since it fails open |
| D5 | Readiness check | `scripts/verify.sh` wraps `scripts/verify_demo.ts`; Metrics v1 for the Langfuse count, the app's own route for the persona counts | The seeded traces have no observations, so Observations v2 has nothing to count, and Observations v2 and Metrics v2 404 on the v3 server. Reading through the app checks the path the audience sees |
