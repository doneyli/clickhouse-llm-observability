-- Analytics at Agentic Speed: US domestic flights ("ontime"), loaded by scripts/load_data.py.
--
-- Source: the original BTS monthly files in the public bucket
-- (s3://clickhouse-public-datasets/ontime/original/*.zip), 1987-10 .. 2026-08, minus
-- 2025-08 .. 2026-02 (those seven "zips" are HTML error pages). Only the 35 columns
-- the scenarios use are kept. Cardinalities and value ranges below were measured on
-- the playground's ontime.ontime, which holds the same BTS data.
--
-- Design, checked against the clickhouse-best-practices rules:
--
--   ORDER BY (Origin, Year, Month, Reporting_Airline, FlightDate)
--   * schema-pk-plan-before-creation: the key is immutable, so it was chosen from the
--     scenario templates, not from the source table. Nearly every template is a
--     per-hub sweep ("10 busiest hubs x buffers x departure blocks, 2015 onward"),
--     then per-carrier and per-month cuts.
--   * schema-pk-prioritize-filters / schema-pk-filter-on-orderby: Origin is the filter
--     in almost every scenario, so it leads. A hub query then reads a few contiguous
--     ranges instead of every row in the date range (the playground sorts by date
--     first, so the same query scans everything).
--   * schema-pk-cardinality-order: measured distinct values are Origin 438, Year 40,
--     Month 12, Reporting_Airline 34, FlightDate ~14K. That is the rule's own pattern:
--     a low-cardinality category first, then the coarse date parts, then the
--     highest-cardinality column (FlightDate) last. Five columns, within the 4-5 the
--     rule suggests.
--
--   Types
--   * schema-types-lowcardinality: airport codes (438), states (54), city names (430),
--     carrier (34), flight numbers (8,290), time blocks (20) and CancellationCode (5)
--     are all under 10K distinct values -> LowCardinality(String). Tail_Number has
--     20,087 distinct values, over the rule's 10K line, and no scenario filters on it,
--     so it stays a plain String.
--   * schema-types-lowcardinality (LowCardinality vs FixedString): the playground keeps
--     Origin/Dest as FixedString(5), which pads 'ATL' with NUL bytes (so does the
--     csv_by_year copy). The loader strips NULs; nothing here is FixedString.
--   * schema-types-minimize-bitwidth: numeric widths come from measured min/max
--     (DepDelay -1410..4413, ArrDelay -1437..4405, AirTime -2378..1557, Distance
--     0..5812, TaxiOut 0..3905 -> Int16; 0/1 flags and DistanceGroup 1..11 -> Int8).
--     Signedness follows the source (delays are negative), so arithmetic an agent
--     writes, e.g. ArrDelay <= 15 + {buffer:UInt8}, behaves the obvious way.
--   * schema-types-avoid-nullable: no Nullable. The source has no NULLs; a delay cause
--     that was not reported (all of them before mid-2003) is 0, not NULL.
--   * schema-types-native-types: FlightDate is a Date, year/month parts are UInt8/16.
--
--   Partitioning: none.
--   * schema-partition-start-without / schema-partition-lifecycle: this is a static,
--     historical dataset with no retention or archiving needs, and ~230M rows sit
--     comfortably in one partition. Partitioning would not speed up the hub queries.
--
--   Engine: plain MergeTree; ClickHouse Cloud maps it to SharedMergeTree.
--   Loading: one INSERT ... SELECT per year (~1.3-7.5M rows each), so every insert is
--   a large batch (insert-batch-size) and a failed year can be retried on its own.

CREATE DATABASE IF NOT EXISTS flights;

CREATE TABLE IF NOT EXISTS flights.ontime
(
    Year                            UInt16,
    Quarter                         UInt8,
    Month                           UInt8,
    DayofMonth                      UInt8,
    DayOfWeek                       UInt8,
    FlightDate                      Date,
    Reporting_Airline               LowCardinality(String),
    Tail_Number                     String,
    Flight_Number_Reporting_Airline LowCardinality(String),
    Origin                          LowCardinality(String),
    OriginCityName                  LowCardinality(String),
    OriginState                     LowCardinality(String),
    Dest                            LowCardinality(String),
    DestCityName                    LowCardinality(String),
    DestState                       LowCardinality(String),
    CRSDepTime                      Int16,
    DepDelay                        Int16,
    DepDel15                        Int8,
    DepTimeBlk                      LowCardinality(String),
    TaxiOut                         Int16,
    TaxiIn                          Int16,
    ArrDelay                        Int16,
    ArrDel15                        Int8,
    ArrTimeBlk                      LowCardinality(String),
    Cancelled                       Int8,
    CancellationCode                LowCardinality(String),
    Diverted                        Int8,
    AirTime                         Int16,
    Distance                        Int16,
    DistanceGroup                   Int8,
    CarrierDelay                    Int16,
    WeatherDelay                    Int16,
    NASDelay                        Int16,
    SecurityDelay                   Int16,
    LateAircraftDelay               Int16
)
ENGINE = MergeTree
ORDER BY (Origin, Year, Month, Reporting_Airline, FlightDate);
