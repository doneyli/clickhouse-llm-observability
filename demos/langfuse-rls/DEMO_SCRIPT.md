# Trace Governance (RLS) — Demo Script (simulated, ~10 min)

> **DEMO ONLY — say this out loud, first.** Langfuse does **not** natively support
> Row-Level Security on traces today. This app **simulates the proposed behavior
> in the application layer** so a customer can *interact* with what trace-level
> access control would feel like — and so we can advocate for it with concrete
> requirements. Never present it as a Langfuse-native feature; with a
> sophisticated audience that costs you the room.

A **governance conversation with a working mock**, not a product walkthrough:
three personas, one shared Langfuse project, and each persona sees a different
filtered subset of the same 30 bank-flavored traces.

- **App:** `http://localhost:3000` (Next.js, standalone — own `npm` + `.env`)
- **Data:** 30 seeded traces in your local Langfuse (`http://localhost:3001`),
  fetched via the public API and filtered by an in-app policy engine
- **Personas:** Alice (executive, `ceo-only`) sees **30/30**, Bob (compliance,
  `restricted`) sees **20/30**, Carol (analyst, `general`) sees **10/30**
- **Run length:** 8–12 min — best as a **rider** on a broader Langfuse demo, or a
  standalone discovery call with a regulated prospect

> This is deliberately smaller than the other demo scripts in this repo: there is
> one strong interaction (the persona switch) and a lot of strong *conversation*
> (the gap analysis on the `/design` page). Let the conversation carry it.

---

## How to run this script

Same conversational shape as the other scripts — each act **frames** a problem,
**shows** the answer, **lands** the benefit, and hands an **ask** back to the
room. Here the Ask beats matter even more than usual: this demo exists because a
customer requirement has no native answer yet, so what you're really doing is
qualifying *their* requirement precisely enough to feed the roadmap conversation.

---

## 0 · Pre-flight (do this BEFORE the meeting)

Requires the Langfuse stack up on `:3001` and Node 18+.

```bash
cd demos/langfuse-rls
npm install                      # one-time
cp .env.example .env             # set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
npm run seed                     # push the 30 fixture traces into Langfuse
npm run dev                      # app on http://localhost:3000
```

Sanity-check the money numbers before the meeting — they're deterministic:

```bash
curl -s 'localhost:3000/api/traces?persona=alice' | jq '.visible | length'   # 30
curl -s 'localhost:3000/api/traces?persona=bob'   | jq '.visible | length'   # 20
curl -s 'localhost:3000/api/traces?persona=carol' | jq '.visible | length'   # 10
```

**Browser tabs ready:** the app (`:3000`) on the Demo tab, plus `/policies` and
`/design` in adjacent tabs. Langfuse itself (`:3001`) in a fourth tab — you'll
want it to show that *natively*, every project member sees everything.

---

## What each act proves

| Point | Where |
|---|---|
| Langfuse RBAC controls **operations**, not **row visibility** | Opening — show any member sees all traces in Langfuse itself |
| What trace-level RLS would feel like (persona → filtered view) | Act 1 — persona switch, 30 → 20 → 10 |
| Policy is explainable: which rule allowed/denied each trace | Act 1 — the "hidden by RLS policy" banner, expanded |
| A defensible policy model (clearance ceiling, default-deny) | Act 2 — `/policies` matrix + the removed team-override rule |
| Honest gap analysis + the native path (ClickHouse push-down) | Act 3 — `/design` |

---

## Opening · Locate the pain (2 min, no screen yet)

**Frame.** LLM traces are some of the most sensitive data a company produces:
prompts carry customer PII, unreleased strategy, an exec's verbatim questions.
Langfuse's roles (Owner/Admin/Member/Viewer) control what you can *do* — they
don't control which *rows* you can see. **Every member of a project sees every
trace in it.** For a bank putting exec-strategy, compliance, and analyst teams on
one observability platform, that's not a nice-to-have gap; it's a deal blocker.
This prototype came out of exactly that conversation.

**Ask (these steer the session):**
- "Who can see your LLM traces today — and is there anything in them that not
  everyone should see?"
- "How do you segregate sensitive data in your other platforms — per-team
  workspaces, row-level policies, something else?"
- "If you had to put three teams with different clearances on one trace store
  tomorrow, what would you do?"

**Land.** "Today the honest answer in Langfuse is *separate projects per
sensitivity tier* — which works, but fragments your cross-team analytics and
multiplies ops. Let me show you what we think the real answer should look like,
as a working mock we built to pin the requirement down."

---

## Act 1 · One project, three clearances (3 min)

**Frame.** Same data, different eyes. The requirement in one sentence: an
analyst and an executive share the platform, but the analyst must never see the
M&A traces.

**Show.** Open the **Demo** tab (`:3000`). Three persona cards — Alice
(executive / `ceo-only`), Bob (compliance / `restricted`), Carol (analyst /
`general`). Click through them and watch the stat strip:

- **Alice → 30/30.** Full visibility: board strategy, M&A, sanctions screening,
  the lot.
- **Bob → 20/30.** The `ceo-only` traces are gone; a red **"10 traces hidden by
  RLS policy"** banner appears. **Expand it** — each hidden trace shows *which
  rule* denied it.
- **Carol → 10/30.** Only `general` traffic — churn analysis, credit-risk
  summaries.

Point at a visible trace card: classification and team badges, the actual
prompt/response, and the **matched rule name** on the card.

**Land.** "One project, one dataset, three views — and every allow/deny is
*explainable*: the trace tells you which policy matched. That's the shape of the
requirement. What you just saw is the behavior a native implementation should
produce; here it's simulated so you can react to it."

**Ask.** "Map your teams onto these three personas — who's your Alice, your Bob,
your Carol? And is clearance the right axis for you, or is it team, region,
customer, something else?"

---

## Act 2 · The policy model (2 min)

**Frame.** Access control demos are easy to fake with an `if` statement. What
makes this useful is that the policy model underneath is one you could defend to
a security review.

**Show.** The **Policies** tab: two rules —
`allow-clearance-ge-classification` and `deny-default` — a clearance-rank table,
and a live-computed **persona × classification access matrix**.

Tell the design story: "An earlier revision had a third rule — *allow if same
team*. We **removed** it, because it let a general-clearance analyst read
`ceo-only` traces just because their team produced them. The model is now:
**clearance is a hard ceiling; team can only narrow access, never widen it** —
and anything without a matching allow rule is denied by default."

**Land.** "That's attribute-based access control: subject attributes (clearance,
team) evaluated against row attributes (classification, owner). It's the same
model your other governed data stores use — which is exactly why it belongs at
the platform layer, not in every app team's code."

**Ask.** "Who would own these policies in your org — security, platform, each
team? And what attributes would the real rules need — clearance, geography,
customer tenant?"

---

## Act 3 · The honest architecture conversation (3–4 min)

**Frame.** Now the part that makes this consultative instead of a magic trick:
where this enforcement *actually runs*, and why that's not good enough for
production.

**Show.** The **Design** tab (`/design`). Walk three things:

1. **The sequence diagram** — the app fetches *all* traces from the Langfuse API
   server-side, filters in the app, returns the subset. Say it plainly: "the
   filter runs *after* the data leaves the store. Anyone with the Langfuse API
   key bypasses it entirely, and it degrades linearly with trace volume."
2. **The gap analysis table** — app-layer enforcement, no push-down, no audit
   log, exports not covered. "We wrote the holes down so nobody discovers them
   for us."
3. **The three horizons** — short term: separate projects per sensitivity tier
   (works today); medium term: a filtering proxy like this one (bypassable,
   doesn't scale); long term: **native RLS, enforced as a `WHERE` clause pushed
   down into ClickHouse** so unauthorized rows never leave the database.

**Land.** "The end-state is a ClickHouse story: Langfuse traces already live in
ClickHouse, so row policies belong where the rows are — evaluated in the
database, at query time, at analytical scale. This prototype is the requirements
spec for that conversation, and your input sharpens it."

**Ask.** "If native RLS landed, what would your policy actually look like — and
what would you need alongside it: audit logs of denied access, export controls,
per-tenant keys? What's the minimum that unblocks you?"

---

## Close · What to leave them with (1 min)

Three sentences: **the gap is real** (roles ≠ row visibility, and traces are
sensitive); **the requirement now has a concrete shape** (this mock, plus the
design doc with the gaps named); **the path runs through ClickHouse** (native
enforcement, push-down, one governed store for traces and analytics). The repo
is public — the app, the policy engine, the seed data, and the full design doc
are all in `demos/langfuse-rls/`; the `/design` page *is* the leave-behind.

---

## Under the hood — the whole mechanism (for the "show me" moment)

The enforcement is deliberately small — that's the point; it's a spec, not a product.

**1 · The one real gate — `lib/rls-policy.ts`**
```ts
// lib/rls-policy.ts:4   clearance is an ordered ladder
const CLEARANCE_RANK = { "ceo-only": 2, "restricted": 1, "general": 0 };
// lib/rls-policy.ts:17  evaluate(): allow iff subject clearance >= row classification,
// no-metadata rows are public, everything else falls through to deny-default
```
*Why it matters:* the entire policy engine is a rank comparison plus
default-deny — easy to review, easy to argue about, easy to hand to Langfuse
engineering as a spec.

**2 · The enforcement point (and the problem with it) — `app/api/traces/route.ts:11`**
```ts
const traces   = await listTraces();                 // fetch ALL traces from the Langfuse API
const evaluated = evaluateBatch(persona, traces);    // filter in-process
const visible   = evaluated.filter((t) => t._rls.allow);
```
*Why it matters:* this is the exact line that should not exist in a native
implementation — it would become a `WHERE` clause evaluated inside ClickHouse
before any row leaves the store.

**3 · Subjects are attributes, not special cases — `lib/personas.ts:3`**
```ts
{ id: "alice", team: "executive",  clearance: "ceo-only"   },
{ id: "bob",   team: "compliance", clearance: "restricted" },
{ id: "carol", team: "analyst",    clearance: "general"    },
```
*Why it matters:* swap these for your customer's real teams/attributes and the
demo becomes *their* requirement in ten minutes.

**4 · The data path — `lib/langfuse-client.ts:44`**
```ts
// paginated fetch of ${host}/api/public/traces with Basic auth (pk:sk), 60s cache
```
*Why it matters:* everything rides the public Langfuse API — no forked Langfuse,
no direct ClickHouse access. The prototype changes nothing about the stack it
critiques.

---

## Talking points & objections

- **"So Langfuse can do this?"** No — and say so before they ask. Native Langfuse
  RBAC is role-based per project; all members see all traces. This simulates the
  *proposed* row-level behavior. That candor is what makes the rest credible.
- **"Can't we just use this proxy in production?"** Not as-is: it's bypassable by
  anyone holding the API keys, it fetches everything and filters in the app, and
  there's no audit trail. It's a requirements artifact, not a control.
- **"What do we do *today*?"** Separate Langfuse projects per sensitivity tier —
  real isolation now, at the cost of fragmented analytics and duplicated ops.
  Self-hosting keeps all tiers in your own tenant either way.
- **"Why is ClickHouse the answer here?"** Traces are stored in ClickHouse
  already; row policies enforced at the database mean unauthorized rows never
  leave the store, the same policy governs UI and API, and it holds up at
  analytical scale — none of which an app-layer filter can promise.
- **"Is anyone actually asking for this?"** Yes — this prototype exists because a
  global bank flagged trace-level access control as a blocker, and Langfuse
  engineering pointed to attribute-based RBAC/RLS as the long-term direction.
  Customer requirements with concrete specs are what move that roadmap.

---

## Reset / re-run

```bash
npm run seed          # re-push the 30 fixture traces (idempotent for the demo's purposes)
npm run dev           # app on :3000
bash scripts/verify.sh   # trace count + classification distribution via langfuse-cli
```

If the app shows an empty state, Langfuse isn't reachable on `:3001` or the
`.env` keys are wrong — fix `.env`, reseed, refresh.
