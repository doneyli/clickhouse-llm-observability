# LiteLLM Gateway — Demo Script (centralized observability at the AI gateway)

A minimal demo of the **gateway instrumentation pattern**: an OpenAI-compatible
request flows through a real **LiteLLM proxy**, and the *gateway* — not the
client — exports the call to **Langfuse** over OTLP. The client carries no
Langfuse SDK. Its signature beat: **move instrumentation off every app and onto
the boundary they all already call through.**

- **App:** a small Python client (`demos/litellm-gateway/client.py`) hitting the
  proxy's OpenAI-compatible `/v1/chat/completions`; the LiteLLM proxy runs as a
  container in the root `docker-compose.yaml`
- **Observability backend:** Langfuse (`http://localhost:3001`), trace name
  `litellm-gateway-demo`
- **Upstream model:** `anthropic/claude-sonnet-4-6` (via `LITELLM_UPSTREAM_MODEL`)
- **Run length:** ~5 min

> Config and instrumentation live in `demos/litellm-gateway/`; the gateway wiring
> is the `litellm-proxy` service in the root `docker-compose.yaml`. For the loop
> framing shared by all the demos, see
> [`../../AI_ENGINEERING_LOOP.md`](../../AI_ENGINEERING_LOOP.md). Ops/config
> reference: [`../../docs/LITELLM_GATEWAY_DEMO.md`](../../docs/LITELLM_GATEWAY_DEMO.md).

---

## How to run this script

Same shape as the other demo scripts: each act **frames** a problem, **shows** the
answer, **lands** the benefit, then hands a **question** back to the room.

Prep — the base stack running, then one command:

```bash
./setup.sh                          # once, if the stack isn't already up
./demos/litellm-gateway/run_demo.sh # starts only litellm-proxy, sends one request
```

It health-checks Langfuse, starts the proxy, sends a single request, and prints
the Langfuse trace it produced (with a session filter link).

---

## Act 1 — One request, fully traced, with zero SDK in the app

**Frame.** "Every team that calls an LLM needs the same three things — cost,
latency, and a record of what was sent. Today each app wires its own SDK to get
them. Multiply that across every service and every language and it's a tax nobody
owns."

**Show.** Run the demo. Point at `client.py`: it's a plain HTTP POST to an
OpenAI-compatible endpoint — **no Langfuse import anywhere.** The answer comes
back; the script then finds the trace in Langfuse by session id. Open the trace:
model, tokens, **cost**, latency, tags, and the user — all captured at the
gateway.

**Land.** "The app didn't know Langfuse exists. The gateway instrumented the call
for every request that passes through it — one place to own, every language for
free."

**Ask.** "How many of your services call the model directly today — and what
would it take to put them all behind one boundary?"

---

## Act 2 — The boundary is also where policy lives

**Frame.** "Once every call flows through one proxy, that proxy isn't just
tracing — it's the natural home for the controls you keep re-implementing."

**Show.** Point at `config.yaml`: one `callbacks: ["langfuse_otel"]` line turns on
tracing for *every* model and route the proxy serves. Name what else lives here in
production: model routing and fallbacks, rate limits, key management, budget
caps — all centralized, all visible in the same traces.

**Land.** "Centralizing the call path centralizes observability *and* governance.
You instrument and you enforce in one place, not N."

**Ask.** "If cost caps and audit logging lived at the gateway instead of in each
app, whose backlog does that clear?"

---

## Show me the code

- `client.py` — OpenAI-compatible request; **no Langfuse SDK**. Sets a session id
  and tags in `metadata`; the gateway maps them onto the trace.
- `config.yaml` — one model alias + `callbacks: ["langfuse_otel"]`; credentials
  come from the environment.
- `docker-compose.yaml` (`litellm-proxy`) — pinned image, `demo` profile, Langfuse
  credentials read from `.env` via `env_file` (shell-override-proof), OTLP host
  set to the Docker-internal Langfuse URL.
- `run_demo.sh` — health-checks Langfuse (honoring `DEPLOY_MODE`), starts the
  proxy, runs the client.
