# LiteLLM Gateway — Centralized observability at the AI gateway, where a LiteLLM proxy exports every call to Langfuse over OTLP, with no SDK in the client

A minimal demo of the gateway instrumentation pattern. A small Python client sends one
OpenAI-compatible chat request through a real LiteLLM proxy, and the proxy, not the
client, exports the call to Langfuse over OTLP. The client imports no Langfuse SDK.

It reuses the repo's Anthropic key and the stack's self-hosted or cloud Langfuse project.
It does not reuse Text-to-SQL or RAG, because their application logic would hide the
gateway behaviour this demo is meant to show. There is no company or data set: the only
input is one prompt.

**The question it answers:** how do we get cost, latency and a record of every LLM call
across all our services without adding an SDK to each one?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Gateway |
| **Stack** | Python · LiteLLM · OpenAI SDK · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Cloud or self-hosted · project `LLM Observability Demo` |
| **Length** | 5 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 5 minutes)
- **Operations guide:** [docs/LITELLM_GATEWAY_DEMO.md](../../docs/LITELLM_GATEWAY_DEMO.md), dedicated project, tag taxonomy and image upgrades

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Tracing at the gateway, no client SDK | `callbacks: ["langfuse_otel"]` in `config.yaml`; `client.py` is a plain HTTP POST |
| Session, user and tags carried in the request | `metadata` in `client.py` becomes the trace's session, user and tags |
| Proxy versus SDK instrumentation, side by side | `run_demo.sh` (tag `path:proxy`) and `run_sdk_demo.sh` (tag `path:sdk`) in one project |
| A gateway as the home for policy | The LiteLLM admin UI at `:4000/ui/`: models, keys, teams, budgets |

## Who it's for

Platform and infrastructure teams that run, or are choosing, an AI gateway and want
observability without asking every application team to instrument. Pick it when the
conversation is about coverage across many services or languages. For depth inside one
application (nested spans, prompts, scores), show an SDK-instrumented demo from the
[index](../README.md) instead, or after this one.

## What runs where

```text
proxy  (run_demo.sh)      client.py --HTTP--> litellm-proxy :4000 --> Anthropic
                                                   `--> Langfuse OTLP --> ClickHouse
sdk    (run_sdk_demo.sh)  sdk_client.py (litellm SDK) -----------> Anthropic
                            `--> Langfuse OTLP                (app instruments)
```

- `litellm-proxy` is a service in the root `docker-compose.yaml` (profile `demo`, image
  pinned to `ghcr.io/berriai/litellm:v1.93.0`, host port `LITELLM_PORT`, default 4000).
- `client.py` runs on the host. `sdk_client.py` runs inside the `litellm-proxy` container
  as a separate process; the proxy is not in its call path.
- **What lands in Langfuse:** proxy path, trace `litellm-gateway-demo` with a
  `litellm-gateway-completion` generation, session `litellm-gateway-<8 hex>` per run, user
  `gateway-demo-user`, tags `litellm`, `gateway`, `gateway:litellm`, `path:proxy`, `demo`.
  SDK path, trace `litellm-sdk-demo`, session `litellm-sdk-<8 hex>`, tags `litellm`,
  `sdk`, `path:sdk`, `demo`.

## Set up from scratch

Prerequisites: Docker, an Anthropic API key, and the root stack set up with `./setup.sh`.
Nothing needs seeding. From the repo root:

```bash
./setup.sh                          # the root stack and its .env (idempotent)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
./demos/litellm-gateway/run_demo.sh # starts litellm-proxy, one model call (one short Sonnet completion)
python3 demos/litellm-gateway/scripts/verify_demo.py   # read-only, no model calls; should print READY
```

The root `.env` is the only source of `LANGFUSE_*`. `verify_demo.py` drops inherited
`LANGFUSE_*` and `LITELLM_LANGFUSE_*` before it loads the root `.env`, and fails before
any API call when the file or a key is missing. `run_demo.sh` unsets shell-exported
`LANGFUSE_*` before it loads `.env`, and the proxy reads `.env` through `env_file`, so
every side uses the same project.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `./demos/litellm-gateway/run_demo.sh` | Pulls the image and boots the proxy once; a first boot with a database runs migrations for 30–60 s |
| 30 minutes before | `python3 demos/litellm-gateway/scripts/verify_demo.py --links` | Read-only: prints READY and the link each act opens |
| 5 minutes before | Open the links it printed, plus `config.yaml` in an editor | Act 1 needs the trace; Act 2 needs `config.yaml` and the admin UI |

`python3 demos/litellm-gateway/scripts/verify_demo.py` is the readiness check. It starts nothing and makes no model call: it confirms the
keys map to the expected project, the proxy answers its liveness probe, and a gateway run
from the last 7 days has observations (a run older than 24 hours only warns).
`run_demo.sh` is the live path: it starts the proxy and makes one model call per run
(one short Sonnet completion, `max_tokens` 120).

## Run it

```bash
./demos/litellm-gateway/run_demo.sh                                            # proxy path, default prompt
./demos/litellm-gateway/run_demo.sh "What are two benefits of an AI gateway?"  # your own prompt
./demos/litellm-gateway/run_sdk_demo.sh                                        # SDK path, for the comparison
```

`run_demo.sh` checks the API key, Docker and Langfuse health, starts `litellm-proxy`, sends
one request and then reads the run's session back from Langfuse. It succeeds only when the
gateway answered and the session's trace has at least one observation; it prints the
response, the request, session and trace ids, and which observations API it read.

```bash
python3 demos/litellm-gateway/scripts/verify_demo.py           # PASS / FAIL / WARN per check, then READY or NOT READY
python3 demos/litellm-gateway/scripts/verify_demo.py --links   # also the trace, session and admin UI links each act opens
```

**What you see in Langfuse:** open **Tracing > Traces** at your `LANGFUSE_BASE_URL`
(<http://localhost:3001> by default) and filter by the `litellm` tag, or by `path:proxy`
and `path:sdk` to compare the two paths. The trace carries the request and response, the
upstream model, token usage, cost, latency, session, user and tags.

## Proxy or SDK

Both paths use the same `langfuse_otel` callback and export into the same project. The
choice is where the instrumentation lives, not which one produces better traces.

| | Proxy | SDK |
|---|---|---|
| Instrumentation lives | once, at the gateway | in every application |
| Covers apps you don't own | yes | no |
| Client needs a Langfuse SDK | no | yes |
| Application-level context (business logic, nesting) | limited to what the request carries | full |
| Enforces policy centrally (keys, budgets, redaction) | yes | no |
| Language-agnostic | yes | Python only |

The usual answer for a platform team is both: the gateway guarantees a floor of coverage
that no team can forget, and the SDK adds depth inside the services that need it.

**The SDK path needs a settle, then a flush.** LiteLLM records the span from an
asynchronous success callback and batches it on a timer of about 5 s, so a short-lived
script that exits right away loses the trace while the response prints fine.
`sdk_client.py` waits `SDK_DEMO_SETTLE_SECONDS` (default 6), then force-flushes the
logger's tracer provider. The proxy is a long-running process, so it is not affected.

## LiteLLM admin UI

<http://localhost:4000/ui/>, user `admin`, password `LITELLM_MASTER_KEY`
(`sk-litellm-demo` by default): model list, request logs, virtual keys, teams and budgets.
The UI logs in against LiteLLM's own Postgres, which the service picks at startup:

| Situation | Database | Admin UI |
|---|---|---|
| `LITELLM_DATABASE_URL` set in `.env` | that one | works |
| Self-hosted, `langfuse-postgres` running | a separate `litellm` database on it, created by LiteLLM | works |
| Cloud mode, no local Postgres | none | login fails; proxying and tracing still work |

The database is never set unconditionally: the proxy exits at boot when `DATABASE_URL`
points at a Postgres it cannot reach.

## Configuration

All variables live in the root `.env` (template: the root `.env.example`).

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | blank | Upstream key the proxy uses; `run_demo.sh` fails without it |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from `./setup.sh` | Stack-wide keys, for the `LLM Observability Demo` project |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Host-side Langfuse, for the health check and the read-back |
| `LANGFUSE_INTERNAL_URL` | `http://langfuse-web:3000` | Where the container exports (cloud mode sets it to the cloud URL) |
| `LITELLM_PORT` | `4000` | Host port for the gateway |
| `LITELLM_BASE_URL` | `http://localhost:4000` | Client-facing gateway URL |
| `LITELLM_MASTER_KEY` | `sk-litellm-demo` | Gateway bearer token and admin UI password; local demos only |
| `LITELLM_UPSTREAM_MODEL` | `anthropic/claude-sonnet-4-6` | Provider and model behind the `demo-model` alias |
| `LITELLM_LANGFUSE_PUBLIC_KEY`, `LITELLM_LANGFUSE_SECRET_KEY` | stack-wide keys | A Langfuse project for this gateway only |
| `LITELLM_LANGFUSE_BASE_URL` | `LANGFUSE_BASE_URL` | Host-side URL of that project, for the read-back |
| `LITELLM_LANGFUSE_INTERNAL_URL` | `LANGFUSE_INTERNAL_URL` | Container-side URL of that project, for the export |
| `LITELLM_DATABASE_URL` | unset | Postgres for the admin UI (see above) |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` (`LLM Observability Demo`) | Project name `verify_demo.py` requires the keys to map to. Set it in the root `.env` when dedicated `LITELLM_LANGFUSE_*` keys or a Cloud project point elsewhere |
| `SDK_DEMO_SETTLE_SECONDS` | `6` | SDK path: seconds to wait before the flush |
| `SDK_DEMO_FORCE_NEW_CONTAINER` | unset | SDK path: run in a throwaway container instead of `litellm-proxy` |

To point only this gateway at Langfuse Cloud, set the four `LITELLM_LANGFUSE_*` values
(both URLs are the cloud URL), then recreate the service:

```bash
docker compose --profile demo up -d --force-recreate litellm-proxy  # applies the new environment
```

Prompt and completion content is captured by default. For sensitive workloads add
`turn_off_message_logging: true` under `litellm_settings` in `config.yaml`. The
[operations guide](../../docs/LITELLM_GATEWAY_DEMO.md) covers dedicated projects, the tag
taxonomy and image upgrades.

## Layout

```
demos/litellm-gateway/
├── demo.yaml          manifest: profile, coverage, commands (feeds the index)
├── README.md          this file
├── DEMO_SCRIPT.md     the talk track, act by act
├── config.yaml        LiteLLM proxy config: the demo-model alias and the langfuse_otel callback
├── run_demo.sh        proxy path: preflight, starts litellm-proxy, runs client.py (one model call)
├── client.py          one OpenAI-compatible request, then the Langfuse read-back (stdlib only)
├── scripts/
│   └── verify_demo.py readiness check: read-only, READY or NOT READY (stdlib only)
├── run_sdk_demo.sh    SDK path: runs sdk_client.py inside the litellm-proxy container
├── sdk_client.py      the same call through the litellm SDK, instrumented in-process
└── tests/
    ├── fake_langfuse.py   offline stand-in for the Langfuse API and the proxy probe
    ├── test_client.py     the request and the post-run read-back
    └── test_verify_demo.py the readiness check
```

The `litellm-proxy` service itself is defined in the root `docker-compose.yaml`.

## Tests

```bash
cd demos/litellm-gateway
python -m unittest discover -s tests -v   # no services, no model calls, under a second
```

They run against `tests/fake_langfuse.py`, an offline stand-in for the Langfuse API that
honours field projection, cursor and page traversal and rejects unknown query parameters.
`test_verify_demo.py` covers READY on a v4 server (Observations v2) and on the pinned v3
server (the v1 fallback, only after a v2 404), cursors across empty pages and a repeated
cursor, a missing or partial `.env` under hostile shell exports, empty, wrong and
unauthorised projects (including dedicated keys and cloud mode), a missing run, a stale
run, a non-404 read error, the proxy being down, and the verdict and exit codes.
`test_client.py` covers the request payload, the gateway call and the post-run read-back
on both servers. None of them uses legacy trace endpoints.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `FAIL  env loaded from .env` | No root `.env`: run `./setup.sh` |
| `FAIL  Langfuse keys and base URL set: …` | A key or `LANGFUSE_BASE_URL` is missing from the root `.env`, or only one of the two `LITELLM_LANGFUSE_*` keys is set |
| `FAIL  keys authenticate …: HTTP 401` | Wrong key pair, or the base URL points at another host or region |
| `FAIL  keys resolve to a project` | The API returned no project for these keys |
| `keys map to project '…' (want '…')` | The keys belong to another project. Fix the root `.env`, or set `DEMO_LANGFUSE_PROJECT` to the project the gateway exports to |
| `FAIL  litellm-proxy live at …` | The proxy is not running: `./demos/litellm-gateway/run_demo.sh` starts it |
| `FAIL  a gateway run (user gateway-demo-user) …` | No proxy-path run in the last 7 days: `./demos/litellm-gateway/run_demo.sh` |
| `Error: ANTHROPIC_API_KEY is not set` (run) | Add it to the root `.env` and rerun |
| `Error: Langfuse is not reachable at …` (run) | The stack is down, or `DEPLOY_MODE` and `LANGFUSE_BASE_URL` disagree. Run `./setup.sh` |
| `Error: LiteLLM did not become ready …` (run) | Read the proxy logs it prints. A first boot with a database runs migrations; rerun after a minute |
| `Demo failed: … returned HTTP 401` on the gateway call | `LITELLM_MASTER_KEY` changed since the container started: recreate `litellm-proxy` |
| `Demo failed: Langfuse did not return the … session` | The proxy exports somewhere else: check `LITELLM_LANGFUSE_INTERNAL_URL` and the keys in the container. Self-hosted: if traces stopped landing after a long uptime, `docker compose restart langfuse-worker` |
| Admin UI: `Authentication Error, Not connected to DB!` | No database for LiteLLM; see [LiteLLM admin UI](#litellm-admin-ui) |
| `run_sdk_demo.sh` exits 137 with no output | A second LiteLLM container was OOM-killed. Start the proxy first so the SDK path reuses it |
| SDK path prints a response but no trace appears | The flush ran before the async callback; raise `SDK_DEMO_SETTLE_SECONDS` |

## Known limits

- One model alias and one request per run. Routing, fallbacks, rate limits and budgets are
  named in Act 2 and visible in the admin UI, but `config.yaml` configures none of them.
- No scores, evaluators, datasets or prompts: this demo stops at tracing.
- The proxy only knows what the request carries. Business context and nested steps inside
  the app need the SDK path or an SDK-instrumented demo.
- `run_demo.sh` and `verify_demo.py` check the proxy path only. `run_sdk_demo.sh` prints a
  session id but does not read its trace back.
- The readiness check reads Observations v2. On the pinned self-hosted v3 server that
  answers 404, so it falls back to the v1 observations list, which has no session field:
  there the post-run check takes the newest trace of `gateway-demo-user` that started
  after the request, rather than matching the session id.
- The default master key and captured message content are for local demos only.
