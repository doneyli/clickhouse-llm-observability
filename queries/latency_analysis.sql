-- Latency percentiles by operation
SELECT
    ServiceName,
    SpanName,
    count() AS requests,
    round(quantile(0.50)(Duration / 1e6), 2) AS p50_ms,
    round(quantile(0.95)(Duration / 1e6), 2) AS p95_ms,
    round(quantile(0.99)(Duration / 1e6), 2) AS p99_ms,
    round(max(Duration / 1e6), 2) AS max_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 1 HOUR
GROUP BY ServiceName, SpanName
ORDER BY p50_ms DESC
LIMIT 20
