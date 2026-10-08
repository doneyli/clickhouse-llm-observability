# OBS-02 — End-to-end traceability for low-code (n8n)

A real n8n AI workflow, **Complaint triage (Northwind)**, traced in Langfuse: one
trace per run that holds n8n's own workflow and node spans, the two LLM calls
(model, tokens, cost, linked prompt version), a PII-masking step, the customer id,
a session id and tags.

```
POST /webhook/complaint-triage   {customer_id, channel, text, session_id?}
  │  traceparent: 00-<trace id>-<span id>-01      ← sent by the channel gateway
  ▼
Complaint webhook → Trace context → Get prompt (Langfuse) → Mask PII
  → Classify complaint (Anthropic, Haiku) → Parse classification
  → Draft reply (Anthropic, Sonnet) → Build Langfuse trace → Respond to webhook
  → Send trace to Langfuse
```

## How it is traced

Two sources write to the same trace. The W3C `traceparent` header joins them.

| Source | What it sends | How |
|---|---|---|
| **n8n built-in OpenTelemetry** (`N8N_OTEL_*`, set in `docker-compose.yml`) | `workflow.execute` span + one `node.execute` span per node: timings, status, errors, execution id, workflow id and version. No prompts, no tokens. | n8n exports OTLP/protobuf directly to `https://us.cloud.langfuse.com/api/public/otel`. n8n parents its `workflow.execute` span on the inbound `traceparent` header. |
| **The workflow itself** (nodes *Build Langfuse trace* + *Send trace to Langfuse*) | root observation `n8n-complaint-triage`, guardrail `mask-pii`, generations `classify-complaint` and `draft-reply` | A Code node turns the Anthropic responses (`model`, `usage`) into OTLP/JSON spans with Langfuse attributes. An HTTP Request node posts them to the same OTLP endpoint after the customer already has the answer. |

The trace in Langfuse looks like this (sample 1, measured on Cloud):

```
n8n-complaint-triage                  span        user cust-1001 · session · tags     6.9 s
├─ workflow.execute                   span        (n8n)  n8n.execution.id, status
│  ├─ node.execute  Complaint webhook             (n8n)  n8n.node.name in metadata
│  ├─ node.execute  Get prompt (Langfuse)         0.4 s
│  ├─ node.execute  Classify complaint            1.1 s
│  ├─ node.execute  Draft reply                   4.2 s
│  └─ … one per node (10)
├─ mask-pii                           guardrail   WARNING when something was masked
├─ classify-complaint                 generation  claude-haiku-4-5 · 342→77 tokens · $0.0007 · prompt northwind-complaint-triage v1
└─ draft-reply                        generation  claude-sonnet-4-6 · 238→166 tokens · $0.0032
```

- Trace name `n8n-complaint-triage`, `user_id` = `customer_id`, `session_id` = the request's
  `session_id`, or else `<channel>-<customer_id>-<date>`.
- Tags: `channel:n8n`, `team:customer-care`, `intake:<channel>`, and
  `regulatory-escalation` when the model sets that flag.
- Trace metadata (you can filter on it): `category`, `severity`, `regulatory_escalation`,
  `n8n_execution_id`, `n8n_execution_url` (this links back to the run in n8n).
- Langfuse computes cost from the model name and token counts. The classification
  generation links to the prompt version it used.

### Why this option

| Option | Result | Verdict |
|---|---|---|
| A. n8n native OTel → Langfuse OTLP alone | Works (verified). You get the workflow and node structure with timings and errors. You do not get model, tokens, cost, prompts or user. Every node span is named `node.execute`. | Keep it for the workflow skeleton, but it is not enough alone. |
| B. Community node `n8n-nodes-openai-langfuse` | Not installed. It supports OpenAI only. It uses the legacy `langfuse-langchain` v3 SDK, which writes to the deprecated `/api/public/ingestion` API. The last release was Sep 2025. Its traces are separate from the n8n spans, so the two never join. | Rejected. |
| C. Report the generations from the workflow (Code + HTTP Request → OTLP) | Works (verified). You get generations with model, tokens, cost, prompt link, user, session and tags. | **Chosen, together with A.** |
| OpenRouter Broadcast | It forces all LLM traffic through OpenRouter, and its traces are not joined to the n8n run. | Not suitable for a bank. |
| `n8n-langfuse-shipper` (Python, reads the n8n DB) | It is an extra service with database access, and it sends the data after the run finishes. | Not evaluated. |

**A + C** uses only OpenTelemetry, which the bank already runs, plus an n8n feature
that is built in and enabled with environment variables. It needs no custom package
inside n8n, and it keeps n8n's view and the LLM view in one trace. Prompt management
uses the **official** node `@langfuse/n8n-nodes-langfuse` (Get Prompt, label
`production`). The model, temperature and max_tokens come from the prompt's `config`.
To change the model or prompt, publish a new prompt version in Langfuse; the workflow
does not change.

## Run it

```bash
./scripts/up.sh                                   # platform (if not running)
./scripts/setup_n8n.sh                            # idempotent: OTel, owner, node, prompt, creds, workflow
./.venv/bin/python scripts/run_n8n_samples.py     # 6 complaints → prints Langfuse trace URLs, verifies ingestion
./.venv/bin/python scripts/run_n8n_samples.py --only 5   # the card-number sample
```

- n8n UI: http://localhost:5678. Log in with `N8N_OWNER_EMAIL` / `N8N_OWNER_PASSWORD` from `.env`.
- Langfuse target: the same one as the Python app (`northwind/config.py`). That is
  `.env.cloud` (Langfuse Cloud US) when present, otherwise the local stack.
- `setup_n8n.sh` writes `N8N_OTEL_ENABLED`, `N8N_OTEL_EXPORTER_OTLP_ENDPOINT` and
  `N8N_OTEL_EXPORTER_OTLP_HEADERS` (`Authorization=Basic …,x-langfuse-ingestion-version=4`)
  into `.env`. It then recreates the `n8n` service only.
- Credentials in n8n: **Langfuse (Northwind)** (`langfuseApi`, used by the prompt node and the
  OTLP POST) and **Anthropic (Northwind)** (`anthropicApi`). They are piped from the demo's `.env`
  files into n8n's encrypted store. The exported workflow references them by id and name only.

Manual call (any OTel-instrumented caller does the same automatically):

```bash
curl -s localhost:5678/webhook/complaint-triage -H 'content-type: application/json' \
  -H "traceparent: 00-$(openssl rand -hex 16)-$(openssl rand -hex 8)-01" \
  -d '{"customer_id":"cust-1","channel":"web","text":"I was charged twice for my card fee"}'
```

## What to click in Langfuse

1. **Tracing → Traces**, filter on name `n8n-complaint-triage` or tag `channel:n8n`. The list
   shows the user, session, tags, latency and cost of each run.
2. Open a trace. The tree shows n8n's `workflow.execute` with its nodes next to the
   generations. On the timeline, *Draft reply* takes most of the run.
3. Click `classify-complaint`. You see the system prompt from Langfuse, the masked user
   text, the JSON output, the model, tokens and cost, and the **prompt link**
   (`northwind-complaint-triage` v1).
4. Click `node.execute`. The metadata `attributes.n8n.node.name` and
   `n8n.node.items.input/output` are the n8n view. The root metadata
   `n8n_execution_url` opens the same run in n8n.
5. **Sessions** shows `case-2044-overdraft`: two e-mails from the same customer, where the
   second one escalates to the ombudsman.
6. **Prompt Management → northwind-complaint-triage**. Publish v2 with a different model in
   `config` and re-run the samples. The workflow picks it up with no change in n8n.
7. **Sample 5** (card number). In the trace, the PAN, IBAN, e-mail and phone are already
   masked, and `mask-pii` shows WARNING with the counts.

## PII masking: approach and limits

- The **Mask PII** Code node runs before the text reaches the model **and** before it reaches
  Langfuse. It masks card numbers (Luhn-checked, last 4 digits kept), IBANs, e-mails and
  phone numbers. Names, postal addresses and plain account numbers are **not** masked; sample 5
  shows this (*Maria Gonzalez*, *Account 12345678*). For production, use an NER-based service
  (for example Presidio) behind an HTTP node, or the bank's DLP API.
- n8n's native spans carry no payloads: only names, counts and status. In the Cloud project, a check of sample 5's 15 observations found no PAN, IBAN or e-mail.
- **n8n still stores the raw request in its execution history.** The workflow keeps it
  (`saveDataSuccessExecution: all`) so the demo can show executions. For real PII flows,
  turn that off or reduce retention (`EXECUTIONS_DATA_PRUNE` / `EXECUTIONS_DATA_MAX_AGE`).
- The Python app masks inside the Langfuse SDK (`mask=`). n8n has no SDK hook, so the
  workflow has to mask the text itself. A step the workflow author forgets is not masked.

## Limitations (honest list)

- **The join needs a `traceparent` from the caller.** Without it, the workflow still traces its
  root and generations, but n8n starts its own trace named `workflow.execute`. To find the
  two halves, use `n8n_execution_id` / `n8n.execution.id`.
- **n8n's spans are generic.** They are all named `workflow.execute` / `node.execute`, carry
  no user, session or tags, and the node name sits in metadata. To rename them and copy trace
  attributes in production, put an OpenTelemetry Collector between n8n and Langfuse
  (`transform` processor: `set(name, attributes["n8n.node.name"])`). That collector can
  also send the same spans to Dynatrace. This recipe is **not** set up or verified in this demo.
- **The generations are siblings of `workflow.execute`, not children of the node that called
  the model.** Expressions in the workflow cannot read n8n's node span ids. The timings
  still line up on the timeline.
- **The generation timings are taken in the Code nodes around each LLM node**, so they are
  accurate to within milliseconds, not exact. The reporting POST runs after the response,
  so the n8n workflow span ends about 0.5 s after the root observation.
- **The reporting is per workflow.** The Code node is plain JavaScript that can be copied
  between workflows, but a new workflow needs it wired in. For many workflows, put it in a
  sub-workflow called with *Execute Workflow*.
- **n8n's OpenTelemetry needs n8n ≥ 2.19** and is still labelled *preview* by n8n. Verified
  here on **n8n 2.42.4**. On older self-hosted n8n, option C still works on its own: root,
  generations, user, session and tags, but no node spans. In **queue mode**, set the
  `N8N_OTEL_*` variables on the main instance **and every worker**.
- `N8N_OTEL_TRACES_PRODUCTION_ONLY=false` is set so that test runs from the editor are traced
  too. Keep the default (`true`) in production.
- Not exercised here: **n8n Agents** (n8n ≥ 2.33) emit `gen_ai.*` agent and LLM spans through
  the same exporter (`N8N_AGENTS_TRACING_ENABLED`, on by default). For workflows built on
  n8n Agents, these would give generations without the reporting nodes. The classic
  LangChain *AI Agent* and *Chat Model* nodes do not emit them.

## Files

- `n8n/complaint-triage.workflow.json`: the exported workflow. It contains no secrets, and
  credentials are referenced by name. The OTLP URL is Langfuse Cloud US;
  `setup_n8n.sh` rewrites it for other targets.
- `scripts/setup_n8n.sh`: idempotent setup.
- `scripts/run_n8n_samples.py`: plays the channel gateway: sends 6 complaints, prints the trace
  URLs and checks that both halves of each trace arrived in Langfuse.
