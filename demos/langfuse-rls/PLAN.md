# Langfuse RLS Demo - Implementation Plan

## Context

**Why we're building this.** On 2026-05-13 Doneyli asked Langfuse engineering whether they natively support Client-Side Field Level Encryption (FLE) - a global bank and other financial institutions have flagged this as a deal blocker. Langfuse engineering confirmed FLE is not on the roadmap, but said the intended long-term answer is **attribute-based RBAC / Row-Level Security**: "user has rights to see data if condition is met on the observation/trace". No public design or timeline exists.

For the bank meeting tomorrow, Doneyli wants a working demo running against his local Langfuse instance at `localhost:3001` that:
1. Shows **what the proposed Langfuse RLS design would look like in practice**, end-to-end with personas.
2. Ships with a **detailed HTML design doc** suitable for sharing with a financial institution and Langfuse engineering.
3. Lives in `demos/` so it can be shown alongside other Langfuse demos.

**Important framing**: Langfuse does not have RLS today. This demo *simulates* the future feature by:
- Using existing trace metadata (`userId`, `tags`, `metadata.classification`, `metadata.team`) on ingest.
- Layering an **RLS policy engine in our app** that gates the Langfuse Public API response by persona attributes.
- The HTML doc must call this out explicitly so the bank and Langfuse Eng don't mistake it for a Langfuse-native feature.

## Goals / Non-Goals

**Goals**
- A Next.js demo at `demos/langfuse-rls/` with a persona switcher UI and a full design walkthrough at `/design`.
- Seeded against `localhost:3001` with ~30 demo traces carrying RLS-relevant metadata.
- Three bank-flavored personas: Alice (executive), Bob (compliance), Carol (analyst).
- HTML walkthrough doc covers: problem, current RBAC state, proposed model, sequence diagram, example policy, demo walkthrough, gap analysis, open questions.

**Non-Goals**
- Building this into a forkable Langfuse feature. Pure demo, no PR to langfuse/langfuse.
- Solving FLE / cryptographic decryption (that's a different feature; RLS is access control).
- Authentication. Persona switching is via dropdown - no login.

## Architecture

```
                     Browser (Next.js app)
                            |
   [Persona Switcher] -> selects current_user attributes
                            |
                            v
   GET /api/traces?persona=alice
                            |
         +------------------+------------------+
         |                                     |
         v                                     v
   Langfuse Public API                  RLS Policy Engine
   (localhost:3001/api/public/traces)   (lib/rls-policy.ts)
   - Basic Auth pk:sk                    - evaluates each trace
   - returns all traces in project       - subject.clearance vs trace.classification
                                         - subject.team vs trace.metadata.team
                                         - returns: visible[], denied_count
                            |
                            v
   UI renders visible traces + "N denied by policy" banner
```

**Key insight**: We query Langfuse with no filter, then apply the policy in our route handler. This makes the policy *visible* in code (good for the demo) and decouples us from any specific server version of the Langfuse filter param.

(Optional follow-up after the core works: also demonstrate server-side filtering via the Nov 2025 `filter` param to show "what Langfuse could do if RLS were native".)

## File Layout

```
demos/langfuse-rls/
  README.md                        # quickstart: seed -> run -> open
  package.json                     # next 15, react 19, tailwind, @langfuse/langfuse, zod
  next.config.mjs
  tsconfig.json
  tailwind.config.ts
  postcss.config.mjs
  .env.example                     # LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_PROJECT_NAME
  .gitignore                       # node_modules, .env, .next
  app/
    layout.tsx                     # shared chrome (nav: Demo | Design)
    page.tsx                       # persona switcher + trace list
    design/page.tsx                # full HTML walkthrough doc (rendered as MDX/TSX)
    api/
      traces/route.ts              # GET handler: fetch from Langfuse, apply RLS, return JSON
      personas/route.ts            # GET handler: list personas (small static set)
  lib/
    langfuse-client.ts             # thin fetch wrapper, Basic Auth
    rls-policy.ts                  # policy schema + evaluate(subject, trace) -> {allow, reason}
    personas.ts                    # 3 personas with attributes (clearance, team, name)
    policies.ts                    # the policies that ship with the demo (allow/deny rules)
    types.ts                       # TraceWithMetadata, Subject, Policy, EvaluationResult
  components/
    PersonaSwitcher.tsx
    TraceList.tsx
    TraceCard.tsx                  # shows classification badge, team, allowed/denied state
    DeniedBanner.tsx               # "N traces hidden by policy"
    PolicyExplainer.tsx            # hover/click on denied -> shows which rule fired
  scripts/
    seed-traces.ts                 # runs via `npx tsx scripts/seed-traces.ts`
    fixtures/
      traces.json                  # ~30 prepared trace payloads w/ varied metadata
  public/
    diagrams/                      # sequence diagram, policy model diagram (drawio + svg)
```

## Personas + Policy Model (referenced by both seeder and viewer)

**Personas** (`lib/personas.ts`):
```ts
[
  { id: "alice",   name: "Alice Chen",   team: "executive",  clearance: "ceo-only"   },
  { id: "bob",     name: "Bob Singh",    team: "compliance", clearance: "restricted" },
  { id: "carol",   name: "Carol Diaz",   team: "analyst",    clearance: "general"    },
]
```

**Trace metadata schema** (set by seeder, queried by viewer):
```ts
metadata: {
  classification: "ceo-only" | "restricted" | "general",
  team: "executive" | "compliance" | "analyst",
  topic: string,   // e.g. "Q3 earnings narrative", "AML review", "customer churn"
}
userId: <persona-id>   // who originally generated the trace
```

**Policy schema** (`lib/rls-policy.ts`):
```ts
type Policy = {
  id: string;
  effect: "allow" | "deny";
  match: {
    subject?: Partial<Record<"team" | "clearance", string | string[]>>;
    object?:  Partial<Record<"classification" | "team", string | string[]>>;
  };
  reason: string;   // human-readable explanation for UI
}
```

**Ship with these policies** (`lib/policies.ts`) - evaluated in order, deny wins ties:
1. `allow-own-team` - subject.team == object.team
2. `allow-clearance-ge-classification` - clearance(subject) >= classification(object), with ordering `ceo-only > restricted > general`
3. `deny-default` - implicit final deny

This is the simplest model that produces interesting matrix:
| Persona  | ceo-only / exec | restricted / compliance | general / analyst |
|----------|------------------|--------------------------|---------------------|
| Alice (exec, ceo-only)        | allow (clearance + team) | allow (clearance) | allow (clearance) |
| Bob (compliance, restricted)  | deny  | allow (clearance + team) | allow (clearance) |
| Carol (analyst, general)      | deny  | deny  | allow (clearance + team) |

## HTML Design Doc Outline (`app/design/page.tsx`)

Render as a single long page styled with Tailwind prose. Use Mermaid (`mermaid` npm package) for diagrams (renders client-side in `useEffect`).

1. **Why RLS** - Quote Doneyli's original Slack ask + Langfuse's reply. Link the bank use case.
2. **Current Langfuse RBAC** - Owner/Admin/Member/Viewer at org+project level. No row filtering. Cite https://langfuse.com/docs/administration/rbac.
3. **Proposed RLS model**
   - Subject attributes (user team/clearance/role - source: SSO claims, SCIM)
   - Object attributes (trace metadata.* and tags)
   - Policy = subject predicate + object predicate + allow/deny effect
   - Evaluation order: explicit deny > explicit allow > default deny
4. **End-to-end flow** - Mermaid sequence diagram: SDK ingest -> store -> query -> policy eval -> response.
5. **Example policy** - YAML version of the demo's `policies.ts` so the bank can see it as config.
6. **Demo walkthrough** - Screenshots/embeds of the `/` page with each persona's view. Show the explainer ("Why did Bob get denied? rule: deny-default fired because allow-own-team and allow-clearance-ge-classification both failed").
7. **Gap analysis vs Langfuse's proposed design**
   - What this demo does in user-space that Langfuse would do server-side natively.
   - What Langfuse needs to add: policy storage, policy editor UI, SSO claim mapping, audit log of policy decisions, performance (push-down to ClickHouse).
8. **Open questions for Langfuse Eng**
   - Will policies be per-org, per-project, or both?
   - How are SSO claims mapped to subject attributes?
   - Push-down to ClickHouse `WHERE` clauses or post-filter?
   - Caching of policy decisions for hot dashboards?
   - Behavior in CSV/API export contexts.

Add a banner at the top: **"DEMO ONLY. Langfuse does not natively support RLS as of 2026-05-19. This demo simulates the proposed behavior in our app layer."**

## Reused Patterns / Reference Files

### Next.js scaffolding
- Standard Next.js + Tailwind + `app/` router scaffolding (`package.json`, `tsconfig.json`, `tailwind.config.ts`, `next.config.mjs`, `app/layout.tsx`).

### Langfuse interaction patterns (TypeScript)
These mirror common Langfuse Python client patterns, translated to TypeScript.

- **Client construction**:
  ```ts
  // lib/langfuse-client.ts
  const host = process.env.LANGFUSE_BASE_URL ?? process.env.LANGFUSE_HOST ?? "http://localhost:3001";
  const pk = process.env.LANGFUSE_PUBLIC_KEY!;
  const sk = process.env.LANGFUSE_SECRET_KEY!;
  const auth = Buffer.from(`${pk}:${sk}`).toString("base64");
  // Use raw fetch with `Authorization: Basic ${auth}` for reads; SDK for writes.
  ```
- **Env var convention**:
  - Primary: `LANGFUSE_BASE_URL`
  - Fallback: `LANGFUSE_HOST` (older convention, both supported)
  - Keys: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
- **Pagination wrapper**: read `data` array + `meta.totalPages`, default `limit=50`, hard cap `MAX_PAGES=100` as a circuit breaker. Even though we only have ~30 traces, ship the safety rail.
- **TTL cache**: small in-memory `Map` keyed by `(persona,page)` with 60s TTL. Keeps the persona-switcher snappy without hammering Langfuse on every dropdown change.
- **Graceful Langfuse-down fallback**: wrap API calls; on error return `{ traces: [], error: "Langfuse unreachable" }` so the UI renders an empty state instead of 500ing.
- **Ingest SDK choice**: Use the official `langfuse` npm package in `scripts/seed-traces.ts`. Pin the JS major to match your Langfuse server version.
- **Don't copy** heavy OTel BSP tuning (`OTEL_BSP_MAX_QUEUE_SIZE` etc.) - that's for 150+ item runs; our 30 traces don't need it. Mention in README under "Scaling this up".

### Langfuse CLI (use for verification, not implementation)
Reference: the `langfuse-cli` (npm) for read-only verification.

- CLI is a **read-only convenience** for the implementer/SA, NOT runtime code in the demo.
- Useful invocations to bake into a `scripts/verify.sh`:
  ```bash
  # Sanity check after seeding (LANGFUSE_BASE_URL=http://localhost:3001 must be set):
  npx langfuse-cli api traces list --limit 50 --json | jq '.data | length'
  npx langfuse-cli api traces list --limit 50 --json | jq '.data[] | {id, name, userId, metadata}'
  ```
- Write operations are **not documented** in the CLI. Stick with the SDK for ingest.
- `--json` output pipes cleanly to `jq` for ad-hoc inspection.

### README structure
Match `demos/brand-promo-multi-agent/README.md`: "What it demos -> Who it's for -> Quick start -> Deep dive -> Troubleshooting". Add a CLI-based verify section per above.

## Critical Implementation Steps (sequenced)

1. **Scaffold the Next.js app** - standard `create-next-app` structure, set up Tailwind + TS.
2. **Define types and personas** - `lib/types.ts`, `lib/personas.ts`, `lib/policies.ts`. No Langfuse calls yet. Unit-testable.
3. **Implement the RLS policy engine** - `lib/rls-policy.ts` with `evaluate(subject, trace, policies)` returning `{ allow: boolean, matchedRule: string, reason: string }`. Write 6-10 vitest cases covering the matrix above.
4. **Implement Langfuse client wrapper** - `lib/langfuse-client.ts` exposes `listTraces(projectFilter?)`. Basic Auth via env vars. Handle pagination (Langfuse default page=1, limit=50).
5. **Seeder script** - `scripts/seed-traces.ts` reads `scripts/fixtures/traces.json` and POSTs each via the Langfuse SDK. Each fixture has `name`, `userId`, `metadata.classification`, `metadata.team`, `metadata.topic`, `tags`, `input`, `output`. Aim for 30 traces with a realistic spread across the 3x3 matrix.
6. **API routes** - `app/api/traces/route.ts` fetches all traces, evaluates each through the policy engine for the requested persona, returns `{ visible: Trace[], denied: { count, samples: [{traceId, reason}] } }`. `app/api/personas/route.ts` returns the static persona list.
7. **UI components** - `PersonaSwitcher` (dropdown), `TraceList` (shows visible), `DeniedBanner` ("12 traces hidden - hover to see why"), `TraceCard` with classification + team badges, `PolicyExplainer` modal.
8. **Design doc page** - `app/design/page.tsx`. Long-form Tailwind prose + Mermaid diagrams. Sections as outlined above. Top banner.
9. **README and .env.example** - quickstart: clone, `npm install`, `cp .env.example .env`, set keys from Langfuse UI, `npm run seed`, `npm run dev`, open localhost:3000 (note: avoid 3001 since Langfuse is there).
10. **Polish** - empty states, loading states, error states when Langfuse is unreachable.

## Configuration & Setup Notes for Implementer

- **Local Langfuse on `localhost:3001`** - Doneyli already has this running. The implementer must:
  - Tell Doneyli to create a project in the Langfuse UI named "RLS Demo" (or whatever - capture name in `.env`).
  - Tell Doneyli to mint a project API key pair in Project Settings -> API Keys.
  - Confirm the local Langfuse version is post-October 2025 (PR #9492) - the demo's filter approach doesn't depend on the server filter param, but the optional follow-up does. Note this in README troubleshooting.
- **Next.js dev port**: use 3000 (default). Document that 3001 is taken by Langfuse.
- **No login**: persona switching is a query param `?persona=alice`. Trivially bypassable - the doc must say so.
- **Mermaid**: install `mermaid` and render in a `<ClientOnly>` wrapper (Next.js SSR doesn't run mermaid).

## Verification Plan

End-to-end manual test (implementer should run these after build):

1. **Seed works (SDK path)** - `npm run seed` completes; check Langfuse UI at localhost:3001 -> RLS Demo project -> Traces. Expect ~30 traces with varied metadata.classification values.
2. **Seed works (CLI cross-check)** - From terminal with `LANGFUSE_BASE_URL=http://localhost:3001` and project keys exported:
   ```bash
   npx langfuse-cli api traces list --limit 50 --json | jq '.data | length'   # ~30
   npx langfuse-cli api traces list --limit 50 --json | jq '[.data[] | .metadata.classification] | group_by(.) | map({k: .[0], n: length})'
   ```
   Expect a roughly balanced spread across `ceo-only`, `restricted`, `general`.
3. **API returns matrix** - Demo API:
   - `curl http://localhost:3000/api/traces?persona=alice | jq '.visible | length'` should be ~30 (sees everything).
   - `curl http://localhost:3000/api/traces?persona=carol | jq '.visible | length'` should be ~10 (general only).
   - `curl http://localhost:3000/api/traces?persona=bob | jq '.visible | length'` should be ~20 (restricted + general).
   - Exact counts depend on fixture distribution but the ordering must hold: alice > bob > carol.
4. **UI parity** - Open localhost:3000, switch through all 3 personas, confirm trace counts match the matrix.
5. **Denied explainer** - Hover/click denied banner, see which rule fired for a sample.
6. **Design page renders** - Open `/design`, all 8 sections present, mermaid diagrams render, demo-only banner shown at top.
7. **Unit tests** - `npm test` runs the policy engine cases and passes.
8. **Langfuse-down behavior** - Stop the local Langfuse, refresh the demo - UI shows "Langfuse unreachable" empty state, no 500. Restart Langfuse, refresh - traces reappear.
9. **README quickstart** - A fresh clone + the README instructions reproduce the demo from zero in <5 minutes.

## Risks / Open Items

- **Langfuse version drift**: If local Langfuse is older than Oct 2025, server-side filter param may not work for the optional follow-up section. Mitigated by doing all filtering in our app layer.
- **API rate limits**: Local self-hosted shouldn't hit limits, but if implementer expands trace count past ~1000, paginate properly.
- **Demo banner**: The "DEMO ONLY" framing is critical - implementer must keep it prominent so the bank doesn't think Langfuse already ships RLS.
- **Mermaid SSR**: Common gotcha - render client-side only.

## Files an Implementer Will Touch (all new files)

All paths below are NEW. No existing files modified.

- `demos/langfuse-rls/README.md`
- `demos/langfuse-rls/package.json`
- `demos/langfuse-rls/next.config.mjs`
- `demos/langfuse-rls/tsconfig.json`
- `demos/langfuse-rls/tailwind.config.ts`
- `demos/langfuse-rls/postcss.config.mjs`
- `demos/langfuse-rls/.env.example`
- `demos/langfuse-rls/.gitignore`
- `demos/langfuse-rls/app/layout.tsx`
- `demos/langfuse-rls/app/page.tsx`
- `demos/langfuse-rls/app/design/page.tsx`
- `demos/langfuse-rls/app/api/traces/route.ts`
- `demos/langfuse-rls/app/api/personas/route.ts`
- `demos/langfuse-rls/lib/langfuse-client.ts`
- `demos/langfuse-rls/lib/rls-policy.ts`
- `demos/langfuse-rls/lib/personas.ts`
- `demos/langfuse-rls/lib/policies.ts`
- `demos/langfuse-rls/lib/types.ts`
- `demos/langfuse-rls/components/PersonaSwitcher.tsx`
- `demos/langfuse-rls/components/TraceList.tsx`
- `demos/langfuse-rls/components/TraceCard.tsx`
- `demos/langfuse-rls/components/DeniedBanner.tsx`
- `demos/langfuse-rls/components/PolicyExplainer.tsx`
- `demos/langfuse-rls/scripts/seed-traces.ts`
- `demos/langfuse-rls/scripts/fixtures/traces.json`
- `demos/langfuse-rls/public/diagrams/*.svg` (drawio originals optional)

## Reference Files

Standard Next.js + Tailwind scaffolding and common Langfuse client patterns:
client init, Basic Auth reads, a paginated list wrapper with a `MAX_PAGES`
circuit breaker, a short TTL cache, and a graceful "Langfuse unreachable"
fallback. `LANGFUSE_BASE_URL` is the primary env var, with `LANGFUSE_HOST` as a
fallback.
