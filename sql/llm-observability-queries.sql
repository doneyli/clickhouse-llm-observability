-- ============================================================================
-- LLM Observability Dashboard - SQL Queries for ClickHouse
-- ============================================================================
-- Run against ClickStack's ClickHouse: clickhouse-client --user api --password api
-- ============================================================================

-- ============================================================================
-- SUMMARY METRICS (Single Values)
-- ============================================================================

-- 1. Total LLM Requests (24h)
SELECT count(*) as total_llm_requests
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != '';

-- 2. Total Input Tokens (24h)
SELECT sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as total_input_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.usage.input_tokens'] != '';

-- 3. Total Output Tokens (24h)
SELECT sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as total_output_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.usage.output_tokens'] != '';

-- 4. Total Cost Estimate (24h) - Approximate based on Claude pricing
SELECT
    round(sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) * 0.000003, 4) as input_cost_usd,
    round(sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) * 0.000015, 4) as output_cost_usd,
    round(
        sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) * 0.000003 +
        sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) * 0.000015
    , 4) as total_cost_usd
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != '';

-- 5. Average Latency (ms) for LLM calls
SELECT round(avg(Duration) / 1000000, 2) as avg_latency_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != '';

-- 6. P95 Latency (ms) for LLM calls
SELECT round(quantile(0.95)(Duration) / 1000000, 2) as p95_latency_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != '';

-- ============================================================================
-- TIME SERIES (For Charts)
-- ============================================================================

-- 7. Token Usage Over Time (hourly buckets)
SELECT
    toStartOfHour(Timestamp) as hour,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as input_tokens,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as output_tokens,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) +
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as total_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.usage.input_tokens'] != ''
GROUP BY hour
ORDER BY hour;

-- 8. LLM Requests Over Time (hourly buckets)
SELECT
    toStartOfHour(Timestamp) as hour,
    count(*) as request_count
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
GROUP BY hour
ORDER BY hour;

-- 9. Latency Over Time (hourly P50, P95, P99)
SELECT
    toStartOfHour(Timestamp) as hour,
    round(quantile(0.50)(Duration) / 1000000, 2) as p50_ms,
    round(quantile(0.95)(Duration) / 1000000, 2) as p95_ms,
    round(quantile(0.99)(Duration) / 1000000, 2) as p99_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
GROUP BY hour
ORDER BY hour;

-- ============================================================================
-- BREAKDOWN BY DIMENSIONS
-- ============================================================================

-- 10. Requests by Model
SELECT
    SpanAttributes['gen_ai.request.model'] as model,
    count(*) as request_count,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as input_tokens,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as output_tokens,
    round(avg(Duration) / 1000000, 2) as avg_latency_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
GROUP BY model
ORDER BY request_count DESC;

-- 11. Requests by Service
SELECT
    ServiceName as service,
    count(*) as request_count,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as input_tokens,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as output_tokens,
    round(avg(Duration) / 1000000, 2) as avg_latency_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
GROUP BY service
ORDER BY request_count DESC;

-- 12. Requests by Service Over Time
SELECT
    toStartOfHour(Timestamp) as hour,
    ServiceName as service,
    count(*) as request_count
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
GROUP BY hour, service
ORDER BY hour, request_count DESC;

-- ============================================================================
-- EVALUATION METRICS
-- ============================================================================

-- 13. Average Evaluation Scores (24h)
SELECT
    round(avg(toFloat64OrZero(SpanAttributes['eval.relevance_score'])), 3) as avg_relevance,
    round(avg(toFloat64OrZero(SpanAttributes['eval.coherence_score'])), 3) as avg_coherence,
    count(*) as total_evaluations
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['eval.relevance_score'] != '';

-- 14. Evaluation Scores Over Time
SELECT
    toStartOfHour(Timestamp) as hour,
    round(avg(toFloat64OrZero(SpanAttributes['eval.relevance_score'])), 3) as avg_relevance,
    round(avg(toFloat64OrZero(SpanAttributes['eval.coherence_score'])), 3) as avg_coherence,
    count(*) as eval_count
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['eval.relevance_score'] != ''
GROUP BY hour
ORDER BY hour;

-- 15. Evaluation Scores by Source Service
SELECT
    SpanAttributes['eval.source_service'] as source_service,
    round(avg(toFloat64OrZero(SpanAttributes['eval.relevance_score'])), 3) as avg_relevance,
    round(avg(toFloat64OrZero(SpanAttributes['eval.coherence_score'])), 3) as avg_coherence,
    count(*) as eval_count
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['eval.relevance_score'] != ''
GROUP BY source_service
ORDER BY eval_count DESC;

-- 16. Low-Quality Responses (Relevance < 0.5 or Coherence < 0.5)
SELECT
    Timestamp,
    SpanAttributes['eval.source_service'] as service,
    SpanAttributes['eval.source_model'] as model,
    toFloat64OrZero(SpanAttributes['eval.relevance_score']) as relevance,
    toFloat64OrZero(SpanAttributes['eval.coherence_score']) as coherence,
    substring(SpanAttributes['eval.input'], 1, 100) as prompt_preview
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['eval.relevance_score'] != ''
  AND (
    toFloat64OrZero(SpanAttributes['eval.relevance_score']) < 0.5 OR
    toFloat64OrZero(SpanAttributes['eval.coherence_score']) < 0.5
  )
ORDER BY Timestamp DESC
LIMIT 20;

-- ============================================================================
-- RECENT ACTIVITY
-- ============================================================================

-- 17. Recent LLM Calls (last 20)
SELECT
    Timestamp,
    ServiceName as service,
    SpanAttributes['gen_ai.request.model'] as model,
    toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens']) as input_tokens,
    toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens']) as output_tokens,
    round(Duration / 1000000, 2) as latency_ms,
    substring(SpanAttributes['gen_ai.prompt.0.content'], 1, 80) as prompt_preview
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
ORDER BY Timestamp DESC
LIMIT 20;

-- 18. Recent Prompts and Completions (full text)
SELECT
    Timestamp,
    ServiceName as service,
    SpanAttributes['gen_ai.request.model'] as model,
    SpanAttributes['gen_ai.prompt.0.content'] as prompt,
    SpanAttributes['gen_ai.completion.0.content'] as completion
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 1 HOUR
  AND SpanAttributes['gen_ai.prompt.0.content'] != ''
ORDER BY Timestamp DESC
LIMIT 10;

-- ============================================================================
-- SERVICE HEALTH
-- ============================================================================

-- 19. Service Summary (all services with LLM activity)
SELECT
    ServiceName as service,
    count(*) as total_requests,
    countIf(StatusCode = 'Error') as error_count,
    round(countIf(StatusCode = 'Error') * 100.0 / count(*), 2) as error_rate_pct,
    round(avg(Duration) / 1000000, 2) as avg_latency_ms,
    round(quantile(0.95)(Duration) / 1000000, 2) as p95_latency_ms,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as total_input_tokens,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as total_output_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != ''
GROUP BY service
ORDER BY total_requests DESC;

-- 20. Trace Count by Service (all traces, not just LLM)
SELECT
    ServiceName as service,
    count(*) as trace_count
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
GROUP BY service
ORDER BY trace_count DESC;

-- ============================================================================
-- QUICK DASHBOARD VIEW (Combined Summary)
-- ============================================================================

-- 21. Executive Summary Dashboard
SELECT
    '24h Summary' as period,
    count(*) as total_llm_calls,
    countDistinct(ServiceName) as active_services,
    countDistinct(SpanAttributes['gen_ai.request.model']) as models_used,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) as total_input_tokens,
    sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) as total_output_tokens,
    round(avg(Duration) / 1000000, 0) as avg_latency_ms,
    round(quantile(0.95)(Duration) / 1000000, 0) as p95_latency_ms,
    round(
        sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.input_tokens'])) * 0.000003 +
        sum(toFloat64OrZero(SpanAttributes['gen_ai.usage.output_tokens'])) * 0.000015
    , 2) as estimated_cost_usd
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.request.model'] != '';
