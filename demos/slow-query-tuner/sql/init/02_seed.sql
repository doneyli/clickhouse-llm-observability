-- =============================================================================
-- Seed ~30M deterministic rows. No randomness anywhere, so result-set
-- signatures (sha256 of sorted rows) are STABLE across runs and machines — that
-- is what makes the equivalence probe reliable. Generates in seconds via
-- numbers(30e6); no data files are shipped in git.
--
-- event_date is derived from the SAME base date as ts_raw, so
--   toDate(parseDateTimeBestEffortOrNull(ts_raw)) == event_date
-- always holds — the redundant String-vs-Date sin the agent must discover, and
-- the reason the "use event_date" rewrite is result-equivalent.
-- =============================================================================

INSERT INTO tuning_lab.web_events
SELECT
    lower(hex(sipHash64(number)))                                        AS event_id,
    formatDateTime(toDateTime(d) + toIntervalSecond((number * 37) % 86400),
                   '%Y-%m-%dT%H:%i:%SZ')                                 AS ts_raw,
    d                                                                    AS event_date,
    arrayElement(['US','GB','DE','FR','ES','IT','NL','SE','PL','BR',
                  'CA','AU','JP','IN','MX','AR','CL','PT','IE','NO'],
                 toUInt32(number % 20) + 1)                              AS country,
    concat('/p/', toString(number % 5000))                               AS url,
    toUInt64(number % 500000)                                            AS user_id,
    toUInt32(50 + (number % 4000))                                       AS duration_ms,
    arrayElement(['direct','google','bing','twitter','newsletter'],
                 toUInt32(number % 5) + 1)                               AS referrer
FROM
(
    SELECT number, toDate('2024-01-01') + toIntervalDay(number % 365) AS d
    FROM numbers(30000000)
);
