-- =============================================================================
-- Tuning-lab schema — deliberately pessimal on purpose.
-- =============================================================================
-- Every modelling sin here is a tuning opportunity the agent can discover from
-- EXPLAIN + measured timings. The point of the demo is that the FIXES are not
-- known in advance: which of them apply (and in what order) depends on the query
-- and on what the live environment reveals.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS tuning_lab;

CREATE TABLE IF NOT EXISTS tuning_lab.web_events (
    event_id    String,          -- UUID-ish token as a plain String, first column
    ts_raw      String,          -- ISO-8601 timestamp stored as String (the cardinal sin)
    event_date  Date,            -- the redundant, PROPERLY typed column the agent should discover
    country     String,          -- ~20 values, NOT LowCardinality
    url         String,
    user_id     UInt64,
    duration_ms UInt32,
    referrer    String
) ENGINE = MergeTree
ORDER BY tuple();                -- no sort key: every query is a full scan until rewritten well
