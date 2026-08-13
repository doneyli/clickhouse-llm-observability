# Langfuse v4 Migration Spec

> **Status:** ✅ **EXECUTED** — Phases 1–4 complete and verified (2026-08-13).
> **Scope:** migrated this repo's Langfuse Python SDK usage to v4 while the self-hosted
> stack stays on Langfuse server **3.221.1**. One SDK (`langfuse>=4.7,<5.0`) now serves both
> Langfuse Cloud and self-hosted.
> **Author:** drafted 2026-08-05 from a three-part repo audit + empirical verification
> against `langfuse` 3.15.0 and 4.14.x installed side by side.
>
> **Read the corrections.** Two conclusions in this document were wrong and are retracted
> in place rather than deleted, because both misdiagnoses are instructive:
> - **§2.1** — the trace-delay/latency rationale was overstated; measured baseline is 1–3s.
> - **§11b → §11c** — an "SDK v4 is incompatible with self-hosted 3.221.1" finding that was
>   really a shell-key/project mismatch producing false 404s.
>
> Execution records: §11a (Phase 1), §11d (Phase 2), §11e (Phase 3), §11f (Phase 4).

---

## 1. TL;DR

Three things a reviewer needs to know before reading further:

1. **The repo is already ~95% v4-shaped.** There are exactly **3 hard breakages in 2
   files**. Someone already adopted the v4-shaped API (`start_as_current_observation`,
   `propagate_attributes`, `dataset.run_experiment`) across every demo. The bulk of this
   migration is version pins and stale docs, not call-site rewrites.

2. **A single v4 SDK is officially supported against both backends.** Python SDK v4
   requires self-hosted server **≥ 3.63.0**; this stack runs **3.221.1**. So "v4 for Cloud,
   v3 for self-hosted" is *not* a constraint we have to design around — we can standardize
   on one SDK and keep the server on v3. The exceptions are narrow and enumerated in §5.

3. **The trace-delay link is weaker than first drafted — see the correction in §2.1.**
   Measured baseline on the current v3 SDK is 1–3s, not minutes. The migration is justified
   on deprecation/GA/CI grounds; latency is a secondary benefit, not the headline.

---

## 2. Why now: the v3-SDK-on-Cloud real-time gap

This is the finding that reframes the migration from hygiene to bug-fix.

Langfuse Cloud runs the **v4** data model. On v4 ingestion, a span export only gets
**real-time** processing if the exporter identifies itself as v4-capable. Per Langfuse's
compatibility docs:

> On Langfuse v4, send the `x-langfuse-ingestion-version: 4` header on your span exporter
> to see data in real time; without it, data can be delayed by **up to 10 minutes**.
> (Langfuse SDKs at Python ≥ 4.7.0 / JS ≥ 5.4.0 qualify for real time automatically.)

**Verified mechanism** (read directly from both installed SDKs):

| | `langfuse` 3.15.0 | `langfuse` 4.14.2 |
|---|---|---|
| OTLP endpoint | `{base}/api/public/otel/v1/traces` | identical |
| `x-langfuse-sdk-version` header sent | `3.15.0` | `4.14.2` |
| `x-langfuse-ingestion-version` in default headers | absent | absent |
| Qualifies for Cloud real-time | **no** | **yes** (server infers from SDK version ≥ 4.7.0) |

Source: `span_processor.py:107-123` in 4.14.2 (`default_headers` + endpoint), and the
equivalent `:91-103` in 3.15.0. Neither sets the ingestion-version header explicitly; the
server derives eligibility from `x-langfuse-sdk-version`.

`demos/real-estate` runs **3.15.0 against Cloud** → it does not qualify for real-time →
traces land on a batching window of **up to 10 minutes**, intermittently. That matches the
reported symptom exactly: some traces appeared in seconds, others took minutes, all
eventually arrived, and nothing was ever actually lost.

Corroborating detail already in the codebase: `demos/real-estate/agent/config.py:114` sets
`x-langfuse-ingestion-version: "4"` **by hand** on the mirror exporter, with a comment
noting that observation-level evaluators only fire in real time on v4-ingested data. The
primary client never got the same treatment because the v3 SDK gives you no hook for it.

**Implication:** upgrading `demos/real-estate` to `langfuse >= 4.7.0` removes the delay
deterministically, and removes the need for the hand-set header on the mirror.

> ⚠️ **Reviewer input wanted.** You mentioned the earlier issue "was a V4 error." If you saw
> a literal error string, please paste it — it either confirms this analysis or points at a
> second, separate problem. This spec assumes the ingestion-version gap; it does not depend
> on that assumption for anything except §2.

### 2.1 CORRECTION — measured, 2026-08-05

The above mechanism is real and quoted from Langfuse's docs, but **it does not currently
reproduce on this project, and the original draft overstated it as "probably the fix."**

Four pre-migration turns measured against the unmodified v3.15.0 portal (full data in the
`BASELINE-pre-migration.md` capture):

| Sample | turn latency | id resolvable | observations present |
|---|---|---|---|
| pre1 | 14s | 0s | <40s |
| pre-s1 | 17s | 2s | **2s** |
| pre-s2 | 15s | 3s | **3s** |
| pre-s3 | 15s | 1s | **1s** |

Steady state is reached within tens of seconds in every case: 7 observations, 4–5 scores,
trace I/O present. **No multi-minute delay was observed.**

What the measurement *did* establish — and this is the more useful finding — is that Cloud
consistency is **staged**, and the first stage is misleading:

1. the trace **id resolves almost immediately**, returning **HTTP 200 with an empty trace**
   (0 observations, `input`/`output` null)
2. observations appear (partially first — 3–5 — then 7)
3. trace-level input/output and evaluator scores land **last**

So "the View-trace link works but the trace looks blank" is the *expected* appearance during
the window, and **any verification that asserts only `HTTP 200` reports a false success.**
This is almost certainly what a user clicking through immediately after a turn perceives as
"the trace isn't there."

Consequences for this spec:
- Latency is demoted to a **secondary** rationale. The primary case is: Python SDK v3 is
  **Deprecated** on Cloud v4 while v4 is **GA**, plus unblocking `langfuse/experiment-action`
  (§7.2) and collapsing three SDK generations into one story.
- **V2 in §8 is rewritten** to assert *populated* (observations > 0 **and** trace I/O set),
  never bare HTTP 200 — and to compare against the measured 1–3s baseline rather than an
  assumed multi-minute one.
- The up-to-10-minute worst case remains documented and possible under load; it is simply
  not the current steady state, so it cannot be used as the justification.

---

## 3. Ground truth (verified, not assumed)

### 3.1 Version facts

| Fact | Value | How verified |
|---|---|---|
| Self-hosted Langfuse web | `langfuse/langfuse:3.221.1` | `docker-compose.yaml:523` |
| Self-hosted Langfuse worker | `langfuse/langfuse-worker:3.221.1` | `docker-compose.yaml:504` |
| Min server for Python SDK v4 | **≥ 3.63.0** | Langfuse self-hosted SDK↔server matrix |
| → 3.221.1 satisfies it | ✅ yes, by a wide margin | arithmetic |
| Python required by v4 | **≥ 3.10** | PyPI metadata (install on 3.9 fails) |
| → all demos run Python | **3.11** | every `Dockerfile:1`, real-estate `.venv` |
| Pydantic v2 required by v4 | already satisfied repo-wide | `dashboard/requirements.txt:4` (2.11.3), brand-promo `>=2.0`, installs carry 2.12–2.13 |
| Latest `langfuse` on PyPI | 4.14.2 | probe venv |

### 3.2 What v4 actually removes (diffed 3.15.0 → 4.14.2)

Removed from the `Langfuse` client: `update_current_trace`, `start_span`,
`start_generation`, `start_as_current_span`, `start_as_current_generation`.
Removed from span objects: `update_trace`, plus the same four `start_*` methods.

Added: `set_current_trace_io` (carries an `@deprecated` decorator),
`set_current_trace_as_public`, span `.set_trace_io()` / `.set_trace_as_public()`.

### 3.3 Five things the standard migration checklist warns about that do NOT apply here

Each of these removes work from the estimate. All verified against installed 4.14.2:

1. **`release=` / `environment=` are NOT removed.** Both are still live constructor
   params. No env-var migration needed; `LANGFUSE_RELEASE` / `LANGFUSE_TRACING_ENVIRONMENT`
   already worked in v3.
2. **The metadata `dict[str,str]` ≤200-char rule applies only to
   `propagate_attributes(metadata=)`.** Observation-level `metadata=` on
   `start_observation` / `.update()` is still `Optional[Any]`, so the large metadata dicts
   in `test-scenarios/export_test_scenarios.py:1155-1164` and `concierge.py:165-167` are
   safe as-is.
3. **Smart span filtering is a non-event.** `is_default_export_span` exports any span from
   the Langfuse tracer, any span carrying `gen_ai.*` attributes, and spans from ~35
   allow-listed LLM scopes — `litellm` and
   `opentelemetry.instrumentation.langchain` are both on that list. All instrumentation in
   this repo is Langfuse-SDK-created or LangChain/LiteLLM. No `should_export_span` needed.
4. **Zero hits repo-wide** for `item.run(...)`, `@observe`, `CallbackHandler(update_trace=)`,
   `observations_v_2` / `score_v_2` / `metrics_v_2`, `langfuse.types` imports,
   `TraceMetadata` / `ObservationParams` imports.
5. **`host=` still works in v4.** Not renamed. `base_url=` is preferred and has higher
   precedence — see the footgun in §7.4.

Two lookalikes that must NOT be "migrated" by a mechanical pass:
- `demos/agentic-rag/langfuse_config.py:79` defines its **own** `observe()` contextmanager.
- `demos/langfuse-rls/lib/types.ts:12` `TraceMetadata` is a local domain interface.

---

## 4. Current state inventory

### 4.1 Which backend each component targets

Cloud-facing (the migration target):

| Component | Target | Evidence |
|---|---|---|
| `demos/real-estate` | **Langfuse Cloud US** | `.env:3` → `https://us.cloud.langfuse.com` |
| `.github/workflows/langfuse-prompt-ci.yml` | **Cloud**, by necessity | `:9-12`, secrets at `:160-162` — a GitHub runner cannot reach localhost |

Self-hosted-facing (must not regress):

| Component | Host resolution | SDK pin |
|---|---|---|
| `demos/text-to-sql` | `${LANGFUSE_INTERNAL_URL:-http://langfuse-web:3000}` | `>=3.0.0,<4.0.0` |
| `demos/vector-rag` | same | `>=3.0.0,<4.0.0` |
| `demos/agentic-rag` | same | `>=3.0.0,<4.0.0` |
| `test-scenarios` | same | `>=3.0.0,<4.0.0` |
| `demos/brand-promo-multi-agent` | `.env` → `localhost:3001` (template) | **`>=4.0,<5.0` — already v4** |
| `demos/litellm-gateway` | `LANGFUSE_OTEL_HOST` → internal | no repo SDK (LiteLLM's own) |
| `dashboard` | raw ClickHouse + REST | **no SDK** |
| `librechat` | internal | bundled `@langfuse/* 5.7.0` (JS v5) |
| `demos/langfuse-rls` | `localhost:3001` | JS `langfuse ^3.36.0` |
| `scripts/*` | root `.env` — backend-agnostic | inline pins in docstrings |

**Note the two components that are already on a newer SDK than a v3 server:**
`brand-promo` (Python v4, template points at `localhost:3001`) and `librechat`
(JS v5 → `langfuse-web:3000`). These are the existing empirical proof that a
newer SDK works against 3.221.1 — see §5.3.

### 4.2 The three hard breakages

**B1 + B2 — `demos/real-estate/agent/concierge.py`**

- `:170-171` — `lf.update_current_trace(input={"query": query}, metadata={"agent_model": model})`
- `:317` — `lf.update_current_trace(output=final_text)`

Both are inside `if not is_experiment:`, so only the live/portal path is affected.

**B3 — `scripts/import-external-traces.py:150` — the subtle one**

```python
client.api.observations.get_many(trace_id=trace_id, limit=100, page=page)
```

This is **not a rename — it is a meaning change on an unchanged spelling.** Verified by
introspecting 4.14.2:

| | v3.15.0 | v4.14.2 |
|---|---|---|
| `api.observations` resolves to | v1 client | **v2 client** |
| `get_many` params | `page`, `limit`, … | `cursor`, `fields`, `limit`, … — **no `page`** |
| v1 client still available at | — | `api.legacy.observations_v1` (confirmed: takes `page`) |

Under v4 this raises `TypeError: get_many() got an unexpected keyword argument 'page'`,
and `:154` (`batch.meta.total_pages`) breaks next. **A grep-for-renames pass would miss
this entirely** — nothing about the line changes.

Verified safe in the same file: `:119` `client.api.trace.list(limit=, page=)` (signature
byte-identical across versions), `:311` raw `POST /api/public/ingestion` (HTTP, SDK-agnostic).

### 4.3 Pin sites (10)

Declared pins: `demos/text-to-sql/requirements.txt:7`,
`demos/vector-rag/requirements.txt:16`, `demos/agentic-rag/requirements.txt:20`,
`demos/real-estate/requirements.txt:2`, `test-scenarios/requirements.txt:2`.

Inline `pip install 'langfuse>=3.0,<4.0'` strings in docstrings/help text:
`scripts/import-external-traces.py:31,45`, `scripts/run-experiments.py:25,38`,
`scripts/seed-datasets.py:25,37`, `scripts/seed-demo-data.sh:182`.

---

## 5. The self-hosted safety model

This section answers "how do I make sure there are no breaking changes for self-hosted,
which stays on v3?"

### 5.1 The key reframe

The SDK does not target "Cloud" or "self-hosted" — it targets whatever `LANGFUSE_HOST` /
`LANGFUSE_BASE_URL` points at. So the question is not "can I use two SDKs?" but **"is v4
supported against server 3.221.1?"** Per Langfuse's own SDK↔server matrix: **yes, minimum
3.63.0.** We are 158 minor versions above the floor.

This means the safest design is *not* to maintain two SDK generations — it is to
standardize on v4 and keep the server on v3.

### 5.2 The narrow exceptions — features that need a real v4 server

Only three, and we can audit each:

| v4-server-only feature | Repo usage today | Action |
|---|---|---|
| `api.observations` / `api.metrics` (v2 endpoints) | **1 site**: `import-external-traces.py:150` | pin to `api.legacy.observations_v1` (B3) — works on both backends |
| Monitors & alerts | none | none |
| Real-time OTel ingestion | Cloud only; a v3 server has no such batching gate | nothing needed for self-hosted |

Everything else the repo touches — tracing/ingestion via OTLP, `create_score`,
`get_prompt` / `create_prompt`, datasets, `dataset.run_experiment`, annotation queues,
score-configs — is available on 3.221.1.

### 5.3 Existing empirical proof

Two components in this repo **already** run a newer-than-v3 SDK against the 3.221.1
server, in the default configuration:

- `demos/brand-promo-multi-agent` — Python `langfuse>=4.0,<5.0` (4.14.0 installed), and its
  `.env.example:13` / `demo.config.example.yaml:132` both default to `http://localhost:3001`.
- `librechat` — bundles `@langfuse/{core,tracing,otel,langchain} 5.7.0` (the JS v5 line)
  and points at `http://langfuse-web:3000` (`docker-compose.yaml:78,81`).

Both are known-working in this stack today. That is the strongest available evidence that
the v4-SDK-against-3.221.1 combination is fine, and it de-risks Phase 2 substantially.
**Verification step V4 in §8 makes this explicit rather than assumed.**

### 5.4 What is genuinely coupled to server v3 — and is NOT touched by an SDK bump

Worth stating plainly so it doesn't get conflated with this migration:

The evaluator seeding scripts write **directly into Langfuse's Postgres schema** —
`eval_templates`, `job_configurations`, `default_llm_models`, reading `api_keys`,
`llm_api_keys`, `projects`, `datasets`, and deleting two Redis cache keys
(`scripts/seed-code-evaluators.sh`, `seed-llm-judge-evaluators.sh`,
`seed-agentic-rag-evaluators.sh`, `demos/real-estate/scripts/seed_managed_evaluators.sh`).
`seed-agentic-rag-evaluators.sh:13` says so outright: *"schema-coupled to the langfuse:3
image."* `setup.sh:692,697` and `scripts/validate-langfuse.sh:268-269` also read
`job_configurations` directly for the readiness checklist.

Also schema-coupled: `scripts/seed-clickhouse-project-dict.sh` (ClickHouse dictionary over
Langfuse's Postgres `projects` table) and `dashboard/clickhouse_client.py` (queries
Langfuse's ClickHouse `traces`/`observations`/`scores` tables).

**None of this is affected by an SDK version bump.** It breaks on a *server* upgrade.
Since this spec explicitly keeps the server on 3.221.1, all of it is out of scope — but it
is also the reason **§6 recommends not upgrading the server**.

### 5.5 The real self-hosted risk in this migration

Not the SDK version. It is this: **`demos/real-estate` can be pointed at self-hosted**, and
after migration an operator following the shipped template gets v4-SDK → v3-server on a
path we only tested against Cloud. Specifics:

- `demos/real-estate/.env.example:15` still ships `LANGFUSE_HOST=http://localhost:3001` —
  the *opposite* of the working `.env` (Cloud). The template is already misleading today.
- `demos/real-estate/.env.selfhosted.bak` exists and is a one-command switch back to
  self-hosted-primary + Cloud-mirror.
- `agent/config.py:35` defaults to `http://localhost:3001`.
- On self-hosted, `seed_managed_evaluators.sh` provisions **trace-level**
  (`target_object='trace'`) judges, which **depend on trace-level input/output**. On Cloud
  it provisions **observation-level** (`target: "observation"`) judges.

That last point drives a real design decision: **we must keep trace-level I/O populated**,
which is exactly what `set_current_trace_io()` does — despite it being deprecated on
arrival. Its own docstring says it exists for platform features still relying on
trace-level I/O, naming legacy LLM-as-a-judge evaluators. That is our self-hosted case, so
using it is correct here rather than a shortcut. Flagged as tech debt in §10.

A cross-cutting hazard found in the audit, worth fixing while we're here:
`setup.sh:243-244` never rewrites an existing `LANGFUSE_HOST` line, and `:262-263` never
rewrites `LANGFUSE_INTERNAL_URL`. Flipping `DEPLOY_MODE` on an already-provisioned `.env`
leaves stale hosts in place — the exact mechanism by which a component ends up pointed at
the backend nobody expected.

---

## 6. Scope and phasing

### In scope

- Python SDK v3 → v4 across the repo, **staged**.
- Fix the 3 hard breakages.
- Bump the 10 pin sites.
- Keep the self-hosted server on **3.221.1** (unchanged).
- Update stale docs that assert v3.

### Out of scope (confirmed separate work)

- **Upgrading the self-hosted server to v4.** ✅ *Confirmed out of scope — server stays on
  `3.221.1`.* Would break the Postgres-INSERT evaluator seeding (§5.4) and the ClickHouse
  dictionary — a substantially larger project.
- **`demos/langfuse-rls` JS SDK v3 → v5.** Different version track; `baseUrl`, `.trace()`,
  and `flushAsync()` all change shape. Separate PR.
- **`librechat`'s bundled JS SDK** — controlled by the image digest, not by us.
- The two incidental pre-existing bugs in §10.2.

### Phasing — ✅ all four phases approved

All four phases are in scope for execution, landing **in order** as separate commits so each
stays reviewable and independently revertable. Phase 1 must still be internally atomic (see
below).

**Phase 1 — Cloud-facing (fixes the bug, unblocks CI).** `demos/real-estate` + the CI
workflow. Must land atomically: CI installs `demos/real-estate/requirements.txt`, so the
pin bump and the `concierge.py` fixes cannot be split or CI installs v4 against unfixed
`update_current_trace` calls.

**Phase 2 — self-hosted demos (pin-only, zero code changes).** `text-to-sql`,
`vector-rag`, `agentic-rag`, `test-scenarios`. All four are already v4-clean; this is
purely a pin bump plus verification against 3.221.1.

**Phase 3 — shared scripts.** `import-external-traces.py` (B3) + the inline pin strings.

**Phase 4 — docs.** §7.5.

Phases 2–4 are independent of Phase 1 and of each other. Phase 1 alone delivers the
user-visible win.

---

## 7. Detailed change list

### 7.1 Phase 1 — `demos/real-estate` (Cloud)

**`demos/real-estate/requirements.txt:2`**
```diff
-langfuse>=3.0,<4.0
+langfuse>=4.7,<5.0
```
Floor is `4.7` deliberately, not `4.0`: **≥4.7.0 is what qualifies for Cloud real-time
ingestion** (§2). Using `>=4.0` would technically migrate the API but leave the delay bug
in place.

**`demos/real-estate/agent/concierge.py:170-171`** — decompose. Current:
```python
lf.update_current_trace(input={"query": query}, metadata={"agent_model": model})
```
Target: move `metadata` into the `propagate_attributes(...)` block already present at
`:150`, and set I/O via the new call:
```python
lf.set_current_trace_io(input={"query": query})
```
`propagate_attributes(metadata=)` coerces values to strings and caps them at 200 chars;
`{"agent_model": model}` is a short model id, so it is safe. Exact placement to be
confirmed against the live `propagate_attributes` call site during execution.

**`demos/real-estate/agent/concierge.py:317`**
```diff
-lf.update_current_trace(output=final_text)
+lf.set_current_trace_io(output=final_text)
```

**`demos/real-estate/agent/config.py:107-115`** — the mirror exporter. Once the primary is
on ≥4.7.0, re-evaluate the hand-set `x-langfuse-ingestion-version: "4"` header. Two
sub-cases:
- mirror = Cloud → header now redundant (SDK version suffices), but harmless. Recommend
  keeping it with an updated comment, since this is a plain `BatchSpanProcessor` that
  bypasses the SDK's own header logic entirely and therefore does *not* inherit the
  version-based eligibility.
- mirror = self-hosted v3 → the header is meaningless to a v3 server (no v4 ingestion
  gate). Harmless. Document rather than remove.

**No change needed** to `dataset.run_experiment`, `get_prompt(fallback=)`, `create_score`,
`start_as_current_observation`, or `propagate_attributes` anywhere in this demo — all
verified signature-compatible.

### 7.2 Phase 1 — CI

**`.github/workflows/langfuse-prompt-ci.yml`** — no change required for the migration
itself (it installs from `requirements.txt`, `:152`; cache key `:147`). Verify the cache
key busts on the pin change.

**Unlocked, optional follow-up:** v4 exports `RunnerContext` and `RegressionError`
(verified present in 4.14.2; v3 exports only `Evaluation`). That is exactly what Langfuse's
official `langfuse/experiment-action` needs, and it is why
`demos/real-estate/scripts/prompt_gate.py:23-28` documents its hand-rolled gate as a v3
workaround. Replacing the hand-rolled gate with the official action is now *possible* —
recommend deferring to its own PR so the migration stays reviewable.

### 7.3 Phase 2 — self-hosted demos (pin-only)

```diff
-langfuse>=3.0.0,<4.0.0
+langfuse>=4.7,<5.0
```
in `demos/text-to-sql/requirements.txt:7`, `demos/vector-rag/requirements.txt:16`,
`demos/agentic-rag/requirements.txt:20`, `test-scenarios/requirements.txt:2`.

**Zero code changes.** All four were audited as already v4-clean. Requires a Docker image
rebuild (`docker compose build`) since these are baked into images.

Align `demos/brand-promo-multi-agent/pyproject.toml:12` from `>=4.0,<5.0` to `>=4.7,<5.0`
for consistency (it is already v4; this only raises the floor).

### 7.4 Phase 3 — shared scripts

**`scripts/import-external-traces.py:150`** — B3:
```diff
-batch = client.api.observations.get_many(trace_id=trace_id, limit=100, page=page)
+batch = client.api.legacy.observations_v1.get_many(trace_id=trace_id, limit=100, page=page)
```
Keeps `page`-based pagination and the `ObservationsViews` return shape, so `:154`
(`meta.total_pages`) and the `input`/`output`/`metadata` reads at `:258-260` keep working
unchanged. **Do not** "modernize" this to `api.observations` — the v2 endpoint gates fields
behind a `fields=` param and would silently drop the I/O this script depends on.

This is also the one change that must work against **both** backends: this script can
straddle two instances at once (`SOURCE_LANGFUSE_HOST` default `localhost:3050`,
`TARGET_LANGFUSE_HOST` default `localhost:3001`, `:83`/`:102`). `api.legacy.observations_v1`
is available on both, which is precisely why it is the right target.

Inline pin strings → `langfuse>=4.7,<5.0`: `scripts/import-external-traces.py:31,45`,
`scripts/run-experiments.py:25,38`, `scripts/seed-datasets.py:25,37`,
`scripts/seed-demo-data.sh:182`.

**Precedence hardening — ✅ approved.** In v4, the `LANGFUSE_BASE_URL` **env var takes
priority over the `host=` constructor argument**, while `base_url=` passed explicitly has
the highest precedence of all. `demos/real-estate/agent/config.py:30-40` currently hard-sets
`os.environ["LANGFUSE_HOST"]` and then passes `host=`. That still works, but anything in the
environment setting `LANGFUSE_BASE_URL` would silently win over the demo's carefully
isolated `host=` — the same class of failure as this repo's documented history of
shell-exported `LANGFUSE_*` clobbering config.

Changes to make in `demos/real-estate/agent/config.py`:

1. Pass `base_url=LANGFUSE_HOST` instead of `host=LANGFUSE_HOST` in the `Langfuse(...)`
   constructor (`:144-148`) — highest precedence, immune to env override.
2. Hard-set `os.environ["LANGFUSE_BASE_URL"]` alongside the existing
   `os.environ["LANGFUSE_HOST"]` (`:38-40`), so any *other* SDK path in-process
   (`get_client()`, sub-libraries) resolves to the same backend rather than an inherited one.
3. Keep reading the demo's own `.env` with `override=True` — unchanged.

Apply the same treatment where the mirror builds its endpoint, so primary and mirror cannot
diverge via env. This is a behavior change, so V1/V3 in §8 must confirm the demo still lands
in the expected project (`verify_project()` at `:240-266` is the existing guard and should
still pass).

### 7.5 Phase 4 — docs

Assert-v3 statements to correct: `CLAUDE.md:83` (documents `langfuse.trace()` /
`trace.span()` / `trace.generation()` as "v3 patterns" — that is actually the **v2** API and
matches no code in the repo), `docs/LANGFUSE_INTEGRATION.md:288-306` ("SDK v3
Compatibility"), module docstrings at `demos/vector-rag/langfuse_config.py:2,31`,
`demos/agentic-rag/langfuse_config.py:2`, `demos/text-to-sql/langfuse_config.py:2`,
plus `docs/LANGFUSE_SKILLS.md:45`, `docs/CODE_EVALUATORS.md:72`, `README.md:379`.

After Phase 1, also update `demos/real-estate/scripts/prompt_gate.py:22-28` and
`demos/real-estate/cicd/README.md:137-144`, which both state the demo *cannot* use
`langfuse/experiment-action` because it needs v4.

Fix the template/reality inversion at `demos/real-estate/.env.example:15` (ships
`localhost:3001` while the working config is Cloud) — see §5.5.

---

## 8. Verification plan

Mocked tests are explicitly insufficient — the workflow requires inspecting real traces.

| ID | Check | Method | Gate |
|---|---|---|---|
| V1 | Real-estate portal produces a complete trace on Cloud | run a portal turn, fetch trace via API, assert ≥5 observations + scores present | Phase 1 |
| V2 | Trace latency does not regress | timestamp a portal turn, poll Cloud until **populated** — `observations > 0` **AND** trace `input`/`output` non-null. **Never assert bare HTTP 200**: an empty trace returns 200 (§2.1). Compare against measured baseline **1–3s to first observations**; flag any regression | Phase 1 |
| V3 | Trace-level I/O still populated (managed judges depend on it) | assert `input`/`output` non-null on the root, and that judge scores attach | Phase 1 |
| V4 | v4 SDK works against self-hosted 3.221.1 | run text-to-sql + agentic-rag against `langfuse-web:3000`, compare trace shape/observation count to a pre-migration baseline | Phase 2 |
| V5 | No span thinning from smart filtering | diff observation counts per trace, pre vs post, for every migrated demo | Phases 1–2 |
| V6 | `import-external-traces.py` still paginates | dry-run against a source instance, assert >1 page traversed and I/O fields non-empty | Phase 3 |
| V7 | CI gate still gates | run the workflow with a deliberately bad prompt label, assert non-zero exit | Phase 1 |
| V8 | Evaluators still fire | `./setup.sh --status` all ✓; `job_configurations` still 5 `code-eval*` + 4 `obs-eval*` ACTIVE | Phases 1–2 |
| V9 | Session grouping intact | multi-turn portal conversation groups into one Langfuse Session | Phase 1 |

Baselines for V4/V5 must be captured **before** any pin bump.

---

## 9. Rollback

Every phase is independently revertable, and rollback is cheap because no server or data
migration is involved:

- **Phase 1/2/3:** `git revert` the PR, then rebuild (`docker compose build` for
  containerized demos, `pip install -r requirements.txt` in the real-estate venv). The v3
  SDK writes to the same OTLP endpoint, so already-ingested traces stay valid and readable.
- **No schema, no data, no server change** in any phase → no data-loss rollback path needed.
- Keep `demos/real-estate/.venv` reproducible: capture `pip freeze` before upgrading so the
  exact 3.15.0 dependency set can be restored.
- Traces ingested during a v4 window remain fully readable if you roll back to v3; the
  ingestion-version only affects *processing latency*, not stored shape.

---

## 10. Risks, decisions, and debt

### 10.1 Decisions — resolved 2026-08-05

| # | Decision | Outcome |
|---|---|---|
| 1 | Phasing | ✅ **All four phases**, landing in order as separate commits. Phase 1 internally atomic. |
| 2 | Pin floor | ✅ **`>=4.7,<5.0`** — below 4.7 the Cloud real-time fix (§2) does not apply. |
| 3 | `config.py` `host=` → `base_url=` | ✅ **In scope** — hardened per §7.4. |
| 4 | Self-hosted server upgrade to v4 | ✅ **Out of scope** — server stays `3.221.1` (§5.4). |

Still outstanding (does not block execution):

- **The "V4 error" string.** If a literal error message was seen when traces failed to
  appear, it should be checked against §2. The §2 analysis stands on its own evidence, but a
  real error string could reveal a second, independent problem.

### 10.2 Incidental pre-existing bugs found during the audit

Both are broken **today on v3** and v4 will not fix them. Recommend separate PRs so they
don't muddy the migration diff:

- **`client.score(...)` does not exist in v3 or v4** (the real name is `create_score`). The
  calls sit inside `try/except` that prints and swallows, so `score_trace()` has silently
  never recorded anything: `demos/text-to-sql/langfuse_config.py:195,204` and
  `demos/vector-rag/langfuse_config.py:174,183,192`.
- **`test-scenarios/export_test_scenarios.py:1178`** passes `usage={...}` to
  `generation.update()`. Both SDK versions accept `**kwargs`, so it is silently swallowed —
  token usage is never recorded. Correct param is `usage_details=`.

### 10.3 Debt accepted by this spec

- `set_current_trace_io` is **deprecated on arrival**. We adopt it anyway because
  self-hosted trace-level judges need trace I/O (§5.5). Revisit when those judges move to
  observation-level on both backends.
- The repo will briefly span two SDK generations (Python v4 + JS v3 in `langfuse-rls`).
- `langfuse-cli` remains **unpinned** (`npx langfuse-cli`, floats to npm latest) —
  `scripts/langfuse-cli.sh:46`. Independent of this migration but a latent surprise.

### 10.4 Pre-existing wrapper bugs (adjacent, not caused by this)

`scripts/langfuse-cli.sh` checks for `npx` at `:39-43` but then runs `exec langfuse "$@"`
at `:46`, requiring a **globally installed** binary. Separately, every documented
invocation omits the CLI's `api` verb (`docs/LANGFUSE_CLI.md:34-46`, `README.md:343-351`,
`CLAUDE.md:48`) while the one working caller uses `langfuse api traces list`. Worth fixing,
but as its own change.

---

## 11. Preliminary readiness report

Per the migration workflow's required seven rows. **This reflects the pre-execution state**
— it is an assessment, not a completion report. The post-execution report will restate
these.

| Row | Status | Notes |
|---|---|---|
| **Project access** | `ready` | Read access verified to **both** backends this session: Cloud US via the real-estate project keys (`/api/public/traces` 200) and self-hosted via `localhost:3001` + `docker exec langfuse-postgres`. Enables real verification rather than code-only guessing. No writes performed. |
| **SDK / instrumentation** | `changed` (planned) | 3 hard breakages in 2 files (§4.2), 10 pin sites (§4.3). Repo otherwise already v4-shaped. Server 3.221.1 ≥ the 3.63.0 floor, Python 3.11 ≥ 3.10, Pydantic v2 satisfied. |
| **Trace evaluators** | `manual action` | Self-hosted judges are seeded by **direct Postgres INSERT**, schema-coupled to `langfuse:3` (§5.4) — untouched by an SDK bump but must be re-verified via V8. Cloud judges are observation-level via the **unstable** `/api/public/unstable/evaluation-rules` API. A "Legacy" row inventory in each project's Evaluators UI is still required and has **not** been done. |
| **Dataset evaluators** | `manual action` | `code-eval*` / `obs-eval*` rows include `target_object='experiment'`. `dataset.run_experiment` verified signature-compatible v3↔v4, but the experiment-scoped evaluator rows need the same legacy-row inspection. |
| **Direct APIs** | `changed` (planned) | Repo is v1-paths throughout except prompt management (v2). One real break: the silent `api.observations` v1→v2 flip (B3). Deprecated-endpoint readers to watch: `meta.totalItems` / `meta.totalPages` in `setup.sh:675`, `dashboard/langfuse_client.py`, `demos/langfuse-rls/lib/langfuse-client.ts`; hand-built `POST /api/public/ingestion` envelopes in `import-external-traces.py:311`; private `POST /api/admin/projects` in `brand-promo/scripts/setup_langfuse_project.py:31`. |
| **Exports** | `blocked` | Blob Storage / Mixpanel / PostHog integrations live in **Project Settings → Integrations** and have not been inspected. No export configuration found in the repo, but absence in code is not proof of absence in the projects. Requires a UI/API read per project before this can move off `blocked`. |
| **Verification / rollback** | `ready` (plan only) | 9-check plan in §8 with per-phase gates; rollback in §9 needs no data or server migration. **Not yet executed.** |

---

## 11a. Phase 1 execution results — DONE, 2026-08-05

Executed on branch `claude/langfuse-trace-delay-93fc38`. **Phases 2–4 not started** (stopped
before the self-hosted demos, as requested).

### Changes landed (3 files, +44/−10)

| File | Change |
|---|---|
| `demos/real-estate/requirements.txt:2` | `langfuse>=3.0,<4.0` → `>=4.7,<5.0` |
| `demos/real-estate/agent/concierge.py:158` | `metadata={"agent_model": model}` moved into `propagate_attributes(...)` |
| `demos/real-estate/agent/concierge.py:172` | `update_current_trace(input=,metadata=)` → `set_current_trace_io(input=)` |
| `demos/real-estate/agent/concierge.py:324` | `update_current_trace(output=)` → `set_current_trace_io(output=)` |
| `demos/real-estate/agent/config.py:40-47` | accept `LANGFUSE_BASE_URL` or `LANGFUSE_HOST`; pin **both** in `os.environ` |
| `demos/real-estate/agent/config.py:166` | `host=` → `base_url=` (highest precedence) |
| `demos/real-estate/agent/config.py:125-134` | mirror header comment: why it is still needed under v4 |
| `demos/real-estate/agent/config.py:1-18` | docstring: v4 requirement + precedence rules |

`.github/workflows/langfuse-prompt-ci.yml` needed **no change**: its pip cache key is
`cache-dependency-path: demos/real-estate/requirements.txt` (`:148`), so the pin change busts
the cache automatically. It passes `LANGFUSE_HOST` (`:160`), which the new `or` fallback in
`config.py` still honors — and CI has no `.env`, which also still works.

### Verification — measured, not assumed

Environment: fresh Python 3.11 venv in the worktree, `langfuse==4.14.3`, `pydantic==2.13.4`.
Migrated portal run on **:8081**; the pre-existing v3 portal on :8080 left untouched so the
comparison is apples-to-apples against the same Cloud project.

| ID | Check | Result |
|---|---|---|
| — | modules import under v4 (`config`, `concierge`, `webapp.server`) | ✅ clean |
| — | `propagate_attributes` accepts `metadata=`; `set_current_trace_io(input,output)` exists | ✅ verified by introspection |
| V1 | complete trace produced on Cloud | ✅ 7 observations, 5 scores |
| V2 | latency does not regress | ✅ 1s / 1s / 5s to populated (baseline 1–3s) |
| V3 | **trace-level I/O still populated** (judges depend on it) | ✅ `input=True`, `output=True` on all 3 |
| V5 | no span thinning | ✅ **7 observations, identical names** to baseline |
| V5b | trace metadata unchanged | ✅ identical keys **and values**, incl. `turn: 1` still an `int` |
| V5c | no metadata pollution on children from propagation | ✅ child metadata byte-identical to baseline |
| V7 | CI gate still gates | ✅ correctly **failed** `first-draft` (`avg-used-search-tool` 0.947 < 1.00) over 19 dataset items, code evaluators **and** LLM judges; `run_evaluations` + `dataset_run_url` both work |
| V9 | multi-turn session grouping | ✅ both turns share `sessionId`, `turn` increments 1→2, session listed in Sessions API |

Sample trace ids — baseline v3: `8ecf1f2db057`, `4a243454bbac`, `1b6102b18bb6`,
`69e5745e8a05`. Post-migration v4: `348d7f068f63`, `860dfe213fca`, `7bf78068652f`.

### Notes and caveats

- **V7's literal exit code was not captured** — the run was wrapped in a pipe and
  `${PIPESTATUS[0]}` is bash-only (this shell is zsh), so the reported status came from
  `tail`. The gate's *semantics* are verified: it printed `GATE FAILED` with a non-empty
  failure set, and `prompt_gate.py:169-170` is `if failures and not args.warn_only:
  sys.exit(1)`. Re-running purely to capture the integer costs another ~9 min and 19 LLM
  calls, so it was judged not worth it. **Worth confirming on the first real CI run.**
- `propagate_attributes(metadata=)` turned out to be **redundant** for trace metadata — the
  root observation already supplies it under v4's observations-first model (V5b/V5c show
  identical output with and without). Kept anyway: it faithfully preserves the intent of the
  v3 `update_current_trace(metadata=)` call and is verified harmless.
- **`demos/real-estate/.venv` in the main checkout is still on 3.15.0 and was deliberately
  not touched.** It also predates the `demos/` reorg — its console scripts carry a stale
  shebang (`.../real-estate-demo/.venv/bin/python3.11`), so `./.venv/bin/pip` fails with
  "bad interpreter" and only `./.venv/bin/python -m pip` works. **Recreate that venv rather
  than pip-installing into it** when adopting these changes on the main checkout.
- A migrated portal is **still running on :8081** from the worktree (see §12).

## 11b. Phase 2 — first attempt was a FALSE ALARM (2026-08-12)

> ⛔ **RETRACTED.** This section originally concluded that v4 traces are silently dropped by
> self-hosted 3.221.1, and Phase 2 was aborted on that basis. **That conclusion was wrong.**
> The cause was a **key/project mismatch in the operator's shell**, not an SDK
> incompatibility. See §11c for the correction and the real Phase 2 result. The retracted
> reasoning is kept below only because the misdiagnosis is instructive.

### The (incorrect) finding as originally written

**Langfuse Python SDK v4 traces are silently dropped by self-hosted Langfuse server
3.221.1.** The OTLP POST is accepted with **HTTP 200**, `auth_check()` returns `True`, and
the trace never appears — not after 5 minutes, not after 20.

Controlled comparison — same server, same Docker network, same credentials, same endpoint
(`POST /api/public/otel/v1/traces`), differing only in SDK version:

| SDK | OTLP HTTP response | Trace queryable |
|---|---|---|
| `langfuse` **3.15.0** | 200 | ✅ **immediately** |
| `langfuse` **4.14.4** | 200 | ❌ **404 after 20 min** |

Evidence gathered:
- 3 independent minimal probes on v4 (`c21d4a01d009`, `b09e74908fab`, `4edc914e7532`) — all 404.
- A full `text-to-sql` run under v4.14.4: exited 0, no SDK errors, **zero traces** (polled 5 min).
- v3 control probe (`e694b51fd3a9`) on the pre-rebuild image: **landed instantly**.
- `langfuse-web` logs no ingestion error; `langfuse-worker` logs nothing about OTel ingestion.
  The worker is healthy (no Redis stall, code evals executing normally).

Ruled out:
- **Not** auth, host, or networking — `auth_check()` True, prompt fetches return 404-with-body
  (i.e. authenticated round-trips), OTLP POST returns 200.
- **Not** a span-attribute schema change — v3 and v4 emit the *same* attribute names
  (`langfuse.observation.input`, `langfuse.observation.type`).
- **Not** the `x-langfuse-sdk-version` header gate — spoofing it to `3.15.0` via
  `Langfuse(additional_headers=...)` did **not** make the trace land (`4edc914e7532`).
- **Not** ingestion latency — 20-minute poll, well past the documented 10-minute worst case.
- **Not** the worker Redis stall — zero socket-timeout errors in 12h.

Root cause is therefore **inside the 3.221.1 server's handling of v4-SDK OTLP payloads** and
was not isolated further; doing so means debugging Langfuse server internals, which is a far
larger scope than a pin bump.

### This corrects §5.3 — the "existing proof" was not proof

§5.3 argued that `brand-promo-multi-agent` (Python `langfuse>=4.0,<5.0`) and LibreChat
(`@langfuse/* 5.7.0`) already run newer SDKs against 3.221.1, so the combination must work.
**That inference was unsound**: nobody had verified those components actually *produce
traces* on self-hosted. Given this finding, the likelihood is that **brand-promo has never
successfully traced against the self-hosted stack** with its template default of
`http://localhost:3001` — it would fail exactly this silently (HTTP 200, no trace, no error).
Worth confirming separately; if true it is a live bug, not a migration artifact.

LibreChat's JS v5 path is untested here and should not be assumed either way — though note
LibreChat traces *are* known to work in this stack, so the JS ingestion path may differ from
the Python one.

### What was reverted

All five Phase 2 pin edits reverted in the working tree (never committed):
`demos/text-to-sql`, `demos/vector-rag`, `demos/agentic-rag`, `test-scenarios` back to
`langfuse>=3.0.0,<4.0.0`; `brand-promo-multi-agent` back to `>=4.0,<5.0`.

`text-to-sql` and `test-scenarios` images were **rebuilt back to v3** to restore tracing —
they had been left on 4.14.4 and were silently not tracing. `vector-rag` and `agentic-rag`
never successfully rebuilt (a PyPI `ReadTimeoutError` failed that build), so their images
were never on v4.

**Phase 1 (real-estate → Cloud) is unaffected and stays committed** — Cloud runs v4, where
v4 SDK is the GA pairing, and it was verified end-to-end.

### Revised options for the self-hosted demos

1. **Do nothing — leave them on SDK v3 (recommended for now).** v3 works against 3.221.1
   today. The repo then intentionally runs v4 for Cloud-facing code and v3 for
   self-hosted-facing code. This contradicts the original "one SDK story" goal, but it is the
   only option currently backed by evidence.
2. **Upgrade the self-hosted server to v4**, then bump the pins. This was explicitly ruled
   out (§5.4) because it breaks the Postgres-INSERT evaluator seeding and the ClickHouse
   project dictionary. It now looks like the *only* path to a single SDK version.
3. **Investigate the 3.221.1 ingestion path** (or raise it with Langfuse) to find whether a
   server flag enables it — `LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR` is documented as
   governing v4 OTel ingestion behavior on self-hosted and was **not** tested here. This is
   the cheapest next experiment if a single SDK version is still the goal.

### Also found while baselining (pre-existing, NOT migration-related)

Both surfaced on **unmodified v3 images** before any pin was touched:

- **`vector-rag` emits zero traces on a container run.** Runs all 10 queries, prints
  "Langfuse instrumentation enabled", authenticates (prompt fetch returns a real 404 body),
  exits 0 — and lands nothing. Two runs, no traces.
- **`text-to-sql` landed 1 trace for a 10-query run** (obs=10 containing *pairs* of LangChain
  spans, i.e. ~2 queries' worth).
- Likely contributor: `flush()` in both demos calls **`client.shutdown()`**
  (`demos/vector-rag/langfuse_config.py:214-215`, `demos/text-to-sql/langfuse_config.py:226-227`),
  which tears the client down rather than flushing. `demos/agentic-rag/langfuse_config.py:147-152`
  explicitly warns against this ("Use flush() (non-destructive), NOT shutdown()"), so the
  correct pattern already exists in-repo. **Not verified as the cause** — needs its own fix
  and test.
- `job_configurations` holds **6** `code-eval*` + **4** `obs-eval*` rows, all ACTIVE.
  `CLAUDE.md` and the troubleshoot skill both say "5 code-eval" — minor doc drift.

---

## 11c. CORRECTION — the §11b abort was a false alarm

### What actually happened

The operator's shell had `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` exported for a
**different project** than the one being queried:

| Key source | Resolves to project |
|---|---|
| root `.env` (`pk-lf-1234567890`) | `demo-project` — "LLM Observability Demo" |
| **shell export** (`pk-lf-612d504c…`) | **`claude-code`** — "ai-coding-assistants" |

`docker compose run` picks up the shell-exported values, so every containerised probe wrote
into **`claude-code`**. Verification queries used the `.env` key, which can only see
`demo-project` → **HTTP 404**. The v3 "control" probes were run via `docker run` with keys
grepped explicitly from `.env`, so they landed in the project being watched and appeared to
work. **The comparison was project-vs-project, never SDK-vs-SDK.**

Re-queried with the shell key, every supposedly-dropped trace is present:

| Trace | Name | Result |
|---|---|---|
| `c21d4a01d009` | `v4-probe2` | ✅ 200, 1 observation |
| `b09e74908fab` | `v4-probe3` | ✅ 200, 1 observation |
| `4edc914e7532` | `v4-spoofed-as-v3` | ✅ 200, 1 observation |
| `74ee0b8c373a` | `post-revert-healthcheck` | ✅ 200, 1 observation |

**Conclusion: Python SDK v4 ingests correctly against self-hosted Langfuse 3.221.1.** §5's
compatibility claim stands, and §5.3's reasoning about brand-promo/LibreChat — while still
an unverified inference — is no longer contradicted.

### The two "pre-existing bugs" in §11b are also retracted

- **`vector-rag` emits zero traces** — retracted. It ran under `docker compose run`, so its
  traces went to `claude-code`. Not a bug.
- **`text-to-sql` landed only 1 trace for 10 queries** — retracted for the same reason; the
  22:10 baseline trace that *did* appear in `demo-project` was the anomaly, not the rule.
- The `flush()` → `client.shutdown()` observation
  (`demos/vector-rag/langfuse_config.py:214-215`, `demos/text-to-sql/langfuse_config.py:226-227`)
  is still **real and still worth fixing** — `demos/agentic-rag/langfuse_config.py:147-152`
  explicitly warns against it — but it is not proven to drop traces and was not the cause here.
- The `job_configurations` count (6 code-eval, not 5) is unaffected and still a doc drift.

### Process lesson — this is the repo's #1 documented footgun

`AGENTS.md` and the troubleshoot skill both call out shell-exported `LANGFUSE_*` keys as the
top failure mode, and `demos/real-estate/agent/config.py:1-14` exists specifically to defend
against it. It still produced ~40 minutes of confident misdiagnosis.

**Mandatory for any future verification in this repo:**

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
```

…before running demos *or* queries, and **always confirm which project a key resolves to
before trusting a 404**:

```bash
curl -s -u "$PK:$SK" http://localhost:3001/api/public/projects
```

A 404 on `GET /api/public/traces/{id}` means "not in *this* project", never "does not exist".
That is the self-hosted twin of the Cloud trap in §2.1 (200 on an empty trace): **in both
cases the HTTP status alone is not evidence.**

## 11d. Phase 2 execution results — DONE, verified (2026-08-13)

Re-run after the §11c correction, with `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY
LANGFUSE_HOST LANGFUSE_BASE_URL` on **every** command so demo runs and verification queries
both resolve to `demo-project`.

### Changes

| File | Change |
|---|---|
| `demos/text-to-sql/requirements.txt:7` | `>=3.0.0,<4.0.0` → `>=4.7,<5.0` |
| `demos/vector-rag/requirements.txt:16` | same |
| `demos/agentic-rag/requirements.txt:20` | same |
| `test-scenarios/requirements.txt:2` | same |
| `demos/brand-promo-multi-agent/pyproject.toml:12` | `>=4.0,<5.0` → `>=4.7,<5.0` (floor alignment) |

**Zero code changes** — all four were already v4-clean. All four images rebuilt
(`BUILD3_EXIT=0`, no errors); every one reports `langfuse 4.14.4`.

### Verification — v4 SDK against self-hosted Langfuse **3.221.1**

**text-to-sql (V4/V5, the controlled comparison)** — clean v3 baseline vs v4, same project:

| | v3.15.0 baseline | v4.14.4 |
|---|---|---|
| LLM trace obs | 4 | **4** ✅ |
| observation names | `ChatAnthropic`, `ChatPromptTemplate`, `RunnableSequence`, `StrOutputParser` | **identical** ✅ |
| types | `CHAIN`, `GENERATION` | **identical** ✅ |
| scores per LLM trace | 7–8 | **7–8** ✅ |
| trace input/output | True | **True** ✅ |
| `retrieve-context` span trace | present | **present** ✅ |

**No span thinning.** v4's smart span filtering does not drop LangChain-generated spans,
confirming §3.3 item 5 empirically rather than by allow-list inspection.

**vector-rag** — 10 queries, exit 0, no SDK errors. Traces: `obs=4`, all four LangChain
names, 5 scores (`credential-leak`, `leak-type`, `output-present`, `response-length`,
`structure-clean`), trace I/O set. ✅

**agentic-rag** — the richest case, and it exercises the most v4-sensitive surface:
- imports clean under v4 (`langfuse_config`, LangGraph `graph`)
- traces carry `obs=10–27` across **all five observation types**: `AGENT`, `CHAIN`,
  `EVALUATOR`, `GENERATION`, `RETRIEVER`
- LangGraph node/edge spans intact: `route`, `retrieve`, `grade`, `generate`, `reflect`,
  `rewrite`, `_route_edge`, `_grade_edge`, `_reflect_edge`, `LangGraph`
- in-graph self-grades present: `retrieval_relevance`, `groundedness`
- **managed observation-level judges fired**: `faithfulness`, `context-relevance`,
  `answer-relevance` — the highest-risk item, since those evaluators filter on
  `isRootObservation` and observation type/name
- trace I/O set ✅

**test-scenarios** — exit 0, **95 traces** landed: all 40 scenario traces plus evaluator
executions (40 × `credential-leak-guard` code evaluator, 15 × `Correctness` LLM judge). ✅

**V8 — evaluators** — `job_configurations` unchanged at **6** `code-eval*` + **4**
`obs-eval*`, all `ACTIVE`, and demonstrably firing under v4 (scores present on every demo's
traces above). Server-side provisioning is untouched by the SDK bump, as predicted in §5.4.

### Net conclusion

**A single Langfuse Python SDK v4 (`>=4.7,<5.0`) now serves both backends** — Langfuse Cloud
(v4 server) and the self-hosted stack on **3.221.1** — with the server left entirely alone.
§5's central claim holds, verified end-to-end on both sides.

The one v4-server-only dependency in the repo remains `scripts/import-external-traces.py:150`
(the silent `api.observations` v1→v2 flip), which is **Phase 3**.

## 11e. Phase 3 execution results — DONE, verified (2026-08-13)

### Changes

| File | Change |
|---|---|
| `scripts/import-external-traces.py` `fetch_observations` | `api.observations.get_many(page=)` → **`api.legacy.observations_v1.get_many(page=)`**, with a comment explaining why v2 is wrong here |
| `scripts/import-external-traces.py` `create_source_client` | `host=` → **`base_url=`**, plus `tracing_enabled=False` |
| 8 inline pin strings | `langfuse>=3.0,<4.0` → `>=4.7,<5.0` in `import-external-traces.py:31,45`, `run-experiments.py:25,38`, `seed-datasets.py:25,37`, `seed-demo-data.sh:182` |

The extra `base_url=` fix was **not** in the original plan but is a genuine v4 correctness bug:
this script deliberately talks to **two** instances (`SOURCE_*` vs `TARGET_*`), and under v4 an
inherited `LANGFUSE_BASE_URL` outranks `host=` — which would silently repoint the *source* at
the wrong instance and import the wrong data. `tracing_enabled=False` was added because the
source client is read-only and should not spin up a span exporter.

### V6 verification (against self-hosted 3.221.1 as source, `--dry-run`)

- `create_source_client()` resolves `base_url` to `http://localhost:3001` ✅ (immune to env)
- `fetch_traces(..., limit=150)` → **150 traces**, i.e. **page 2 was traversed** — `page=`
  pagination and `meta.total_pages` both still work on the legacy v1 path ✅
- `fetch_observations(...)` → **10 observations, 10 of them carrying `input`/`output`** ✅ —
  the payload the importer exists to copy is intact. Types `AGENT`, `CHAIN`, `GENERATION`.
- Full `--dry-run --verbose --limit 150` run: **exit 0**, events transformed correctly.

### Counterfactual — the bug was real

The pre-fix call, executed against v4.14.3:

```
c.api.observations.get_many(trace_id=..., limit=100, page=1)
→ TypeError: ObservationsClient.get_many() got an unexpected keyword argument 'page'
```

Confirming §4.2's B3: the line's *text* never changes between v3 and v4, only its meaning —
so a grep-for-renames migration pass misses it entirely and the script breaks at runtime.

## 11f. Phase 4 execution results — DONE (2026-08-13)

Documentation sweep. Every stale "v3" assertion corrected, plus two findings that the sweep
itself turned up.

| File | Change |
|---|---|
| `CLAUDE.md:83` | "v3 patterns — `langfuse.trace()`/`trace.span()`/`trace.generation()`" (actually the **v2** API, matching no code in the repo) → v4 API surface, server-version note, and the `api.legacy.*` caveat |
| `CLAUDE.md` | **new** bullet on the shell-exported-keys footgun and the `unset` prophylactic (§11c) |
| `docs/LANGFUSE_INTEGRATION.md:288-306` | "SDK v3 Compatibility" → "SDK v4 Compatibility"; added `base_url=` vs `host=` precedence and the v2-endpoints caveat |
| `docs/LANGFUSE_SKILLS.md:45` | "(v3 API)" → "(v4 API)" |
| `README.md:379`, `docs/CODE_EVALUATORS.md:72` | pin hints → `langfuse>=4.7,<5.0` |
| `demos/{text-to-sql,vector-rag}/langfuse_config.py` | module + function docstrings "(v3 API)" → "(v4 SDK)" |
| `demos/agentic-rag/langfuse_config.py:2` | "(v3 SDK)" → "(v4 SDK)" |
| `demos/real-estate/scripts/prompt_gate.py:23-28` | no longer claims the demo can't use `langfuse/experiment-action`; states it is unblocked and why the hand-rolled gate is still kept |
| `demos/real-estate/cicd/README.md:137-144` | same correction |
| `demos/real-estate/.env.example:8-15` | **fixed the template/reality inversion** — shipped `http://localhost:3001` while the working config is Cloud. Now defaults to Cloud (which the CI gate requires) with self-hosted documented as the swap |

### Finding: the "5 code evaluators" doc drift is really an orphaned evaluator

Chasing the count mismatch found in V8 (docs say 5, DB has 6) turned up something worse than
a typo: **`code-eval-job-chain-gate-check` is an orphan.** It is `ACTIVE`, its `eval_templates`
row carries **2610 chars of `source_code`**, and it is demonstrably executing (`Execute
evaluator: chain-gate-check` traces appear alongside the legitimate five) — but:

- there is **no `evaluators/chain-gate-check.ts`** (only 5 `.ts` files exist)
- `grep -rn "chain-gate-check"` across the repo returns **zero** matches
- `git log --diff-filter=D -- 'evaluators/*.ts'` shows **no deleted evaluator file**

Root cause: the seeders `INSERT ... ON CONFLICT (id) DO UPDATE` and **never delete**. An
evaluator renamed or removed from `evaluators/` leaves a live ACTIVE row that keeps scoring
from DB-resident source that no longer exists in version control. For a customer-facing demo
that is a real integrity problem: a score is being produced by code nobody can read or change.

Docs updated to describe reality rather than paper over it — `AGENTS.md:104-107` and
`.agents/skills/troubleshoot/SKILL.md:66` now say **">= 5"**, explain why the count can
legitimately exceed the file count, and give the orphan-detection cross-check. **Fixing the
orphan itself is deliberately out of scope** and queued as its own task, since it is a
data/provisioning decision (restore the source vs delete the row) plus a possible
self-healing change to the seeder.

## 11g. Post-execution readiness report

Restates §11's seven rows after Phases 1–4. Supersedes the pre-execution assessment.

| Row | Status | Evidence |
|---|---|---|
| **Project access** | `ready` | Read+write verified on both backends. Cloud US (`real-estate`, project `cmrtgeanp0303ad0d6bjhud35`) and self-hosted `demo-project` via API + `docker exec langfuse-postgres`. **Caveat now understood:** a shell-exported key resolves to a *third* project (`claude-code`); §11c. |
| **SDK / instrumentation** | `changed` | All 3 breakages fixed, 14 pin sites moved to `>=4.7,<5.0` (6 declared + 8 inline). Verified on **both** backends: Cloud (§11a) and self-hosted 3.221.1 (§11d). Trace shape, metadata, scores and session grouping all unchanged; no span thinning. |
| **Trace evaluators** | `manual action` | **Now inventoried** (self-hosted `demo-project`): **4 ACTIVE** legacy trace/dataset-scoped rows — `Conciseness` (trace), `Conciseness` (dataset), `re-managed-helpfulness` (trace), `re-managed-relevance` (trace) — plus **6 INACTIVE** legacy rows already deactivated by `seed-llm-judge-evaluators.sh`. The two `re-managed-*` rows are `target_object='trace'` and **read trace-level I/O**, which is the concrete justification for keeping the deprecated `set_current_trace_io()` (§5.5, §7.1). They keep working; migrating them to observation-level is a **separate decision**, not a migration blocker. Cloud-side legacy rows not enumerated (no DB access; the unstable evaluation-rules API would be needed). |
| **Dataset evaluators** | `ready` | `experiment`-scoped rows: **6 ACTIVE**. `dataset.run_experiment(...)` exercised end-to-end under v4 by the CI gate over 19 dataset items with code evaluators *and* LLM judges, correctly failing a bad prompt (§11a V7). One legacy `dataset`-scoped `Conciseness` row remains ACTIVE (listed above). |
| **Direct APIs** | `changed` | The one real break (silent `api.observations` v1→v2) fixed and verified, including the counterfactual `TypeError` (§11e). Repo is otherwise v1 paths + v2 prompts, all valid on both servers. Deprecated-endpoint readers (`meta.totalItems`/`totalPages`, hand-built `/api/public/ingestion` envelopes, private `/api/admin/projects`) are **unchanged and still working** — they become relevant only on a server upgrade. |
| **Exports** | `ready` (self-hosted) / `blocked` (Cloud) | Self-hosted **verified empty**: `blob_storage_integrations`, `mixpanel_integrations`, `posthog_integrations`, `slack_integrations` all have **0 rows**, and the worker logs "No … integrations ready for sync" on every cycle. So there is nothing to dual-source and no downstream consumer to coordinate. Cloud integrations remain **unverified** — Project Settings → Integrations has no public API; needs a UI check. |
| **Verification / rollback** | `ready` | 9 checks executed across the four phases (V1–V9), all passing, each recorded with the trace ids and counts it was judged on. Rollback needs no data or server migration; `git revert` + rebuild. Pre-migration `pip freeze` (47 pkgs, `langfuse==3.15.0`) captured. |

### Honest gaps

1. **Cloud export integrations** — the only genuinely unverified row. Requires a human UI check.
2. **Cloud legacy evaluator rows** — self-hosted was enumerated from Postgres; Cloud was not.
3. **V7's literal exit code** — semantics verified, integer not captured (§11a caveat).
4. **`demos/real-estate/.venv` in the main checkout is still on `langfuse 3.15.0`.** Phase 1 was
   verified from a *worktree* venv. Before running that demo from the main checkout, **recreate**
   the venv (do not pip-install into it — its console scripts carry a stale pre-`demos/`-reorg
   shebang, so `./.venv/bin/pip` fails with "bad interpreter"; only `python -m pip` works).
5. **`demos/brand-promo-multi-agent`** got a pin-floor bump but was **not run** — it has no
   `.env` and its own `uv` environment. Out of the verified set.
6. **`demos/langfuse-rls`** (JS `langfuse@^3.36.0`) and **LibreChat** (bundled `@langfuse/* 5.7.0`)
   are untouched — separate version track, explicitly out of scope (§6).

## 12. Execution checklist

Ordered, for whoever runs this:

- [x] Capture real-estate baseline (7 observations, names, 4–5 scores, trace I/O, 1–3s latency)
- [x] `pip freeze` snapshot for rollback — 47 pkgs, `langfuse==3.15.0`
- [x] Inventory Legacy evaluator rows — self-hosted enumerated (4 ACTIVE, 6 INACTIVE); Cloud not enumerated (§11g)
- [x] Export integrations — self-hosted verified empty (0 rows in all 4 tables); Cloud still needs a UI check (§11g)
- [x] **Phase 1** (atomic): real-estate pin `>=4.7,<5.0` + `concierge.py` B1/B2 + `config.py`
      `base_url=` hardening (§7.4) + mirror comment; V1, V2, V3, V5, V7, V9 all ✅ (§11a)
- [x] **Phase 2**: 4 pin bumps + brand-promo floor alignment + `docker compose build`;
      V4, V5, V8 all ✅ across text-to-sql / vector-rag / agentic-rag / test-scenarios (§11d).
      First attempt was aborted on a misdiagnosis — see §11b (retracted) and §11c.
- [x] **Phase 3**: B3 (`api.legacy.observations_v1`) + `base_url=` fix + 8 inline pin strings; V6 ✅ (§11e)
- [x] **Phase 4**: docs sweep (§7.5) incl. the `.env.example` inversion — §11f
- [x] Restate the seven-row readiness report post-execution — §11g

### Open loose ends from Phase 1

- **A migrated portal is running on `:8081`** (worktree venv, v4). The original v3 portal is
  still on `:8080` (main checkout, pid 16161). Kill the test instance when done:
  ```
  lsof -ti :8081 | xargs kill
  ```
- The worktree has an untracked `demos/real-estate/.env` (copied from the main checkout so the
  migrated code could be exercised). It is gitignored and will not be committed.
- Nothing is committed yet — the Phase 1 diff is unstaged on
  `claude/langfuse-trace-delay-93fc38`.

### Not in this work (queued separately)

The two pre-existing bugs in §10.2 have been split out so they don't muddy the migration
diff — they are broken today on v3 and v4 does not change them:

- `client.score(...)` → `create_score(...)` in text-to-sql + vector-rag (5 sites)
- `usage=` → `usage_details=` in `test-scenarios/export_test_scenarios.py:1178`
