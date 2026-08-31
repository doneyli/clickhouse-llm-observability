-- Fan-out distribution, straight from Langfuse's own ClickHouse.
-- "Your fan-out distribution is one GROUP BY away — because Langfuse stores
--  observations in ClickHouse." Same access pattern as dashboard/clickhouse_client.py.
--
-- Run against langfuse-clickhouse (the SAME server the investigator just diagnosed):
--   docker exec -i langfuse-clickhouse clickhouse-client \
--     --user langfuse --password langfuse123 \
--     --multiquery < demos/cluster-health-investigator/sql/worker_count_by_trace.sql
--
-- One row per investigation: how many workers the planner spawned, the trace's
-- total cost, and its wall-clock time — the cost/shape of dynamic decomposition,
-- made queryable.

SELECT
    t.id                                            AS trace_id,
    countIf(o.name = 'worker')                      AS worker_count,
    round(sum(o.total_cost), 6)                     AS trace_cost_usd,
    dateDiff('millisecond', min(o.start_time), max(o.end_time)) AS wall_time_ms,
    min(o.start_time)                               AS started
FROM observations AS o
INNER JOIN traces AS t ON o.trace_id = t.id
WHERE t.name = 'investigate-cluster-symptom'
  AND t.timestamp > now() - INTERVAL 1 DAY
GROUP BY t.id
ORDER BY worker_count DESC, started DESC
LIMIT 50;
