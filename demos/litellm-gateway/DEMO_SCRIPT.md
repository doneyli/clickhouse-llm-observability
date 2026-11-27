# LiteLLM Gateway — Demo Script (centralized observability at the AI gateway)

A minimal demo of the **gateway instrumentation pattern**: an OpenAI-compatible
request flows through a real **LiteLLM proxy**, and the *gateway* — not the
client — exports the call to **Langfuse** over OTLP. The client carries no
Langfuse SDK. Its signature beat: **move instrumentation off every app and onto
the boundary they all already call through.**

- **App:** a small Python client (`demos/litellm-gateway/client.py`) hitting the
  proxy's OpenAI-compatible `/v1/chat/completions`; the LiteLLM proxy runs as the
  `litellm-proxy` container in the root `docker-compose.yaml`
- **Observability:** Langfuse self-hosted (`http://localhost:3001`) or Cloud, project
  `LLM Observability Demo` (or a dedicated one via `LITELLM_LANGFUSE_*`), trace
  `litellm-gateway-demo`
- **Models:** `anthropic/claude-sonnet-4-6` behind the `demo-model` alias (via
  `LITELLM_UPSTREAM_MODEL`)
- **Run length:** ~5 min; no short path

> Setup lives in [README.md](README.md). Ops/config reference:
> [`../../docs/LITELLM_GATEWAY_DEMO.md`](../../docs/LITELLM_GATEWAY_DEMO.md). For the loop
> framing shared by all the demos, see [`../../AI_ENGINEERING_LOOP.md`](../../AI_ENGINEERING_LOOP.md).

---

## How to run this script

Same shape as the other demo scripts: each act **frames** a problem, **shows** the
answer, **lands** the benefit, then hands a **question** back to the room. The
answers decide where the five minutes go. Run every act; there is no short path.

---

## 0 · Pre-flight

From the repo root, with the base stack up (`./setup.sh` once):

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
./demos/litellm-gateway/run_demo.sh                       # starts only litellm-proxy, one model call
python3 demos/litellm-gateway/scripts/verify_demo.py --links   # read-only: READY + the act links
```

`run_demo.sh` health-checks Langfuse, starts the proxy, sends a single request and reads
its trace back. `verify_demo.py` starts nothing and makes no model call; it prints
`READY` or `NOT READY`. Fix what a `FAIL` line names before the meeting.

**Tabs to open:** the trace and session links it printed, `config.yaml` and `client.py`
in an editor, and the LiteLLM admin UI (<http://localhost:4000/ui/>).

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| A fully traced call with no Langfuse SDK in the client | Act 1 |
| Session, user and tags set by the request, mapped by the gateway | Act 1 |
| One config line instruments every route the gateway serves | Act 2 |
| The gateway as the home for routing, keys and budgets | Act 2 (named, not configured) |

---

## Opening · Instrumentation nobody owns (1 min)

**Frame.** "Every team that calls an LLM needs the same three things — cost,
latency, and a record of what was sent. Today each app wires its own SDK to get
them. Multiply that across every service and every language and it's a tax nobody
owns."

**Ask.** "Who owns LLM cost and latency reporting today? Is there already a gateway or
proxy in front of your model calls?"

**Land.** "Let me show one request through a gateway, fully traced, with nothing added
to the app — then where else that boundary pays off."

---

## Act 1 · One request, fully traced, with zero SDK in the app (2 min)

**Frame.** "If instrumentation lives in each app, coverage depends on every team
remembering to add it and keep it current."

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

> **Fallback:** if the read-back times out, open **Tracing > Traces**, filter by the
> `litellm` tag and open the newest trace; ingestion is asynchronous.

---

## Act 2 · The boundary is also where policy lives (1 min)

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

> **Fallback:** if the admin UI login fails, stay in `config.yaml`; the UI needs
> LiteLLM's own Postgres, which cloud mode does not run.

---

## Close · One boundary, every call (1 min)

1. The gateway traced the call; the app carries no observability code.
2. One callback line covers every model and route behind the proxy.
3. Add an SDK only where you need depth inside one service — the gateway keeps the floor.

Hand over the repo: `demos/litellm-gateway` runs against their own Langfuse project with
the steps in the README, and `run_sdk_demo.sh` shows the SDK path side by side.

---

## Show me the code

**1 · No Langfuse SDK in the client — `client.py:30`**
`build_payload` builds a plain OpenAI-compatible request. Session, trace name and tags
travel in `metadata` (`client.py:57`), and the gateway maps them onto the trace.

**2 · The right user key — `client.py:54`**
LiteLLM's `langfuse_otel` callback maps `trace_user_id`, not `user_id`, to the trace user.
The wrong key is dropped without an error.

**3 · One line instruments the gateway — `config.yaml:10`**
`callbacks: ["langfuse_otel"]` covers every model and route the proxy serves; credentials
come from the environment.

**4 · The same callback, per app — `sdk_client.py:32`**
The SDK path declares the identical callback inside the application, which every app
would have to repeat.

**5 · The read-back counts observations — `client.py:164`**
`wait_for_trace` polls until the session's trace has at least one observation: an HTTP
200 with a trace row is not proof. It shares its reads with the readiness check
(`scripts/verify_demo.py:264`): Observations v2, and the v1 observations list only when v2
answers 404 on the pinned self-hosted v3 server (`scripts/verify_demo.py:276`).

---

## Talking points & objections

- **"We already have an SDK in our main service."** Keep it. The gateway gives the floor
  of coverage for everything else; the SDK adds nested steps where you need them. Both
  land in the same project (`path:proxy` and `path:sdk`).
- **"Doesn't a proxy add latency?"** It adds a network hop, and the export is
  asynchronous to the response. Measure it on your own traffic; this demo does not.
- **"What about sensitive prompts?"** Content is captured by default. Set
  `turn_off_message_logging: true` in `config.yaml` and apply your redaction policy at
  the same boundary.
- **"Does this only work with LiteLLM?"** The pattern is any gateway that exports OTLP;
  the `gateway:<name>` tag keeps several gateways comparable in one project.

---

## Known limits

- One request and one model alias: routing, fallbacks and budgets are named in Act 2, not
  configured.
- The trace holds what the request carries; business context inside the app needs the
  SDK path.
- No scores, evaluators or prompts: this demo stops at tracing.

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

Nothing is seeded, so there is nothing to reset. Each run makes a new session.

```bash
./demos/litellm-gateway/run_demo.sh                          # a fresh trace (one model call)
python3 demos/litellm-gateway/scripts/verify_demo.py         # READY again; read-only
docker compose --profile demo stop litellm-proxy             # stop the gateway; Langfuse data stays
```
