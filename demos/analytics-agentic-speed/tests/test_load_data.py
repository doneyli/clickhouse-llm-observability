"""Unit tests for scripts/load_data.py. No network: SQL generation, parsing, planning."""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import pytest

DEMO_DIR = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("load_data", DEMO_DIR / "scripts" / "load_data.py")
ld = importlib.util.module_from_spec(_spec)
sys.modules["load_data"] = ld  # dataclasses resolve string annotations through sys.modules
_spec.loader.exec_module(ld)

SCHEMA = (DEMO_DIR / "sql" / "schema.sql").read_text()

BRIEF_COLUMNS = (
    "Year, Quarter, Month, DayofMonth, DayOfWeek, FlightDate, Reporting_Airline, Tail_Number, "
    "Flight_Number_Reporting_Airline, Origin, OriginCityName, OriginState, Dest, DestCityName, "
    "DestState, CRSDepTime, DepDelay, DepDel15, DepTimeBlk, TaxiOut, TaxiIn, ArrDelay, ArrDel15, "
    "ArrTimeBlk, Cancelled, CancellationCode, Diverted, AirTime, Distance, DistanceGroup, "
    "CarrierDelay, WeatherDelay, NASDelay, SecurityDelay, LateAircraftDelay"
).split(", ")


# --- schema -----------------------------------------------------------------------------
def test_schema_declares_exactly_the_required_columns_in_order():
    cols = ld.parse_columns(SCHEMA)
    assert [n for n, _ in cols] == BRIEF_COLUMNS == list(ld.REQUIRED_COLUMNS)
    ld.check_columns(cols)  # no FixedString, no Nullable


def test_schema_types():
    types = dict(ld.parse_columns(SCHEMA))
    for c in ("Origin", "Dest", "OriginState", "DestState", "OriginCityName", "DestCityName",
              "Reporting_Airline", "Flight_Number_Reporting_Airline", "DepTimeBlk", "ArrTimeBlk",
              "CancellationCode"):
        assert types[c] == "LowCardinality(String)", c
    assert types["Tail_Number"] == "String"  # 20,087 distinct: over the LowCardinality line
    assert types["FlightDate"] == "Date"
    for c in ("DepDelay", "ArrDelay", "NASDelay", "AirTime"):  # can be negative
        assert types[c].startswith("Int"), c
    assert not any("FixedString" in t or "Nullable" in t for t in types.values())


def test_schema_sort_key_and_no_partition():
    flat = " ".join(SCHEMA.split())
    assert "ORDER BY (Origin, Year, Month, Reporting_Airline, FlightDate)" in flat
    stmts = ld.split_sql(SCHEMA)
    assert not any("PARTITION BY" in s.upper() for s in stmts)
    assert stmts[0] == "CREATE DATABASE IF NOT EXISTS flights"
    assert stmts[1].startswith("CREATE TABLE IF NOT EXISTS flights.ontime")
    assert "ENGINE = MergeTree" in stmts[1]


def test_check_columns_rejects_drift():
    cols = ld.parse_columns(SCHEMA)
    with pytest.raises(ld.LoaderError):
        ld.check_columns(cols[:-1])
    with pytest.raises(ld.LoaderError):
        ld.check_columns([(n, "FixedString(5)" if n == "Origin" else t) for n, t in cols])
    with pytest.raises(ld.LoaderError):
        ld.check_columns(list(reversed(cols)))


def test_split_sql_drops_comments_and_empty_statements():
    text = "-- header; with a semicolon\nSELECT 1; -- trailing\n\n;SELECT 2\n"
    assert ld.split_sql(text) == ["SELECT 1", "SELECT 2"]


def test_render_schema_retargets_database_and_table():
    stmts = ld.render_schema(SCHEMA, "demo_db", "flights_tbl")
    assert stmts[0] == "CREATE DATABASE IF NOT EXISTS demo_db"
    assert stmts[1].startswith("CREATE TABLE IF NOT EXISTS demo_db.flights_tbl")
    assert ld.render_schema(SCHEMA, "flights", "ontime") == ld.split_sql(SCHEMA)
    with pytest.raises(ld.LoaderError):
        ld.render_schema(SCHEMA, "bad;name", "ontime")


# --- INSERT generation --------------------------------------------------------------------
def _select_list(sql: str) -> list[str]:
    body = sql.split("SELECT", 1)[1].split("\nFROM ", 1)[0]
    return [e.strip() for e in body.strip().split(",\n")]


PREFIX = ("https://clickhouse-public-datasets.s3.amazonaws.com/ontime/original/"
          "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_")


def test_zip_insert_sql():
    cols = ld.parse_columns(SCHEMA)
    sql = ld.build_insert_sql(cols, "zip", 2019, "flights.ontime", [1, 2, 3])
    assert sql.startswith("INSERT INTO flights.ontime (Year, Quarter, Month,")
    assert f"FROM s3('{PREFIX}{{2019_1,2019_2,2019_3}}.zip :: *.csv', 'CSVWithNames')" in sql
    assert "WHERE" not in sql  # the glob selects exactly that year's monthly files
    exprs = _select_list(sql)
    assert len(exprs) == len(cols)
    by_name = dict(zip([n for n, _ in cols], exprs))
    assert by_name["Origin"] == r"trimRight(ifNull(Origin, ''), '\0')"  # strips NUL padding
    assert by_name["CancellationCode"] == r"trimRight(ifNull(CancellationCode, ''), '\0')"
    # "12.00", "0900" and "" all parse; accurateCast throws on overflow instead of wrapping
    assert by_name["ArrDelay"] == "accurateCast(toInt64(toFloat64OrZero(ifNull(ArrDelay, ''))), 'Int16')"
    assert by_name["FlightDate"] == "toDate(ifNull(FlightDate, ''))"


def test_zip_url_and_count_sql():
    assert ld.zip_url([(2025, 7)]) == f"{PREFIX}2025_7.zip :: *.csv"  # no braces for one file
    assert ld.source_url("zip", 1987) == f"{PREFIX}{{1987_10,1987_11,1987_12}}.zip :: *.csv"
    sql = ld.count_zip_sql({2026: [3, 4], 1987: [12]})
    assert f"{PREFIX}{{1987_12,2026_3,2026_4}}.zip :: *.csv" in sql
    assert "toUInt16(splitByChar('_', _file)[-2]) AS year" in sql and "GROUP BY year" in sql
    with pytest.raises(ld.LoaderError):
        ld.zip_url([])


def test_classify_zip_files_skips_html_error_pages():
    listing = [
        {"f": "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2025_7.zip", "size": 32208704},
        {"f": "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2025_8.zip", "size": 84927},
        {"f": "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2025_10.zip", "size": 84961},
        {"f": "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2025_1.zip", "size": 27108664},
        {"f": "readme.html", "size": 10},
    ]
    valid, junk = ld.classify_zip_files(listing)
    assert valid == {2025: [1, 7]}
    assert junk == [(2025, 8, 84927), (2025, 10, 84961)]


def test_csv_insert_sql():
    sql = ld.build_insert_sql(ld.parse_columns(SCHEMA), "csv", 2019, "flights.ontime")
    assert ("FROM s3('https://clickhouse-public-datasets.s3.amazonaws.com/ontime/csv_by_year/"
            "2019.csv.gz', 'CSVWithNames')") in sql


def test_insert_settings():
    s = ld.insert_settings()
    assert s["input_format_csv_use_best_effort_in_schema_inference"] == "0"  # everything as String
    assert int(s["max_download_buffer_size"]) >= 64 << 20  # big zips fail with BADZIPFILE otherwise
    assert int(s["max_read_buffer_size"]) >= 64 << 20
    assert s["insert_deduplicate"] == "0"
    s["insert_deduplicate"] = "1"
    assert ld.insert_settings()["insert_deduplicate"] == "0"  # returns a copy


def test_unknown_source_rejected():
    with pytest.raises(ld.LoaderError):
        ld.source_url("ftp", 2019)


def test_reference_counts():
    assert sum(ld.REFERENCE_YEAR_COUNTS.values()) == ld.REFERENCE_TOTAL
    years = list(range(ld.FIRST_YEAR, ld.LAST_YEAR + 1))
    assert sorted(ld.REFERENCE_YEAR_COUNTS) == sorted(ld.REFERENCE_MONTHS) == years
    assert ld.REFERENCE_MONTHS[1987] == (10, 11, 12) and ld.REFERENCE_MONTHS[2019] == tuple(range(1, 13))
    assert all(1_000_000 < n < 8_000_000 for n in ld.REFERENCE_YEAR_COUNTS.values())
    assert "GROUP BY Year" in ld.target_counts_sql("flights.ontime")


# --- user DDL and secrets -------------------------------------------------------------------
def test_user_statements():
    pw = "pa'ss\\word"
    stmts = ld.user_statements("scenario_agent", pw, "flights")
    create, alter, grant_db, grant_cols = stmts
    assert create.startswith("CREATE USER IF NOT EXISTS scenario_agent IDENTIFIED BY 'pa\\'ss\\\\word'")
    assert alter.startswith("ALTER USER scenario_agent IDENTIFIED BY ")
    for s in (create, alter):
        assert "readonly = 2 CONST" in s
        assert "max_execution_time = 30 MIN 1 MAX 300" in s
        assert "DEFAULT DATABASE flights" in s
    assert grant_db == "GRANT SELECT ON flights.* TO scenario_agent WITH REPLACE OPTION"
    assert grant_cols == "GRANT SELECT ON system.columns TO scenario_agent"
    assert not any(re.search(r"\b(INSERT|ALTER|CREATE|S3|URL|REMOTE|FILE)\b", s) for s in (grant_db, grant_cols))
    with pytest.raises(ld.LoaderError):
        ld.user_statements("bad user", pw, "flights")


def test_agent_limits_admit_the_agents_own_settings():
    from explorer.config import DEFAULT_QUERY_SETTINGS

    def bounds(name):
        m = re.search(rf"{name} = \d+ MIN (\d+) MAX (\d+)", ld.AGENT_USER_SETTINGS)
        return int(m.group(1)), int(m.group(2))

    for name in ("max_execution_time", "max_rows_to_read", "max_result_rows"):
        lo, hi = bounds(name)
        assert lo <= float(DEFAULT_QUERY_SETTINGS[name]) <= hi, name


def test_sql_string_and_mask():
    assert ld.sql_string("it's") == "'it\\'s'"
    assert ld.sql_string("a\\b") == "'a\\\\b'"
    assert ld.mask("auth failed for s3cr3t and s3cr3t", ["s3cr3t", ""]) == "auth failed for *** and ***"


# --- planning and args ------------------------------------------------------------------------
def test_plan_chunks_resumes_and_repairs():
    source = {2018: 100, 2019: 200, 2020: 300}
    target = {2018: 100, 2019: 50}
    plan = {c.year: c.action for c in ld.plan_chunks(range(2017, 2021), source, target)}
    assert plan == {2018: "skip", 2019: "repair", 2020: "load"}  # 2017: not in source


def test_plan_chunks_all_done():
    source = {2019: 7, 2020: 8}
    assert all(c.action == "skip" for c in ld.plan_chunks([2019, 2020], source, dict(source)))


def test_parse_args_defaults():
    a = ld.parse_args([])
    assert (a.source, a.reload, a.years, a.jobs, a.count_source, a.verify_only, a.dry_run) == (
        "zip", False, (ld.FIRST_YEAR, ld.LAST_YEAR), 1, False, False, False)


def test_parse_args_options():
    a = ld.parse_args(["--source", "csv", "--reload", "--years", "2019-2020", "--jobs", "4",
                       "--count-source"])
    assert (a.source, a.reload, a.years, a.jobs, a.count_source) == ("csv", True, (2019, 2020), 4, True)
    assert ld.parse_args(["--years", "2001"]).years == (2001, 2001)


@pytest.mark.parametrize("bad", ["2020-2019", "1900", "19-20", "", "2019-2099"])
def test_parse_years_rejects(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        ld.parse_years(bad)


def test_parse_args_rejects_bad_source_and_jobs():
    with pytest.raises(SystemExit):
        ld.parse_args(["--source", "remote"])  # removed: the playground quota can't carry 229M rows
    with pytest.raises(SystemExit):
        ld.parse_args(["--jobs", "0"])
