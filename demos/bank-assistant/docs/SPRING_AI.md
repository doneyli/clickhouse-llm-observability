# Spring AI (Java) → Langfuse

**What this covers:** OBS-01 for Java teams. Spring AI emits OpenTelemetry spans
for model calls through Micrometer; Langfuse ingests them on its OTLP endpoint.
Not built into this demo (the demo agent is Python/LangGraph); this is the
verified recipe to show alongside it.

Source: [Integrating Langfuse with Spring AI using OpenTelemetry](https://langfuse.com/integrations/frameworks/spring-ai)
and the fully instrumented example app in
[langfuse-examples/applications/spring-ai-demo](https://github.com/langfuse/langfuse-examples/tree/main/applications/spring-ai-demo).

## The four pieces

1. **Dependencies** — Spring Boot Actuator, `micrometer-tracing-bridge-otel`,
   `opentelemetry-exporter-otlp` and `opentelemetry-spring-boot-starter`
   (exact Maven coordinates and BOM versions on the docs page).
2. **`application.yml`** — turn on prompt/completion capture for chat
   observations (`spring.ai.chat.observations.log-prompt: true`,
   `log-completion: true`), sampling probability, and suppress duplicate HTTP
   spans.
3. **An `ObservationFilter`** (`ChatModelCompletionContentObservationFilter` on
   the docs page) — required, otherwise generations arrive without input and
   output.
4. **Export to Langfuse** — standard OTel environment variables:

   ```bash
   export OTEL_EXPORTER_OTLP_ENDPOINT="https://us.cloud.langfuse.com/api/public/otel"   # or https://<langfuse-host>/api/public/otel
   export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64 pk:sk>,x-langfuse-ingestion-version=4"
   ```

## Bank-specific notes

- **Masking.** Prompt capture is off by default in Spring AI for privacy; when
  you turn it on, redact before export (an `ObservationFilter` can scrub the
  content attributes) or enforce server-side ingestion masking on a self-hosted
  Enterprise instance (`LANGFUSE_INGESTION_MASKING_CALLBACK_URL`).
- **Dynatrace at the same time.** The OTel Java autoconfigure exports to one
  OTLP endpoint; send to an OpenTelemetry Collector and fan out to Langfuse and
  Dynatrace (two `otlphttp` exporters). Keep AI payload attributes out of the
  Dynatrace pipeline with an `attributes`/`transform` processor. See
  [DYNATRACE.md](DYNATRACE.md).
- **Same data model.** Spring AI generations land as Langfuse generations
  (model, tokens, cost), so the same evaluators, dashboards and annotation
  queues apply as for the Python agent.
