# n8n Complaint Triage

The **Complaint triage (Northwind)** workflow classifies a complaint, drafts a
reply, and records the workflow and LLM calls in one Langfuse trace.

## Set Up

Complete the [bank-demo quick start](../README.md#quick-start), including the
Langfuse target and Anthropic key. Run commands from `demos/bank-assistant`,
not `n8n/`.

```bash
./scripts/setup_n8n.sh
.venv/bin/python scripts/run_n8n_samples.py
```

The setup script configures OpenTelemetry, creates the owner account, installs
the official Langfuse prompt node, seeds the complaint prompt, imports
credentials, publishes the workflow, and restarts n8n. It can be rerun after a
key rotation or workflow edit.

Open [localhost:5678](http://localhost:5678) and log in with `N8N_OWNER_EMAIL`
and `N8N_OWNER_PASSWORD` from `.env`. Check that **Complaint triage (Northwind)**
is published. The sample runner sends six complaints, prints trace URLs, and
checks ingestion.

## Verify in Langfuse

1. Open a trace URL printed by the sample runner, or filter **Tracing** by
   `n8n-complaint-triage` or `channel:n8n`.
2. Confirm the tree contains `workflow.execute`, `node.execute`, `mask-pii`,
   `classify-complaint`, and `draft-reply`.
3. Open a generation. Check the model, tokens, cost, masked input, and output.
   The classification links to the managed `northwind-complaint-triage` prompt.
4. Use root metadata `n8n_execution_url` to open the matching n8n execution.
5. Open session `case-2044-overdraft` to compare the two complaint emails.
6. Run the PII sample and confirm that card numbers, IBANs, emails, and phone
   numbers are masked:

```bash
.venv/bin/python scripts/run_n8n_samples.py --only 5
```

## Call the Webhook

`POST /webhook/complaint-triage` accepts `customer_id`, `channel`, `text`,
and optional `session_id`. Send a W3C `traceparent` header to join n8n's native
spans with the workflow's LLM spans.

```bash
curl -fsS http://localhost:5678/webhook/complaint-triage \
  -H 'Content-Type: application/json' \
  -H "traceparent: 00-$(openssl rand -hex 16)-$(openssl rand -hex 8)-01" \
  -d '{"customer_id":"cust-1","channel":"web","text":"I was charged twice for my card fee"}'
```

The workflow fetches `northwind-complaint-triage` by its `production` label.
Its prompt config supplies the classification model, temperature, and token limit.
Save a new labelled version in Langfuse to change classification behavior.

## Tracing

```text
Webhook -> Trace context -> Get prompt -> Mask PII
  -> Classify complaint -> Parse classification -> Draft reply
  -> Build Langfuse trace -> Respond -> Send trace to Langfuse
```

| Source | Spans | Configuration |
|---|---|---|
| n8n native OpenTelemetry | Workflow and node timings, status, errors, and execution metadata | `N8N_OTEL_*` settings written to `.env` by setup |
| Workflow reporting nodes | Root `n8n-complaint-triage`, masking guardrail, and two generations with model usage | **Build Langfuse trace** creates OTLP/JSON; **Send trace to Langfuse** posts it after the response |

Both exporters use the selected bank-demo Langfuse target. Setup converts
`localhost:3100` to `langfuse-web:3000` for container access in self-hosted mode.
The official `@langfuse/n8n-nodes-langfuse` node handles prompt retrieval.

The root records customer and session IDs, `channel:n8n` and
`team:customer-care` tags, classification metadata, and the n8n execution link.
Generation spans are siblings of `workflow.execute`; their timings align on
the timeline. Native node names are in `attributes.n8n.node.name`.

## PII Masking

The **Mask PII** node runs before the model calls and telemetry reporting.
It masks Luhn-valid card numbers (keeps the last four digits), IBANs, emails,
and phone numbers. It does not mask names, postal addresses, or plain account
numbers. Production flows need the bank's DLP service or an NER-based masker.

n8n still saves the raw request in execution history. Review execution-data
retention and `saveDataSuccessExecution` before using real customer data.

## Limits and Recovery

- Without an inbound `traceparent`, n8n's native spans and the reported LLM
  spans appear in separate traces. Correlate them by execution ID.
- Native OpenTelemetry requires n8n 2.19 or later; the demo was verified on
  2.42.4. Older versions can still use workflow reporting without native node spans.
- Reporting nodes must be wired into each workflow. A shared sub-workflow can
  reduce duplication across a larger deployment.
- The demo traces editor runs as well as production runs. For queue mode,
  configure OpenTelemetry on the main instance and every worker.
- If the webhook is missing, rerun setup and inspect `docker compose logs n8n --tail 50`.
- If traces are missing, check setup's selected target and rerun the sample
  runner before inspecting `docker compose logs n8n --tail 50`.

## Files

- [complaint-triage.workflow.json](complaint-triage.workflow.json): exported workflow.
- [setup_n8n.sh](../scripts/setup_n8n.sh): provisioning and target configuration.
- [run_n8n_samples.py](../scripts/run_n8n_samples.py): sample requests and verification.
