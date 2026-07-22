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
