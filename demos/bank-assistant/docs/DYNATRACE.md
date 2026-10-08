# Dynatrace correlation

**What this covers:** how the demo puts the same W3C trace id in Langfuse and in an APM, the Dynatrace endpoints and tokens to use (including the no-egress ActiveGate option), the collector alternative and its trap, monitoring Langfuse itself, and alerting and cross-linking.
**Capability IDs:** OBS-06 (APM correlation); supports OBS-04 (cross-process MCP traces), ENT-03 (no customer text to the APM), GATE-03, GATE-04.

Checked on 2026-10-07 against the Langfuse docs and Dynatrace's [OTLP export](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/getting-started/otlp-export) and [Events API v2](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/post-event) pages. Locally, **Jaeger stands in for Dynatrace** (`../docker-compose.yml`, UI `http://localhost:16686`, OTLP `http://localhost:14318/v1/traces`).

## 1. Division of labour

| Question | Tool |
|---|---|
| Is the service up? Where is the latency: DB, network, pod, dependency? Service map and infrastructure | **Dynatrace** |
| What did the model see and say? Which prompt version? Which documents and tools? Cost, tokens, quality scores, human review | **Langfuse** |
| One incident, both views | **Same trace id** in both, with a deep link each way |

## 2. How the demo does it: one TracerProvider, two processors

```
            app process (LangGraph agent, MCP client, our spans)
                              |
                 TracerProvider  (one W3C trace id per turn)
                 /                                   \
   LangfuseSpanProcessor                       BatchSpanProcessor(OTLP/HTTP)
   AI spans, PII-masked via mask_otel_spans    ALL spans, prompt/completion
   (SDK-side root and app-root handling)       attributes STRIPPED + langfuse.trace_url
                 |                                    |
             Langfuse                         Dynatrace (local demo: Jaeger)
```

The implementation is in `../northwind/config.py`:

- `tracer_provider()` creates the provider and attaches the **APM processor first**, wrapped in a payload-stripping exporter, then registers the provider globally.
- `get_langfuse()` creates the Langfuse client with `tracer_provider=provider`, `mask_otel_spans=` (see `../northwind/masking.py`), `sample_rate=`, `environment=` and `release=`. Langfuse adds its own span processor to the **same** provider, so both destinations receive identical trace and span ids.
- `flush()` flushes **both** exporters. Langfuse's `flush()` alone does not cover the APM processor, so short-lived scripts would lose the APM copy.
- The MCP server process (`../northwind/mcp_server.py`) uses the same pattern under a different `service.name`. Trace context travels in MCP `_meta`, so the APM service flow also shows agent → MCP server as one trace.

**What the APM copy loses.** These attributes are removed before export: `langfuse.observation.input|output|metadata`, `langfuse.trace.input|output|metadata`, `gen_ai.prompt|completion|input|output*`, `input.value`, `output.value`, `llm.input_messages`, `llm.output_messages`. Dynatrace receives operational telemetry only (names, timings, status, structure, resource attributes), so **no customer text reaches the APM**. This stripping is necessary because `mask_otel_spans` masks only the Langfuse copy, and a plain `BatchSpanProcessor` would otherwise ship raw prompts.

**Sampling caveat (UNVERIFIED):** the intended behaviour is that `LANGFUSE_SAMPLE_RATE` < 1 thins only the Langfuse copy while the APM keeps 100% of traces. Verify this with a provider passed in by the caller, as the demo does. A sampler set on the TracerProvider itself would apply to **both** destinations.

## 3. Dynatrace configuration

| Setting | Value |
|---|---|
| Endpoint, SaaS | `https://{env-id}.live.dynatrace.com/api/v2/otlp/v1/traces` |
| Endpoint, **Environment ActiveGate (the no-egress option)** | `https://{activegate-host}:9999/e/{env-id}/api/v2/otlp/v1/traces` |
| Auth header | `Authorization: Api-Token <token>`, a classic access token with scope **`openTelemetryTrace.ingest`** (a platform token uses `Authorization: Bearer <token>` with `openpipeline:traces:ingest`) |
| Protocol | **OTLP over HTTP with binary protobuf only.** No gRPC, no JSON. `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` |

Demo variables (`../.env.example`) and their Dynatrace values:

```bash
APM_OTLP_ENDPOINT=https://<activegate-host>:9999/e/<env-id>/api/v2/otlp/v1/traces   # full path: passed verbatim to OTLPSpanExporter
APM_OTLP_HEADERS=Authorization=Api-Token <token with openTelemetryTrace.ingest>
APM_UI_TRACE_URL=https://<env-id>.apps.dynatrace.com/ui/apps/dynatrace.distributedtracing/explorer?traceId={trace_id}
```

- `APM_OTLP_ENDPOINT` is used verbatim, so include `/v1/traces`. The generic `OTEL_EXPORTER_OTLP_ENDPOINT` env var takes the **base** (`…/api/v2/otlp`) and the SDK appends the signal path.
- `APM_UI_TRACE_URL` is the deep-link template the demo uses. **UNVERIFIED:** confirm the trace-explorer URL pattern for the bank's Dynatrace platform version.
- **ActiveGate placement:** run an Environment ActiveGate in the Langfuse VPC (or a shared services VPC). Apps and the platform talk only to it, and it reaches the Dynatrace tenant over the bank's existing approved path. If the ActiveGate uses a private CA, mount the CA bundle and set `OTEL_EXPORTER_OTLP_CERTIFICATE` (or pass `certificate_file=` to the exporter).
- **Attribute storage (UNVERIFIED):** depending on the Dynatrace version and settings, custom span attributes such as `langfuse.trace_url` may need to be allow-listed before they are stored and visible. Check this in the tenant.

## 4. Alternative: fan out in an OpenTelemetry Collector

Possible, but not recommended for the Langfuse leg:

```yaml
receivers:
  otlp:
    protocols: { http: { endpoint: 0.0.0.0:4318 } }
processors:
  batch: {}
  transform/strip_payloads:            # APM copy only
    trace_statements:
      - context: span
        statements:
          - delete_matching_keys(attributes, "^(langfuse\\.(observation|trace)\\.(input|output|metadata).*|gen_ai\\.(prompt|completion|input|output).*|input\\.value|output\\.value|llm\\.(input|output)_messages.*)")
exporters:
  otlphttp/langfuse:
    endpoint: https://langfuse.<internal-domain>/api/public/otel     # exporter appends /v1/traces
    headers: { Authorization: "Basic ${env:LANGFUSE_BASIC_AUTH}", x-langfuse-ingestion-version: "4" }
  otlphttp/dynatrace:
    endpoint: https://<activegate-host>:9999/e/<env-id>/api/v2/otlp
    headers: { Authorization: "Api-Token ${env:DT_API_TOKEN}" }
service:
  pipelines:
    traces/langfuse:  { receivers: [otlp], processors: [batch], exporters: [otlphttp/langfuse] }
    traces/dynatrace: { receivers: [otlp], processors: [transform/strip_payloads, batch], exporters: [otlphttp/dynatrace] }
```

**The trap:** teams then add a `filter` processor so that only AI spans go to Langfuse. Langfuse needs a root for each trace, and its docs state a root span must be sent ([OTel](https://langfuse.com/integrations/native/opentelemetry)). The Python SDK (≥ 4.7) handles a non-exported parent by marking the first exported span as the application root. **Nothing sets that marker when filtering happens in a collector.** Trace-level input/output, root-observation judges and the one-row-per-trace view then break. Masking also moves out of the app, so unmasked PII crosses the network to the collector. **Prefer SDK-side export** as in section 2, and use a collector only for the APM leg if the bank's standards require one.

## 5. Monitoring Langfuse itself in Dynatrace

| Signal | Configuration |
|---|---|
| Traces of web and worker | `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.<ns>:4318` (path `/v1/traces` is appended), `OTEL_SERVICE_NAME=langfuse-web` / `langfuse-worker`, `OTEL_TRACE_SAMPLING_RATIO=0.1` ([docs](https://langfuse.com/self-hosting/configuration/observability)). The collector adds the Dynatrace `Api-Token` header and forwards to the ActiveGate. Langfuse documents only these three variables, so whether it honours `OTEL_EXPORTER_OTLP_HEADERS` directly is **UNVERIFIED**. Hence the collector hop |
| Logs | `LANGFUSE_LOG_FORMAT=json`, shipped by the bank's log agent (OneAgent or Fluent Bit) |
| Queue metrics | StatsD `langfuse.queue.ingestion.depth` (waiting), or CloudWatch (`ENABLE_AWS_CLOUDWATCH_METRIC_PUBLISHING=true`) through the Dynatrace AWS integration |
| Availability | Synthetic HTTP monitors on `/api/public/health?failIfDatabaseUnavailable=true` (web) and `:3030/api/health?failIfQueueConsumptionStuck=true` (worker) |

## 6. Alerting: Langfuse alerts → Dynatrace

Langfuse **alerts (self-hosted v4+, OSS, no limit)** evaluate thresholds on observations or scores, then fire **automations** to Slack, a webhook or GitHub Actions ([alerts](https://langfuse.com/docs/observability/features/alerts)).

1. Create an automation (**Alert → Webhook**) pointing to a small in-cluster bridge on port 443. Add the bridge host to `LANGFUSE_WEBHOOK_WHITELISTED_HOST`, because internal IPs are blocked by default and webhooks may only target ports 80/443.
2. Create alerts and link them to the automation. Examples: p95 latency of `generation` > X s; error-level observations > N/h; average `groundedness` < threshold; `pii-leak` true-rate > 0; cost per hour > budget.
3. The bridge verifies the HMAC signature (`x-langfuse-signature`), then calls the Dynatrace Events API v2:

```http
POST https://<activegate-host>:9999/e/<env-id>/api/v2/events/ingest
Authorization: Api-Token <token with events.ingest>
Content-Type: application/json

{
  "eventType": "CUSTOM_ALERT",
  "title": "[Langfuse] avg groundedness below threshold - retail-assistant",
  "entitySelector": "type(SERVICE),entityName.equals(\"northwind-assistant\")",
  "timeout": 60,
  "properties": {
    "langfuse.severity": "ALERT",
    "langfuse.permalink": "<payload.permalink>",
    "langfuse.project_id": "<payload.projectId>",
    "langfuse.window": "<payload.window>",
    "message": "<payload.message.body>"
  }
}
```

Map `ALERT` to `CUSTOM_ALERT`, which typically opens a Dynatrace problem; confirm that the bank's alerting profile routes it to the incident tool (ServiceNow, PagerDuty, ...). Map recovery (`OK`) to `CUSTOM_INFO`. Without `entitySelector`, the event attaches to the environment entity. Langfuse disables an automation's trigger after **5 consecutive delivery failures**, so monitor the bridge.

## 7. Cross-linking

| Direction | Mechanism |
|---|---|
| Dynatrace → Langfuse | The APM copy of every span carries `langfuse.trace_url` = `{LANGFUSE_BASE_URL}/project/{projectId}/traces/{traceId}` (added by the stripping exporter in `../northwind/config.py`). Without it, copy the trace id and filter by `trace_id` in Langfuse |
| Langfuse → Dynatrace | Langfuse has **no native external-link feature**. The demo writes the APM deep link into root-observation metadata (`apm_trace_url`; see `../northwind/agent.py` and `../northwind/voice.py`). Metadata URLs are not clickable, so the pattern adds a **markdown comment** on the trace (`POST /api/public/comments`), which renders as a link. At the time of writing, the metadata write is in the demo code and the comment write is not yet |

## 8. Troubleshooting the integration

| Symptom | Likely cause | Fix |
|---|---|---|
| Nothing arrives in Dynatrace, HTTP 401/403 in exporter logs | Token is missing `openTelemetryTrace.ingest`, or the `Api-Token` prefix is wrong | Re-issue the token with the scope. Header format `Authorization=Api-Token <token>` in `APM_OTLP_HEADERS` |
| HTTP 404 / 415 from Dynatrace | Path is missing `/v1/traces`, or the exporter sends JSON or gRPC | Use the full path with `opentelemetry-exporter-otlp-proto-http` (protobuf), as `../requirements.txt` does |
| TLS errors to the ActiveGate | Private CA not trusted | Mount the CA bundle and set `OTEL_EXPORTER_OTLP_CERTIFICATE` |
| Different trace ids in the two tools | Two TracerProviders: the Langfuse client was created before the shared provider, or another library replaced the global one | Create the provider first and pass `tracer_provider=` to `Langfuse(...)` (the `../northwind/config.py` order) |
| Short scripts show spans in Langfuse but not in the APM | Only the Langfuse client was flushed | Call `config.flush()`, which flushes the provider too |
| Prompts visible in the APM | A second exporter was added without the stripping wrapper, or new attribute names appeared (a new instrumentation library) | Route every non-Langfuse exporter through the payload-free wrapper. Extend `_PAYLOAD_PREFIXES` |
| Orphaned top-level spans in Langfuse | A parent span was filtered out (custom `should_export_span`) | Let the root through, or keep the default filter ([FAQ](https://langfuse.com/faq/all/existing-otel-setup)) |

## 9. Verify locally (5 minutes)

1. `../scripts/up.sh`, then run one assistant turn. The result returns `trace_id`, `trace_url` and `apm_url`.
2. Open `apm_url` (Jaeger) and check the same trace id, the agent and MCP server services, and **no** prompt or completion attributes on any span.
3. Open `trace_url` (Langfuse) and check the same trace id, masked PII, and `apm_trace_url` in the metadata.
4. To switch to Dynatrace, change the three `APM_*` variables. No code changes.
