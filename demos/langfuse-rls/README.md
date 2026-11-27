# Trace Governance (RLS) — Simulated attribute-based row-level security over Langfuse traces, with three personas seeing different slices of one project

> **DEMO ONLY.** Langfuse does not natively support Row-Level Security as of 2026-05-19. This
> demo simulates the proposed behaviour entirely in the application layer. Do not present it
> as a Langfuse-native feature.

A Next.js app that shows what attribute-based row-level security for Langfuse traces would
feel like. Three personas at a bank share one Langfuse project; a small policy engine in the
app's API route decides which of the project's 30 traces each persona may see, and says which
rule allowed or denied each one. The bank and the traces are fictional: 30 synthetic,
bank-flavoured prompts and responses (board strategy, M&A, AML reviews, churn analysis) from
`scripts/fixtures/traces.json`. No model is called.

**The question it answers:** "Who can see which of our LLM traces, and can one project hold
data at different clearances?"

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Governance |
| **Stack** | TypeScript · Next.js |
| **Runs as** | Standalone: own toolchain and `.env` |
| **Langfuse** | Self-hosted · project `RLS Demo` |
| **Length** | 8–12 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (8–12 minutes)
- **Design record:** [SPEC.md](SPEC.md); the in-app design doc at `/design` is the leave-behind

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Langfuse roles control operations, not row visibility | Langfuse itself (`:3001`): every project member sees all 30 traces |
| A persona-filtered view of one project | Demo tab (`/`): Alice sees 30, Bob 20, Carol 10 |
| Explainable decisions | Each visible card names its matched rule; the "N traces hidden by RLS policy" banner lists up to 5 hidden traces with the rule that denied them |
| A defensible policy model: clearance ceiling, default deny | Policies tab (`/policies`): the two rules, the clearance rank order and a live persona × classification matrix |
| The gap to native RLS, and the ClickHouse push-down path | Design tab (`/design`): sequence diagram, gap analysis, open questions, three horizons |

The access matrix the fixtures produce:

| Persona | User ID | Team | Clearance | `ceo-only` | `restricted` | `general` | Sees |
|---|---|---|---|---|---|---|---|
| Alice Chen | `alice` | executive | `ceo-only` | Allowed | Allowed | Allowed | 30 |
| Bob Singh | `bob` | compliance | `restricted` | Denied | Allowed | Allowed | 20 |
| Carol Diaz | `carol` | analyst | `general` | Denied | Denied | Allowed | 10 |

## Who it's for

Security, platform and data-governance leads at regulated companies (banks first) who ask
"who can see which traces?" during a Langfuse evaluation, and Langfuse engineering
stakeholders who want a concrete prototype of the model. It works best as an 8–12 minute rider
on a broader Langfuse demo, or as a discovery call with a regulated prospect. Pick it when
access control is the blocker; for tracing, evaluation or prompt management, pick a demo from
the index in [`demos/README.md`](../README.md).

## What runs where

```
 browser ── Next.js app :3000 (npm run dev)
              │  /api/traces?persona=<id>   app/api/traces/route.ts
              │    listTraces()             lib/langfuse-client.ts  (60 s in-memory cache)
              │    evaluateBatch()          lib/rls-policy.ts       (filter in-process)
              │                             lib/trace-read.ts       (shared by the app, seed and verify)
              ▼  GET /api/public/v2/observations, root rows, cursor-paged, Basic auth
                 (GET /api/public/traces only if v2 answers 404: a self-hosted v3 server)
 Langfuse :3001 (root compose `langfuse` profile)
   project `RLS Demo` (or the name in LANGFUSE_PROJECT_NAME)
   30 traces from npm run seed · tag `rls-demo` · userId alice | bob | carol
   each with one root observation `rls-demo-NN-root` carrying
     metadata {classification, team, topic} · input {query} · output {response}
   no scores, no sessions

 npm run seed ── scripts/seed-traces.ts
   the same read, filtered to tag rls-demo   what exists
   POST /api/public/ingestion                trace-create + span-create (the root), only for
                                             missing or drifted fixtures
```

Each fixture has a stable trace id (`rls-demo-01` … `rls-demo-30`), so re-seeding updates the
same 30 traces. v4 keeps no input, output or metadata at trace level, so the app rebuilds each
trace from its root observation; on a v3 server the legacy trace list carries them directly. The
app reads **every** trace in the project, not only the tagged ones, so keep
the project to these 30. A trace without `classification` and `team` metadata counts as public and is shown to
every persona.

## Set up from scratch

Prerequisites: Node 18+ and the root stack's Langfuse (`./setup.sh`, or
`docker compose --profile langfuse up -d` from the repo root), with a project for this demo and
its API keys.

```bash
cd demos/langfuse-rls
npm ci
cp .env.example .env         # fill in the keys; set LANGFUSE_PROJECT_NAME to the project's name
npm run seed                 # idempotent: created / updated / unchanged per fixture, a few seconds
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
npm run dev                  # app on http://localhost:3000
bash scripts/verify.sh       # in a second terminal; should print READY
```

`npm run seed` and `scripts/verify.sh` read only the demo's `.env`: they clear shell-exported
`LANGFUSE_*` first and stop before any request if the file or a key is missing. The seeder also
writes nothing unless the keys map to the expected project. `npm run dev` does not: Next.js lets shell exports win over `.env`, so unset them
before starting the app. `./setup.sh` does not run this demo's seed.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `npm run seed`, then `bash scripts/verify.sh` with the app running | The seed repairs any drifted fixture and writes nothing otherwise; READY means the 30 fixtures by id and 30 / 20 / 10 through the app |
| 30 minutes before | `unset LANGFUSE_...`, `npm run dev`, then `bash scripts/verify.sh --links` | Prints READY and the tab each act opens |
| 5 minutes before | Open the four links it printed | Langfuse traces (Opening), the Demo, Policies and Design tabs (Acts 1–3) |

No fresh traces are needed: the seeded data does not age out of the demo.

## Run it

```bash
npm run dev                                            # http://localhost:3000
curl -s 'localhost:3000/api/traces?persona=alice' | jq '.visible | length'   # 30
curl -s 'localhost:3000/api/traces?persona=bob'   | jq '.visible | length'   # 20
curl -s 'localhost:3000/api/traces?persona=carol' | jq '.visible | length'   # 10
```

Switch personas on the Demo tab and the stat strip and trace list change; the denied banner
expands to show which rule hid what. An unknown `persona` falls back to Alice.

**What you see in Langfuse:** the same 30 traces, with no filtering at all: names such as
`Q4 earnings narrative generation`, tag `rls-demo`, `userId` set to the persona that owns the
topic, and `metadata.classification` / `metadata.team` on each. That contrast is the Opening of
the script.

## The policy engine

`evaluate()` in `lib/rls-policy.ts` applies two rules from `lib/policies.ts`, in order:

1. **`allow-clearance-ge-classification`**: allow when the subject's clearance rank is at least
   the trace's `metadata.classification` rank (`ceo-only` 2 > `restricted` 1 > `general` 0).
2. **`deny-default`**: no allow rule matched, so deny.

Clearance is a hard ceiling. `metadata.team` is carried for display and audit only: it never
grants access. An earlier revision had an `allow-own-team` rule that let a general-clearance
analyst read `restricted` and `ceo-only` traces their team owned; it was removed.

The filter runs in the Next.js API route (`app/api/traces/route.ts`), after Langfuse has sent
every trace to the app server. Native RLS would push the predicate down into ClickHouse so
denied rows never leave the database; `/design` explains that gap.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Langfuse host for the app, the seeder and the check. `LANGFUSE_HOST` is the fallback |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | blank | Keys for this demo's project |
| `LANGFUSE_PROJECT_NAME` | `RLS Demo` | Project name `scripts/verify.sh` expects the keys to map to |
| `DEMO_LANGFUSE_PROJECT` | unset | Overrides `LANGFUSE_PROJECT_NAME` for the check; then `langfuse.project` in `demo.yaml` |
| `DEMO_APP_URL` | `http://localhost:3000` | Where `scripts/verify.sh` finds the running app |
| `PORT` | `3000` | Next.js port, read by `npm run dev`. Not 3001, which is Langfuse |

## Layout

```
demos/langfuse-rls/
├── demo.yaml                  manifest: profile, coverage, commands (feeds the index)
├── README.md                  this file
├── DEMO_SCRIPT.md             the talk track, act by act
├── SPEC.md                    design record
├── .env.example               copy to .env
├── app/
│   ├── page.tsx               Demo tab: persona switch, stat strip, trace cards, denied banner
│   ├── policies/page.tsx      Policies tab: rules, rank order, access matrix
│   ├── design/page.tsx        Design tab: the design doc and gap analysis (Mermaid, client-side)
│   └── api/                   traces/route.ts (fetch, then filter) and personas/route.ts
├── components/                persona switcher, trace list and card, denied banner, explainer
├── lib/
│   ├── rls-policy.ts          evaluate() and evaluateBatch(): the policy engine
│   ├── policies.ts            the two rules
│   ├── personas.ts            Alice, Bob, Carol
│   ├── langfuse-client.ts     the app's read, with a 60 s cache
│   ├── trace-read.ts          Observations v2 read and trace rebuild; legacy list only on a v2 404
│   └── types.ts
├── scripts/
│   ├── seed-traces.ts         npm run seed: idempotent upsert of the 30 fixtures; --prune-legacy
│   ├── fixtures/traces.json   the 30 synthetic traces, each with a stable id
│   ├── fixtures.ts            fixture validation and the drift comparison (seed and verify)
│   ├── demo_env.ts            the .env loader: clears shell LANGFUSE_*, requires the keys
│   ├── langfuse_http.ts       minimal JSON client for the public API
│   ├── verify.sh              npm run verify: readiness check, wraps verify_demo.ts
│   └── verify_demo.ts         the checks: READY or NOT READY, --links
└── tests/                     vitest; no services
```

## Tests

```bash
cd demos/langfuse-rls
npm ci
npm test                     # vitest, no services, no model calls, under a second
```

`tests/rls-policy.test.ts` covers the policy engine: the full persona × classification matrix,
that a team match never widens access, the no-metadata rule, `evaluateBatch()`, and that the
committed fixtures produce the 30 / 20 / 10 split. `tests/seed_traces.test.ts` covers the
seeder: missing or partial `.env` with hostile shell exports, the wrong project, two unchanged
reruns that write nothing, drift repair, a changed fixture, partial and total write failures, a
failed read, cursor paging, repairing traces the old seeder left without a root observation,
the v3 fallback, and old-seeder duplicates. `tests/verify_demo.test.ts` covers the readiness
check: verdicts and exit codes, the `.env` rule, and full runs on v4 and v3.
`tests/trace_read.test.ts` covers the shared read and the app's `listTraces()`: field groups,
truncated metadata, the 404-only fallback, and rebuilding traces from root rows. All run against
`tests/fake_langfuse.ts`, an in-memory Langfuse (v4 or v3) and app that reject unknown query
parameters, field groups and filter conditions, and return only the field groups asked for. `npm run typecheck` runs `tsc --noEmit`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Langfuse unreachable or no traces found." banner, empty list | Langfuse is down, or the app started with the wrong keys. Start the root stack's `langfuse` profile; unset shell `LANGFUSE_*` and restart `npm run dev` |
| `keys map to project '…' (want '…')` | The keys belong to another project, or `LANGFUSE_PROJECT_NAME` does not match the project's name |
| `project holds 60 trace(s) (want 30 …)`, persona counts 60 / 40 / 20 | Duplicates from the old appending seeder (random ids). `npm run seed` lists them as `legacy`; `npm run seed -- --prune-legacy` deletes just those |
| Persona counts lag a reseed or a prune | The app caches the trace list for 60 seconds. Wait, then refresh |
| `every fixture has its root observation … missing`, Bob and Carol see all 30 | The traces predate the root observations (the old seeder wrote trace-create only), so a v4 server has no metadata to filter on. `npm run seed` repairs them in place |
| `trace read failed: HTTP …` | Observations v2 failed for a reason other than 404 (a 404 switches to the legacy list). Check Langfuse is healthy: `../../setup.sh --status` |
| Port 3000 already in use | `lsof -ti:3000 \| xargs kill`, or `PORT=3002 npm run dev` and `DEMO_APP_URL=http://localhost:3002 bash scripts/verify.sh` |
| Mermaid diagrams blank on `/design` | Mermaid renders client-side only. Wait a moment or hard-refresh |
| Seed says done but no traces appear | Ingestion is asynchronous, and a v4 server can take up to 15 minutes to show batch-ingested events in Observations v2. Wait before re-running `verify.sh` or the seeder. If they never appear, check the worker is processing (`docker compose restart langfuse-worker` after a long uptime) |

## Known limits

- **Not a Langfuse feature.** Everything is simulated in the app. Langfuse sends every trace to
  the app server; anyone with the project's API keys, or a seat in the project, bypasses the
  policy entirely.
- **App-layer filtering only.** No predicate push-down into ClickHouse, so cost grows with the
  project's trace count; `lib/trace-read.ts` stops at 200 pages of 1,000 root rows.
- **No audit log** of allowed or denied reads, and exports, the public API and the Langfuse UI
  are not covered.
- **An unknown classification fails open.** A trace whose `metadata.classification` is not one
  of the three levels ranks below `general` and is shown to every persona, as is a trace with no
  classification or team. `tests/rls-policy.test.ts` pins this behaviour.
- **The denied banner lists at most 5** of the hidden traces (`app/api/traces/route.ts`), not
  all of them.
- **Self-hosted only.** The app reads Observations v2 on a v4 server and falls back to the legacy
  trace list only on a v3 server, but the seeder writes through the batch ingestion API
  (`POST /api/public/ingestion`), which Langfuse Cloud deprecates. The demo targets the root
  stack's self-hosted server; it is not set up for Cloud.
- **Seeded traces show up late on v4.** Batch-ingested events can take up to 15 minutes to reach
  Observations v2, so seed well before the demo.
- **One root observation per trace, nothing more.** No child observations, scores or sessions,
  and no model is called; the demo is about who sees rows, not about tracing.
