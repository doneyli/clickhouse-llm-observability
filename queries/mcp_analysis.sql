-- MCP Server tool usage
SELECT
    SpanName,
    count() AS calls,
    round(avg(Duration / 1e6), 2) AS avg_ms,
    countIf(StatusCode = 'ERROR') AS errors
FROM otel_traces
WHERE ServiceName = 'mcp-clickhouse'
  AND Timestamp > now() - INTERVAL 1 HOUR
GROUP BY SpanName
ORDER BY calls DESC
