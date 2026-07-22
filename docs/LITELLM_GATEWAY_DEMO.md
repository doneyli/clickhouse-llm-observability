# LiteLLM Gateway Demo Operations

This demo shows LiteLLM acting as an OpenAI-compatible AI gateway while
Langfuse captures the gateway's model calls through its native OTEL callback.
The application client does not need a Langfuse SDK; the gateway is the
instrumentation boundary.

For the runnable example and request options, see the
[LiteLLM gateway demo README](../demos/litellm-gateway/README.md).

## Project and credential scoping

The stack-wide `LANGFUSE_*` variables remain the default for LibreChat and the
existing demos. To send only LiteLLM gateway traces to a new Langfuse project,
put that project's credentials in the local, uncommitted `.env` file:

```bash
LITELLM_LANGFUSE_PUBLIC_KEY=...
LITELLM_LANGFUSE_SECRET_KEY=...
LITELLM_LANGFUSE_BASE_URL=http://localhost:3001
```

`litellm-proxy` prefers these `LITELLM_LANGFUSE_*` values and otherwise falls
back to the stack-wide credentials. This lets a future gateway use its own
project without changing either the existing demos or the LiteLLM project.

For a self-hosted deployment, the proxy exports OTEL data to the Docker-internal
Langfuse address (`LANGFUSE_INTERNAL_URL` or `http://langfuse-web:3000`). The
client uses `LITELLM_LANGFUSE_BASE_URL` only to verify the completed trace via
the public API.

After changing the LiteLLM project credentials, recreate only the proxy:

```bash
docker compose --profile demo up -d --force-recreate litellm-proxy
```

Never commit `.env` or project API keys. Use [`.env.example`](../.env.example)
as the safe configuration template.

## Trace tags and filtering

Every request carries this tag taxonomy:

| Tag | Meaning |
|---|---|
| `gateway:litellm` | Canonical gateway implementation filter |
| `gateway` | Broad filter across all gateways |
| `litellm` | LiteLLM integration filter |
| `demo` | Repository demo traffic |

In Langfuse, open **Tracing → Traces** in the LiteLLM project and filter by
`gateway:litellm`. Use that tag as the stable comparison point when another
gateway is added; give the new implementation its own `gateway:<name>` tag
while retaining the shared `gateway` tag.

## Run and verify

Start the base stack, then issue one request and verify the resulting trace:

```bash
./setup.sh
./demos/litellm-gateway/run_demo.sh "What are two benefits of an AI gateway?"
```

The runner waits for LiteLLM health, sends an authenticated OpenAI-compatible
chat-completions request, and queries Langfuse for the unique session it
created. A successful result reports the trace ID, session ID, and tags.

## Image versioning note

Langfuse web and worker images are pinned together in `docker-compose.yaml`.
This avoids a stale local floating `:3` image being started against a newer
ClickHouse migration state. When upgrading Langfuse, update both image tags as
one compatible release, then recreate the affected services.
