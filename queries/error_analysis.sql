-- Error rate and details
SELECT
    toStartOfHour(Timestamp) AS hour,
    ServiceName,
    countIf(StatusCode = 'OK') AS success,
    countIf(StatusCode = 'ERROR') AS errors,
    round(errors / (success + errors) * 100, 2) AS error_rate_pct
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
GROUP BY hour, ServiceName
HAVING errors > 0
ORDER BY hour DESC
