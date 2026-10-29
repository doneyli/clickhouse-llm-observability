# Integrations — AgentCore, Dynatrace, model gateways, self-hosting

What the demo shows live, and the configuration that carries the same pattern to a carrier's platform.

## Agents on Amazon Bedrock AgentCore (configuration)

The demo's agents run locally; on AgentCore Runtime the same OpenTelemetry pipeline applies. Turn off the
runtime's built-in ADOT export and point the OTLP exporter at Langfuse (self-hosted or Cloud):

```bash
DISABLE_ADOT_OBSERVABILITY=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://<your-langfuse>/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64 pk:sk>,x-langfuse-ingestion-version=4"
```

Strands agents emit OpenTelemetry natively (`strands-agents` is on the Langfuse SDK's default export
allowlist); LangGraph/LangChain agents use the Langfuse callback handler. Reference:
[Amazon Bedrock AgentCore integration](https://langfuse.com/integrations/frameworks/amazon-agentcore).

## Agent-to-agent (A2A) and MCP across processes (live)

A call into another service carries W3C `traceparent` **and** `baggage` (claims/a2a_client.py for A2A
HTTP headers, MCP `_meta` for MCP tools). The receiving service extracts both before it creates spans
(doc_intake/service.py, claims/mcp_server.py). Without baggage, Langfuse v4 shows the downstream span as a
second root without the session. Never put tags in baggage.

## Dynatrace (live with Jaeger as the stand-in)

One TracerProvider per process, two span processors (claims/config.py):

- the Langfuse processor — AI spans, PII-masked;
- a batch processor to the APM, through an exporter that strips prompts, completions and claim data
  (`langfuse.observation.*`, `gen_ai.*` payload attributes and events) and adds the Langfuse trace URL.

The trace id is identical in both tools. For Dynatrace:

```bash
APM_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp/v1/traces
APM_OTLP_HEADERS=Authorization=Api-Token <token with openTelemetryTrace.ingest>
APM_UI_TRACE_URL=https://<env-id>.apps.dynatrace.com/ui/apps/dynatrace.distributedtracing/explorer?traceId={trace_id}
```

## Judges through Bedrock or an internal gateway (configuration)

Managed judges call a model through a project LLM connection. The demo uses the Anthropic API
(`scripts/seed_evals.py`); in a carrier VPC use the Bedrock adapter (VPC endpoint) or an
OpenAI-compatible adapter pointed at the internal LLM gateway. The judge's model must support the media
type for multi-modal judges (images of claim documents).

## Self-hosting on AWS

- Infrastructure: the official module [langfuse/langfuse-terraform-aws](https://github.com/langfuse/langfuse-terraform-aws)
  (EKS, Aurora Postgres, ElastiCache, S3, ClickHouse) — see cicd/terraform/main.tf.
- Enterprise features (project-level RBAC, protected prompt labels, audit logs, retention, ingestion
  masking, SCIM) are unlocked by the license key in the same containers as open source; the local demo
  instance (`docker compose --profile selfhosted`, `:3200`) runs the same images.
- Images can be mirrored and scanned through an internal registry, or built from source.
