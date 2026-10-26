# Cloud, self-hosted, or both: one shared local Langfuse and dual ingest

Applies to the three standalone customer demos: `demos/real-estate`, `demos/bank-assistant`
and `demos/insurance-claims`. The other demos keep their current single-target setup for now
(see [Follow-ups](#follow-ups)).

## What we want

1. **Every demo can target Langfuse Cloud, the local self-hosted Langfuse, or both.**
2. **One local self-hosted Langfuse for all demos.** If a healthy Langfuse v4 instance already runs
   on this machine, the demos use it. If none runs, one shared stack starts, and every demo points at
   it. Demos no longer bring their own Langfuse web, worker, Postgres, ClickHouse, Redis and MinIO.
3. **Dual ingest.** Every trace and score goes to Cloud *and* to the local instance, with the same
   ids, so both UIs can be shown side by side. **Cloud is the primary and must never depend on the
   local instance.** If the local instance is down at startup, or stops in the middle of a run,
   Cloud ingest goes on unchanged: no blocked requests, no exceptions, no slow exits.

## Where we start (2026-10-26)

| | Cloud | Self-hosted today | Both at once |
|---|---|---|---|
| real-estate | `.env` holds Cloud keys | swap the keys in `.env` | optional `LANGFUSE_MIRROR_*`: a plain OTLP exporter that skips the SDK's PII masking (so the demo refuses masking + mirror), plus synchronous score copies with a 10 s timeout |
| bank-assistant | `NORTHWIND_PROFILE=cloud` → `.env.cloud` | own Enterprise stack on :3100 (`northwind` compose project) | — |
| insurance-claims | `CLAIMS_PROFILE=cloud` → `.env.cloud` | own Enterprise stack on :3200 (`claims` compose project), plus a second MCP server and doc-intake service (:8768 / :9211) just for the self-hosted copy | — |

Langfuse instances running on this machine right now:

| URL | Version | Compose project | Usable by these demos? |
|---|---|---|---|
| http://localhost:3001 | 3.221.1 | `clickhouse-llm-observability` (repo root stack, LibreChat etc.) | **No**: it is a v3 server, so `/api/public/v2/observations`, `/experiments` and `/experiment-items` return 404 |
| http://localhost:3200 | 4.53.0 | `claims` | **Yes**, and it is the best candidate. (Its container healthcheck reports `unhealthy` even though the server works: Next.js binds the container's hostname and the check calls `localhost`.) |

The latest Langfuse release is v4.70.1 (published 2026-10-26).

## Design

### 1. One shared local instance

`scripts/langfuse_local.py` finds the local instance, starts it if needed, and gives each demo its
own project:

```bash
python scripts/langfuse_local.py status                 # instances found, versions, which demo points where
python scripts/langfuse_local.py up                     # reuse a healthy v4 instance, or start the shared one
python scripts/langfuse_local.py provision real-estate  # org + project + API key → demos/real-estate/.env.selfhosted
```

**How it finds an instance.** It lists running Docker containers whose image is `langfuse/langfuse`
(the web image, not the worker), reads each one's published port, and calls `/api/public/health`
for the version. An instance qualifies if it is healthy and on v4 or newer. The order of preference
is: an explicit `LANGFUSE_LOCAL_URL`, then the shared `langfuse-local` stack, then the newest
version. Skipped instances are printed with the reason (for example "v3 server"). An instance that
is older than the shared stack's pinned version is used, with a note saying how to upgrade.

**When none is running**, `up` starts `infra/langfuse-local/docker-compose.yml`, the one shared
stack:

- compose project `langfuse-local`, web UI on **:3300**, other ports off the defaults
- `LANGFUSE_VERSION` pinned to the latest release (4.70.1), and the same version for web and worker
- the Enterprise license is read from the repo-root `.env` at runtime, as the bank and claims
  bootstraps already do. Without it the stack still runs, with Enterprise features off.
- includes the two fixes the per-demo stacks found the hard way: `HOSTNAME=0.0.0.0`, so the
  healthcheck works, and `LANGFUSE_S3_MEDIA_UPLOAD_INTERNAL_ENDPOINT`, so multi-modal judges can
  download media

**How each demo gets a project.** `provision <demo>` makes sure the demo's organization and project
exist in the chosen instance and mints a project API key. It gives the instance's existing org
owners access to the new org, checks the key against `/api/public/projects`, and writes
`demos/<demo>/.env.selfhosted`, which holds `LANGFUSE_BASE_URL`, the keys and `LANGFUSE_PROJECT_ID`.
Running it again is safe: if the existing file's keys still work against the expected project, it
leaves them alone.

- Keys are minted with a Postgres insert: `docker exec` into the instance's own Postgres container,
  which it finds from the web container's compose labels and `DATABASE_URL`. This is the same method
  `demos/insurance-claims/scripts/seed_selfhosted_annuities.py` uses today. The supported API for
  this, the Instance Management API, is Enterprise-only and needs `ADMIN_API_KEY` set on the server.
  None of the existing instances set it, so it cannot be the only path.
- **Each demo gets its own organization** (`real-estate-demo`, `northwind-bank`, `northwind-life`).
  This keeps the bank and claims Enterprise stories intact and separate: RBAC users, one project per
  team or environment, protected labels, retention and audit logs.

**The per-demo Langfuse services are removed** from `demos/bank-assistant/docker-compose.yml` and
`demos/insurance-claims/docker-compose.yml`. Jaeger and n8n stay. Their `bootstrap_selfhosted.sh`
and RBAC/annuities seeders move to the shared instance: the URL and Postgres container come from
the provisioner instead of being hard-coded as `:3200` and `claims-postgres-1`. Stacks that are
already running keep their data and can still be found, so nothing has to be migrated.

### 2. Choosing a target: the same contract in all three demos

| File | Holds | Written by |
|---|---|---|
| `.env` (real-estate) / `.env.cloud` (bank, claims) | Cloud URL and keys, unchanged | you / `provision_cloud.py` |
| `.env.selfhosted` | local instance URL, keys, project id | `scripts/langfuse_local.py provision` |

Each demo keeps its existing profile setting and gains a third value:
`REAL_ESTATE_PROFILE`, `NORTHWIND_PROFILE` or `CLAIMS_PROFILE` = `cloud` | `selfhosted` | `dual`.

- **Default:** `dual` when both the Cloud file and `.env.selfhosted` exist, otherwise whichever one
  exists. Creating `.env.selfhosted` is the opt-in. CI runners never have one, so the prompt gates
  stay Cloud-only.
- `selfhosted` makes the local instance the primary. Prompts, datasets and experiments are then read
  from it, with full fidelity including media.
- real-estate's `LANGFUSE_MIRROR_*` is still read, as a deprecated alias for the mirror target.

### 3. How dual ingest works: `demos/_shared/langfuse_dual.py`

```
            app spans (LangGraph, LangChain, Strands, MCP, ours)
                                   │
                    one TracerProvider (W3C ids, one sampler)
       ┌───────────────────────────┼──────────────────────────────┐
LangfuseSpanProcessor       LangfuseSpanProcessor             BatchSpanProcessor
(the Langfuse client)       (mirror: same filter + mask)      (APM, payload-stripped)
       │                           │ GuardedExporter (3 s, circuit breaker)
       ▼                           ▼                              ▼
 Langfuse Cloud              local self-hosted                 Jaeger
```

- **There is only one Langfuse client, and it is bound to Cloud.** `get_client()`, the LangChain
  `CallbackHandler` and `@observe` all resolve to it. A second `Langfuse(...)` client is not an
  option: with two clients in one process, `get_client()` without a public key returns a *disabled*
  client, and the bank's CallbackHandler tracing would stop without any error.
- **The mirror is the SDK's own `LangfuseSpanProcessor`**, built only from its public constructor
  arguments:
  - `public_key` = the Cloud public key, which is used only for the project-scope filter, so it
    accepts the spans of the one client
  - `span_exporter` = an OTLP exporter for the local instance, with the local instance's keys and SDK
    headers
  - the demo's own `should_export_span` filter and `mask_otel_spans` hook

  So the local copy gets **the same spans, the same PII masking and the same trace and observation
  ids** as Cloud. This also lifts real-estate's "mirror or masking" restriction.
- **`GuardedExporter` keeps the local copy from slowing anything down:**
  - Each export has a 3 s deadline. The OTLP exporter's retries stay inside that deadline.
  - On a failure the circuit opens for 30 s. While it is open, batches are dropped without any
    network call. Then one batch is tried again, and a success closes the circuit. Each change
    between down and up is logged once.
  - A 401/403 gets its own "keys rejected" message and a longer cool-down.
  - All of this runs on the mirror processor's own worker thread. Cloud export never waits on it.
- **Startup probe.** At startup, `/api/public/health` is called with a 1.5 s timeout. If the local
  instance is down, the demo starts with the circuit open ("local Langfuse down, Cloud only") and
  switches to dual by itself once the instance comes up.
- **Scores.** `client.create_score` is wrapped on the one client. It assigns a `score_id` when the
  caller gave none, sends the score to Cloud as before, then queues a copy for the local instance.
  A background worker posts the copy to `POST /api/public/scores` with the same id, using the same
  circuit breaker, and skips traces the sampler dropped. This covers application scores, portal
  feedback and the evaluator scores from `dataset.run_experiment(...)`.
- **Flush and exit.** `flush()` flushes Cloud first, with no limit as today, then the mirror with a
  3 s limit. The atexit handler does the same, so a dead local instance adds at most about 3 s when
  the process exits.
- **Multi-process demos.** The bank and claims MCP servers and claims' Strands doc-intake service
  import the same `config`. Each process mirrors its own spans, and the trace joins up on both
  backends. The separate self-hosted copies of the claims processes (:8768 / :9211) are no longer
  needed for dual mode.

### 4. What lands where in dual mode

| | Cloud | Local instance |
|---|---|---|
| Traces, observations (live, portal, experiments) | ✓ | ✓ same ids |
| App scores, user feedback, experiment evaluator scores | ✓ | ✓ same score ids |
| Managed LLM judges | run in Cloud | run locally, once `seed_evals` has been run against the local instance |
| Prompts | served from here | prompt link works if the same name and version is seeded locally |
| Datasets and experiment runs | ✓ | experiment traces appear without the dataset-run link |
| Dashboards, annotation queues | per instance (seed scripts run per target) | per instance |
| Media (images, audio) | uploaded | sent inline in v1. A media manager for the mirror is in Phase 2 |
| APM copy (Jaeger / Dynatrace) | unchanged | unchanged |

### 5. When something fails

| Situation | What happens |
|---|---|
| Local instance down at startup | one warning line, Cloud only, retried every 30 s, recovers by itself |
| Local instance stops mid-run | at most one export attempt of up to 3 s every 30 s, on the mirror's thread. Cloud is unaffected and copied scores are dropped. |
| Local instance hangs (accepts the connection, never answers) | same, bounded by the 3 s deadline |
| Local keys rejected (401/403) | circuit opens with a "re-run provision" message |
| Cloud down | the same behaviour as today. The local copy keeps receiving data on its own. |
| `PROFILE=selfhosted` while the local instance is down | fails fast with "run `python scripts/langfuse_local.py up`" |

## Phases

**Phase 1: core**
- `demos/_shared/langfuse_dual.py`: mirror processor, `GuardedExporter`, score mirroring, bounded flush
- `scripts/langfuse_local.py` (`status`, `up`, `provision`) and `infra/langfuse-local/docker-compose.yml`
- each demo's `config.py`: profile with `dual`, `.env.selfhosted`, mirror attached when the profile
  is `dual`. Real-estate's `record_score` and mirror code go away.
- unit tests: circuit-breaker transitions, an open circuit drops without I/O, score payload mapping,
  profile resolution

**Phase 2: demo parity on the shared instance**
- a media manager for the mirror (images and audio show up locally with full fidelity)
- per-demo Langfuse services removed from the bank and claims compose files. Their bootstrap, RBAC
  and annuities seeders run against the shared instance.
- portals show both trace links in dual mode
- one command seeds prompts, judges, datasets and dashboards into both targets

**Checks (live)**
- dual mode: one turn per demo lands under the same trace id on Cloud and locally, with matching
  observation counts and scores on both
- stop the local web container, run a turn: the Cloud trace is complete, the turn takes no longer,
  and the process exits within 5 s. Start the container again: the next turn is copied again.

## Follow-ups

- The repo-root stack (:3001) stays on v3.221.1 for LibreChat and the other demos. Moving it to v4
  would let it serve as the shared instance too. That is a separate change.
- The other Python demos (agentic-rag, brand-promo, text-to-sql, ...) can use `langfuse_dual` the
  same way. grocery-assistant (TypeScript) would need a JS version.
- n8n (bank) exports straight to one OTLP endpoint. Sending it to both would need an OTel Collector
  with two exporters.
