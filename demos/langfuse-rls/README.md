> **DEMO ONLY.** Langfuse does not natively support Row-Level Security as of 2026-05-19. This demo simulates the proposed behavior entirely in the application layer. Do not present this as a Langfuse-native feature.

# Langfuse RLS Demo

A working prototype that shows what **attribute-based Row-Level Security** for Langfuse traces would look like in practice — built for financial-institution evaluations asking "who can see which traces?"

## What it demos

- **Three bank-flavored personas**: Alice (executive, CEO-level clearance), Bob (compliance, restricted clearance), Carol (analyst, general clearance)
- **A client-side RLS policy engine** that evaluates trace metadata against persona attributes and returns only the traces that persona is authorized to see
- **Live persona switching** — change personas and watch the trace list update with different counts and a "N traces hidden by policy" banner
- **Policy explainer modal** — drill into which rule fired to deny each hidden trace
- **A detailed design doc** at `/design` suitable for sharing with a financial institution and Langfuse engineering: problem statement, proposed RLS model, sequence diagram, gap analysis, and open questions for Langfuse Eng

Classification matrix this demo produces:

| Persona | ceo-only traces | restricted traces | general traces |
|---|---|---|---|
| Alice (exec, ceo-only) | Allowed | Allowed | Allowed |
| Bob (compliance, restricted) | Denied | Allowed | Allowed |
| Carol (analyst, general) | Denied | Denied | Allowed |

## Who it's for

- **A global bank** and any financial institution evaluating Langfuse for data residency and access control
- Any prospect asking whether Langfuse can enforce trace-level data segregation by user role or team
- Langfuse engineering stakeholders who want to see a concrete prototype of the RLS model Langfuse described

## Quick start

```bash
# 1. Clone and navigate
cd demos/langfuse-rls

# 2. Install dependencies
npm install

# 3. Configure environment
cp .env.example .env
# Edit .env: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
# from your local Langfuse project at http://localhost:3001

# 4. Seed traces into Langfuse
npm run seed

# 5. Start the demo app
npm run dev
```

Open http://localhost:3000. Langfuse runs on 3001 — do not use that port for this app.

## Deep dive

### Persona matrix

| Persona | User ID | Team | Clearance | Sees |
|---|---|---|---|---|
| Alice Chen | `alice` | executive | ceo-only | All 30 traces |
| Bob Singh | `bob` | compliance | restricted | ~20 traces (restricted + general) |
| Carol Diaz | `carol` | analyst | general | ~10 traces (general only) |

### Policy list (evaluated in order)

1. **allow-clearance-ge-classification** - subject's clearance rank >= trace's `metadata.classification` rank (`ceo-only > restricted > general`)
2. **deny-default** - no matching allow rule - default deny

Clearance is a hard ceiling: a subject sees a trace only if their clearance rank is >= the trace's classification rank. The `metadata.team` field is carried for display and audit only - it never grants access and cannot override the classification gate. (Earlier revisions of this demo included an `allow-own-team` rule that granted access on a team match alone; that allowed a general-clearance analyst to see restricted/ceo-only traces owned by their team, so it was removed.)

### Architecture note

The policy engine (`lib/rls-policy.ts`) runs **client-side in the Next.js API route** (`app/api/traces/route.ts`). The route fetches all traces from the Langfuse Public API with Basic Auth, then applies the policy in-process before returning a filtered JSON response to the browser.

This means Langfuse still sends all traces to our server - the filtering happens in our app, not in Langfuse's database. A native Langfuse RLS implementation would push the `WHERE` clause down to ClickHouse before any data leaves the database. The `/design` page explains this gap in detail.

### Trace classification distribution

The 30 seeded traces are distributed across a 3x3 matrix: classification (ceo-only / restricted / general) x team (executive / compliance / analyst), with bank-flavored topics: board strategy, M&A, earnings, AML reviews, sanctions screening, SAR (suspicious activity report) filings, credit risk models, churn analysis, and more.

## CLI verification

After seeding, cross-check with the Langfuse CLI:

```bash
export LANGFUSE_BASE_URL=http://localhost:3001
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...

bash scripts/verify.sh
```

The script reports trace count, classification distribution, and a sample of recent traces.

Manual API checks against the demo app:

```bash
# Alice should see ~30 traces
curl http://localhost:3000/api/traces?persona=alice | jq '.visible | length'

# Bob should see ~20 traces
curl http://localhost:3000/api/traces?persona=bob | jq '.visible | length'

# Carol should see ~10 traces
curl http://localhost:3000/api/traces?persona=carol | jq '.visible | length'

# Ordering must hold: alice >= bob >= carol
```

## Troubleshooting

**Langfuse not responding / "Langfuse unreachable" banner**
Make sure your local Langfuse is running: `docker compose up -d` in your Langfuse directory. It runs on 3001 by default. The demo app shows an empty-state error instead of crashing.

**Port 3000 already in use**
Kill the process with `lsof -ti:3000 | xargs kill -9` or run the demo on a different port: `PORT=3002 npm run dev`.

**Mermaid diagrams not rendering on the /design page**
Mermaid renders client-side only (Next.js SSR does not execute browser APIs). If diagrams appear blank on first load, wait a moment or hard-refresh. This is expected in development.

**Seed reports success but traces don't appear in Langfuse**
The Langfuse SDK batches trace sends and flushes asynchronously. The seed script calls `flushAsync()` before exiting. If traces still don't appear, check that your `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` belong to the correct project, and that your local Langfuse version is recent (post-October 2025 is recommended).

**Scaling this up**
The seed script is sized for 30 traces - no OTel BSP tuning required. If you expand to 1,000+ traces, consider setting `OTEL_BSP_MAX_QUEUE_SIZE` and `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` environment variables, or splitting the seed into batches. The Langfuse client wrapper in `lib/langfuse-client.ts` includes a `MAX_PAGES=100` circuit breaker for the pagination loop.
