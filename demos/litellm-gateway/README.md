# LiteLLM Gateway + Langfuse

This minimal demo sends an OpenAI-compatible chat request through a real
LiteLLM proxy and lets the proxy export the generation to Langfuse over OTLP.
The client has no Langfuse SDK: instrumentation is centralized at the gateway.

It reuses the repository's existing Anthropic key and self-hosted or cloud
Langfuse project. It does not reuse Text-to-SQL or RAG because their application
logic would hide the gateway behavior this demo is meant to show.

```text
Python client -> LiteLLM proxy -> Anthropic
                       |
                       +--------> Langfuse OTLP -> ClickHouse
```

## Run it

Deploy or start the base stack first:

```bash
./setup.sh
```

Then run the demo (the script starts only the LiteLLM service):

```bash
./demos/litellm-gateway/run_demo.sh
```

Pass a custom prompt if desired:

```bash
./demos/litellm-gateway/run_demo.sh "What are two benefits of an AI gateway?"
```

The command succeeds only after it receives an LLM response and finds the
corresponding unique session through the Langfuse API. Open your configured `LANGFUSE_BASE_URL`
(<http://localhost:3001> by default), then go to **Tracing > Traces** and filter
by the `litellm` tag. The trace includes the request and response, upstream
model, token usage, latency, session ID, and tags.

## Two instrumentation paths, one project

LiteLLM can be instrumented at the **proxy** or via the **SDK**. Both use the same
`langfuse_otel` callback and both export into the same Langfuse project here, so
they can be compared side by side. The architectural choice is *where the
instrumentation lives*, not which one produces better traces.

```text
proxy (run_demo.sh)      app --HTTP--> LiteLLM proxy --> provider
                                             `--> Langfuse    (gateway instruments)

sdk   (run_sdk_demo.sh)  app (litellm SDK) ----------> provider
                          `--> Langfuse                       (app instruments)
```

```bash
./demos/litellm-gateway/run_demo.sh       # proxy path — tagged path:proxy
./demos/litellm-gateway/run_sdk_demo.sh   # SDK path   — tagged path:sdk
```

Filter the Langfuse trace list by `path:proxy` vs `path:sdk` to show both.

| | Proxy | SDK |
|---|---|---|
| Instrumentation lives | once, at the gateway | in every application |
| Covers apps you don't own | yes | no |
| Client needs a Langfuse SDK | no | yes |
| Application-level context (business logic, nesting) | limited to what the request carries | full |
| Enforces policy centrally (keys, budgets, redaction) | yes | no |
| Language-agnostic | yes | Python only |

The usual answer for a platform team is **both**: the gateway guarantees a floor
of coverage that no team can forget to add, and the SDK adds depth inside the
services that need it.

`run_sdk_demo.sh` runs the SDK script inside the already-running `litellm-proxy`
container (a plain Python process — the proxy itself is not in the call path).
Spawning a second copy of the LiteLLM image costs ~900MB and gets OOM-killed on a
busy Docker host, which fails *silently* with exit status 137 and no output. Set
`SDK_DEMO_FORCE_NEW_CONTAINER=1` to use a throwaway container instead.

> **The SDK path needs an explicit settle-then-flush.** LiteLLM records the span
> from an asynchronous success callback and then batches it on a ~5s timer, so a
> short-lived script that returns and exits loses the trace *silently* — the
> response prints fine and Langfuse stays empty. `sdk_client.py` waits
> `SDK_DEMO_SETTLE_SECONDS` (default 6) and then force-flushes the live logger's
> `_tracer_provider`. Flushing without the wait drops the trace every time; this
> was verified against Langfuse Cloud, not assumed. The proxy path is immune
> because the proxy is a long-running process.

## Pointing this demo at Langfuse Cloud

The gateway demo can target a different Langfuse than the rest of the stack, so
you can run it against Cloud while the other demos stay self-hosted. Set in `.env`:

```bash
LITELLM_LANGFUSE_PUBLIC_KEY=pk-lf-...          # the target project's keys
LITELLM_LANGFUSE_SECRET_KEY=sk-lf-...
LITELLM_LANGFUSE_BASE_URL=https://us.cloud.langfuse.com    # host-side, for verification
LITELLM_LANGFUSE_INTERNAL_URL=https://us.cloud.langfuse.com # container-side, for export
```

Both URL variables are needed because they are resolved from different places:
`BASE_URL` is used by `client.py` on the host, `INTERNAL_URL` becomes
`LANGFUSE_HOST`/`LANGFUSE_OTEL_HOST` inside the container. They are identical for
Cloud and different for self-hosted (`http://localhost:3001` vs
`http://langfuse-web:3000`). Only this service is affected — text-to-sql,
vector-rag and LibreChat keep following `LANGFUSE_INTERNAL_URL`.

Then recreate the service so the new environment is applied:

```bash
docker compose --profile demo up -d --force-recreate litellm-proxy
```

## LiteLLM admin UI

The proxy ships an admin UI at <http://localhost:4000/ui/> — model list, request
logs, virtual keys, teams and budgets. Log in with username `admin` and the
`LITELLM_MASTER_KEY` (`sk-litellm-demo` by default).

The UI authenticates against LiteLLM's **own** Postgres database. Without
`DATABASE_URL` every login fails with `Authentication Error, Not connected to
DB!`, so the service points at a separate `litellm` database on the Langfuse
stack's Postgres (no Langfuse table is touched):

```bash
docker exec langfuse-postgres psql -U langfuse -d langfuse -c "CREATE DATABASE litellm"
```

Prisma migrations run on first boot and take ~30-60s before `:4000` binds, which
is why the healthcheck uses a long `start_period`. Override the connection with
`LITELLM_DATABASE_URL` if you would rather use a separate Postgres.

## What is configured

- `config.yaml` exposes one stable alias, `demo-model`, backed by
  `anthropic/claude-sonnet-4-6` by default.
- `callbacks: ["langfuse_otel"]` instruments successful and failed calls at the
  gateway boundary.
- `client.py` adds a unique request ID, a session ID, a readable
  generation name, and `litellm`, `gateway`, `gateway:litellm`, and `demo` tags.
- LiteLLM is pinned to the signed `v1.93.0` release instead of a moving image tag.

Optional `.env` overrides:

| Variable | Default | Purpose |
|---|---|---|
| `LITELLM_PORT` | `4000` | Host port for the gateway |
| `LITELLM_MASTER_KEY` | `sk-litellm-demo` | Demo gateway bearer token |
| `LITELLM_UPSTREAM_MODEL` | `anthropic/claude-sonnet-4-6` | LiteLLM provider/model route |
| `LITELLM_BASE_URL` | `http://localhost:4000` | Client-facing gateway URL |
| `LITELLM_LANGFUSE_PUBLIC_KEY` / `LITELLM_LANGFUSE_SECRET_KEY` | stack-wide keys | Optional Langfuse project dedicated to this gateway |

The committed master-key default is intentionally for local demonstration only;
set a strong secret for any shared environment. Prompt and completion content is
captured by default. For sensitive workloads, add
`turn_off_message_logging: true` under `litellm_settings` and apply your normal
redaction policy.

For a dedicated Langfuse project, tag taxonomy, verification details, and image
upgrade guidance, see the [gateway operations guide](../../docs/LITELLM_GATEWAY_DEMO.md).

Stop the gateway without removing Langfuse data:

```bash
docker compose --profile demo stop litellm-proxy
```

References: [Langfuse LiteLLM Proxy integration](https://langfuse.com/integrations/gateways/litellm),
[LiteLLM proxy health checks](https://docs.litellm.ai/docs/proxy/health).
