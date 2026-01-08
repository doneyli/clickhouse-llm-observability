-- Token usage by service and operation
SELECT
    ServiceName,
    SpanAttributes['traceloop.association.properties.purpose'] AS operation,
    count() AS requests,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.prompt_tokens'])) AS input_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.completion_tokens'])) AS output_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.total_tokens'])) AS total_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.system'] != ''
GROUP BY ServiceName, operation
ORDER BY total_tokens DESC
