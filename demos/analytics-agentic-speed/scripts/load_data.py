#!/usr/bin/env python
"""Load the US flights dataset into the presenter's ClickHouse Cloud service.

What it does, in order (every step is safe to re-run):

1. Creates ``flights.ontime`` from ``sql/schema.sql`` (IF NOT EXISTS).
2. Loads the data one year per INSERT ... SELECT, read by ClickHouse itself from the
   public S3 bucket (nothing is downloaded to this machine). A year whose row count
   already matches the source is skipped, so an interrupted load resumes where it
   stopped; a year with the wrong count is deleted and loaded again.
3. Creates the agent's read-only user (``CH_USER`` / ``CH_PASSWORD`` from ``.env``):
   ``readonly = 2``, SELECT on the demo database and ``system.columns`` only.
4. Verifies the data (row counts, no NUL padding) and the user, connecting as the agent
   through ``explorer.ch_client.ClickHouseClient``.

Sources (all public, ``clickhouse-public-datasets`` bucket):

* ``zip`` (default): the original BTS monthly files, ``ontime/original/*.zip``,
  1987-10 .. 2026-08. Seven of them (2025-08 .. 2026-02) are HTML error pages saved
  under .zip names; they are detected by size and skipped, so those months are absent.
* ``csv``: ``ontime/csv_by_year/{YEAR}.csv.gz``. Ends in 2022.

Why not ``remoteSecure`` from the playground (sql-clickhouse.clickhouse.com): its
``demo`` user has an hourly quota of 60,000 result rows and 60 SELECTs (measured
2026-11-26), and rows streamed to another server count as result rows. Loading 229M
rows that way is impossible however it is chunked.

Usage (from demos/analytics-agentic-speed):

    .venv/bin/python scripts/load_data.py                # create, load what's missing, user, verify
    .venv/bin/python scripts/load_data.py --reload       # truncate, then load everything again
    .venv/bin/python scripts/load_data.py --jobs 2       # two years at a time
    .venv/bin/python scripts/load_data.py --source csv   # fallback source (ends in 2022)
    .venv/bin/python scripts/load_data.py --verify-only  # no DDL, no load: just the checks
    .venv/bin/python scripts/load_data.py --dry-run      # print the SQL it would run

Admin credentials (``CH_ADMIN_USER`` / ``CH_ADMIN_PASSWORD``) are used only here.
Secrets are never printed: any server message is masked before it is shown.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import httpx

DEMO_DIR = Path(__file__).resolve().parent.parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from explorer.ch_client import ClickHouseClient, ClickHouseError  # noqa: E402
from explorer.config import ClickHouseSettings, load_env  # noqa: E402

SCHEMA_PATH = DEMO_DIR / "sql" / "schema.sql"

BUCKET = "https://clickhouse-public-datasets.s3.amazonaws.com/ontime"
ZIP_PREFIX = BUCKET + "/original/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_"
CSV_URL = BUCKET + "/csv_by_year/{year}.csv.gz"
SOURCES = ("zip", "csv")
FIRST_YEAR, LAST_YEAR = 1987, 2026
# Files under this size are not data: the bucket holds the BTS site's ~85 KB HTML error
# page under the names of the months 2025-08 .. 2026-02 (checked 2026-11-26). Real monthly
# zips are 7-33 MB.
MIN_ZIP_BYTES = 1 << 20

# The valid months per year in the bucket, and the rows they hold, measured 2026-11-26
# (``--count-source``). A year whose valid months differ from these (the bucket gained or
# fixed a file) is counted again from the files; the rest trust these numbers, because
# counting all 460 zips takes ~5.5 minutes on a 4-vCPU replica. Totals: 231,636,170 rows
# here vs 228,792,589 in the playground's ontime.ontime, which differs in 2018 (7,251 more
# rows there), 2022 (556,502 fewer), 2025 (1,088,834 fewer) and 2026 (1,205,496 fewer).
REFERENCE_MONTHS: dict[int, tuple[int, ...]] = {
    **{y: tuple(range(1, 13)) for y in range(FIRST_YEAR, LAST_YEAR + 1)},
    1987: (10, 11, 12),
    2025: (1, 2, 3, 4, 5, 6, 7),
    2026: (3, 4, 5, 6, 7, 8),
}
REFERENCE_YEAR_COUNTS: dict[int, int] = {
    1987: 1311826, 1988: 5202096, 1989: 5041200, 1990: 5270893, 1991: 5076925,
    1992: 5092157, 1993: 5070501, 1994: 5180048, 1995: 5327435, 1996: 5351983,
    1997: 5411843, 1998: 5384721, 1999: 5527884, 2000: 5683047, 2001: 5967780,
    2002: 5271359, 2003: 6488540, 2004: 7129270, 2005: 7140596, 2006: 7141922,
    2007: 7455458, 2008: 7009726, 2009: 6450285, 2010: 6450117, 2011: 6085281,
    2012: 6096762, 2013: 6369482, 2014: 5819811, 2015: 5819079, 2016: 5617658,
    2017: 5674621, 2018: 7206195, 2019: 7422037, 2020: 4688354, 2021: 5995397,
    2022: 6729125, 2023: 6847899, 2024: 7079061, 2025: 4078104, 2026: 3669692,
}
REFERENCE_TOTAL = 231_636_170

# The columns the scenarios use, verified to exist on the playground's ontime.ontime.
# sql/schema.sql must declare exactly these, in this order (checked at start-up).
REQUIRED_COLUMNS: tuple[str, ...] = (
    "Year", "Quarter", "Month", "DayofMonth", "DayOfWeek", "FlightDate",
    "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline",
    "Origin", "OriginCityName", "OriginState", "Dest", "DestCityName", "DestState",
    "CRSDepTime", "DepDelay", "DepDel15", "DepTimeBlk", "TaxiOut", "TaxiIn",
    "ArrDelay", "ArrDel15", "ArrTimeBlk", "Cancelled", "CancellationCode", "Diverted",
    "AirTime", "Distance", "DistanceGroup", "CarrierDelay", "WeatherDelay", "NASDelay",
    "SecurityDelay", "LateAircraftDelay",
)

# Per-user limits for the agent. readonly = 2 lets the agent send its own per-query
# settings (explorer.config.DEFAULT_QUERY_SETTINGS); the MIN 1 bounds stop a 0
# ("unlimited") from slipping past the MAX.
AGENT_USER_SETTINGS = (
    "readonly = 2 CONST, "
    "max_execution_time = 30 MIN 1 MAX 300, "
    "max_rows_to_read = 1000000000 MIN 1 MAX 2000000000, "
    "max_result_rows = 10000 MIN 1 MAX 1000000"
)

# Settings for each INSERT ... SELECT (and source count):
# * input_format_csv_use_best_effort_in_schema_inference = 0: read every CSV field as a
#   string and cast explicitly; the BTS files write numbers as "12.00" and times as "0900".
# * max_download_buffer_size / max_read_buffer_size = 256 MiB: with the 10 MiB default,
#   zips over ~24 MB (most months after 2004) fail with BADZIPFILE.
# * insert_deduplicate = 0: after a TRUNCATE or a repair, identical blocks must not be
#   dropped as duplicates of the rows that were just removed.
_READ_SETTINGS = {
    "input_format_csv_use_best_effort_in_schema_inference": "0",
    "max_download_buffer_size": str(256 << 20),
    "max_read_buffer_size": str(256 << 20),
    "max_execution_time": "3600",
}
_INSERT_SETTINGS = {**_READ_SETTINGS, "insert_deduplicate": "0"}
# Reads right after a write may land on the other replica: ask for a consistent view.
_CONSISTENT = {"select_sequential_consistency": "1"}


class LoaderError(RuntimeError):
    """A failure the loader explains and stops on."""


# --- SQL text helpers -----------------------------------------------------------------
def sql_string(value: str) -> str:
    """Quote ``value`` as a ClickHouse string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def check_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise LoaderError(f"not a plain identifier: {name!r}")
    return name


def split_sql(text: str) -> list[str]:
    """Split a .sql file into statements: drops ``--`` comments, splits on ``;``.

    Good enough for sql/schema.sql (no semicolons or ``--`` inside string literals).
    """
    lines = []
    for line in text.splitlines():
        cut = line.find("--")
        lines.append(line if cut < 0 else line[:cut])
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _split_top_level(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_columns(schema_sql: str) -> list[tuple[str, str]]:
    """(name, type) for every column of the CREATE TABLE in ``schema_sql``."""
    for stmt in split_sql(schema_sql):
        m = re.match(r"(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?\S+\s*\(", stmt)
        if not m:
            continue
        start, depth = m.end(), 1
        for i in range(start, len(stmt)):
            if stmt[i] == "(":
                depth += 1
            elif stmt[i] == ")":
                depth -= 1
                if depth == 0:
                    body = stmt[start:i]
                    break
        else:
            raise LoaderError("unbalanced parentheses in CREATE TABLE")
        cols = []
        for item in _split_top_level(body):
            name, _, ctype = item.partition(" ")
            cols.append((name.strip(), ctype.strip()))
        return cols
    raise LoaderError("no CREATE TABLE statement in schema")


def check_columns(columns: Sequence[tuple[str, str]]) -> None:
    names = tuple(n for n, _ in columns)
    if names != REQUIRED_COLUMNS:
        missing = sorted(set(REQUIRED_COLUMNS) - set(names))
        extra = sorted(set(names) - set(REQUIRED_COLUMNS))
        raise LoaderError(f"schema columns differ from REQUIRED_COLUMNS (missing={missing}, "
                          f"extra={extra}, or the order changed)")
    for name, ctype in columns:
        if "FixedString" in ctype or "Nullable" in ctype:
            raise LoaderError(f"{name}: {ctype} is not allowed (use LowCardinality(String) / defaults)")


def render_schema(schema_sql: str, database: str, table: str) -> list[str]:
    """Schema statements, retargeted if .env names a database/table other than flights.ontime."""
    check_identifier(database)
    check_identifier(table)
    out = []
    for stmt in split_sql(schema_sql):
        stmt = re.sub(r"\bflights\.ontime\b", f"{database}.{table}", stmt)
        stmt = re.sub(r"(?i)(DATABASE\s+IF\s+NOT\s+EXISTS\s+)flights\b", rf"\g<1>{database}", stmt)
        out.append(stmt)
    return out


def select_expr(name: str, ctype: str) -> str:
    """The SELECT expression that turns a CSV field (read as Nullable(String)) into ``ctype``.

    Strings: drop NUL bytes (the csv_by_year files keep the playground's FixedString(5)
    padding, 'IAD\\0\\0'). Numbers: "12.00" / "0900" / "" parse via Float64; ``accurateCast``
    then throws on overflow instead of wrapping, so a type that is too tight fails loudly.
    """
    if "String" in ctype:
        return f"trimRight(ifNull({name}, ''), '\\0')"
    if ctype == "Date":
        return f"toDate(ifNull({name}, ''))"  # an empty date throws: never silently 1970
    return f"accurateCast(toInt64(toFloat64OrZero(ifNull({name}, ''))), '{ctype}')"


def zip_url(year_months: Sequence[tuple[int, int]]) -> str:
    """A glob over exactly these (year, month) zips, reading the CSV inside each."""
    if not year_months:
        raise LoaderError("no zip files to read")
    alts = ",".join(f"{int(y)}_{int(m)}" for y, m in year_months)
    part = alts if len(year_months) == 1 else "{" + alts + "}"
    return f"{ZIP_PREFIX}{part}.zip :: *.csv"


def source_url(source: str, year: int, months: Optional[Sequence[int]] = None) -> str:
    if source == "zip":
        return zip_url([(year, m) for m in (months if months is not None else REFERENCE_MONTHS[year])])
    if source == "csv":
        return CSV_URL.format(year=int(year))
    raise LoaderError(f"unknown source {source!r}")


def source_table_expr(source: str, year: int, months: Optional[Sequence[int]] = None) -> str:
    return f"s3({sql_string(source_url(source, year, months))}, 'CSVWithNames')"


def count_zip_sql(months_by_year: Mapping[int, Sequence[int]]) -> str:
    """Rows per year over the given zips, in one query (ClickHouse reads them in parallel)."""
    url = zip_url([(y, m) for y in sorted(months_by_year) for m in months_by_year[y]])
    # _file is the CSV inside the zip: ..._(1987_present)_2018_1.csv -> '2018'
    return (f"SELECT toUInt16(splitByChar('_', _file)[-2]) AS year, count() AS n "
            f"FROM s3({sql_string(url)}, 'CSVWithNames') GROUP BY year ORDER BY year")


def build_insert_sql(columns: Sequence[tuple[str, str]], source: str, year: int,
                     qualified_table: str, months: Optional[Sequence[int]] = None) -> str:
    """One year per INSERT (1.3-7.5M rows): large batches (insert-batch-size), retried alone."""
    names = ", ".join(n for n, _ in columns)
    exprs = ",\n    ".join(select_expr(n, t) for n, t in columns)
    return (f"INSERT INTO {qualified_table} ({names})\n"
            f"SELECT\n    {exprs}\n"
            f"FROM {source_table_expr(source, year, months)}")


def insert_settings() -> dict[str, str]:
    return dict(_INSERT_SETTINGS)


def target_counts_sql(qualified_table: str) -> str:
    return f"SELECT Year AS year, count() AS n FROM {qualified_table} GROUP BY Year ORDER BY Year"


def user_statements(user: str, password: str, database: str) -> list[str]:
    """DDL for the agent's read-only user. Re-runnable: ALTER keeps the password in sync,
    and GRANT ... WITH REPLACE OPTION drops any privilege granted by hand since."""
    check_identifier(user)
    check_identifier(database)
    pw = sql_string(password)
    return [
        f"CREATE USER IF NOT EXISTS {user} IDENTIFIED BY {pw} "
        f"DEFAULT DATABASE {database} SETTINGS {AGENT_USER_SETTINGS}",
        f"ALTER USER {user} IDENTIFIED BY {pw} DEFAULT DATABASE {database} "
        f"SETTINGS {AGENT_USER_SETTINGS}",
        f"GRANT SELECT ON {database}.* TO {user} WITH REPLACE OPTION",
        # The agent profiles its own table; system.columns only lists tables it can see.
        f"GRANT SELECT ON system.columns TO {user}",
    ]


def mask(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


# --- chunk planning ---------------------------------------------------------------------
@dataclass(frozen=True)
class Chunk:
    year: int
    expected: int  # rows in the source
    existing: int  # rows already in the target
    action: str  # "skip" | "load" | "repair"


def plan_chunks(years: Iterable[int], source: Mapping[int, int], target: Mapping[int, int]) -> list[Chunk]:
    """One chunk per year the source has. Matching counts are skipped (resume)."""
    chunks = []
    for y in years:
        if y not in source:
            continue
        exp, have = int(source[y]), int(target.get(y, 0))
        action = "skip" if have == exp else ("load" if have == 0 else "repair")
        chunks.append(Chunk(y, exp, have, action))
    return chunks


def parse_years(spec: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{4})\s*(?:-\s*(\d{4})\s*)?", spec or "")
    if not m:
        raise argparse.ArgumentTypeError(f"expected YEAR or FIRST-LAST, got {spec!r}")
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    if first > last:
        raise argparse.ArgumentTypeError(f"first year {first} is after last year {last}")
    if first < FIRST_YEAR or last > LAST_YEAR:
        raise argparse.ArgumentTypeError(f"years must be within {FIRST_YEAR}-{LAST_YEAR}")
    return first, last


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load flights.ontime and create the agent's read-only user.")
    p.add_argument("--source", choices=SOURCES, default="zip",
                   help="zip: BTS monthly files, 1987-10..2026-08 (default); "
                        "csv: per-year CSV files, 1987-2022 only")
    p.add_argument("--reload", action="store_true", help="truncate the table and load everything again")
    p.add_argument("--years", type=parse_years, default=(FIRST_YEAR, LAST_YEAR), metavar="FIRST-LAST",
                   help=f"limit the load to these years (default {FIRST_YEAR}-{LAST_YEAR})")
    p.add_argument("--jobs", type=int, default=1, choices=range(1, 9), metavar="N",
                   help="years to load in parallel (1-8, default 1)")
    p.add_argument("--count-source", action="store_true",
                   help="count the source files instead of trusting REFERENCE_YEAR_COUNTS (zip; "
                        "~5.5 min for all years). --source csv always counts.")
    p.add_argument("--verify-only", action="store_true", help="skip DDL, load and user creation; only verify")
    p.add_argument("--skip-user", action="store_true", help="do not create/alter the agent user")
    p.add_argument("--dry-run", action="store_true", help="print the SQL (secrets masked) and exit")
    return p.parse_args(argv)


# --- admin HTTP helper ------------------------------------------------------------------
class Admin:
    """Admin connection for DDL and INSERT over the HTTP interface (ClickHouseClient only
    does SELECTs). ``wait_end_of_query=1`` buffers the response, so an error part-way
    through a long INSERT comes back as a non-200 instead of a truncated 200."""

    def __init__(self, settings: ClickHouseSettings, secrets: Sequence[str] = ()):
        self.settings = settings
        self.secrets = [s for s in (settings.password, *secrets) if s]
        self._http = httpx.Client(auth=(settings.user, settings.password),
                                  timeout=httpx.Timeout(60.0, connect=15.0))

    def close(self) -> None:
        self._http.close()

    def mask(self, text: str) -> str:
        return mask(text, self.secrets)

    def _post(self, sql: str, params: Mapping[str, str], timeout: float) -> httpx.Response:
        where = f"{self.settings.host}:{self.settings.port}"
        try:
            resp = self._http.post(self.settings.url, params={"wait_end_of_query": "1", **params},
                                   content=sql.encode(), timeout=httpx.Timeout(timeout, connect=15.0))
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise LoaderError(
                f"cannot connect to ClickHouse at {where} ({type(e).__name__}). Check CH_HOST/CH_PORT "
                f"in .env, that the service is running, and that its IP access list allows this machine."
            ) from None
        except httpx.TimeoutException:
            raise LoaderError(f"no answer from {where} within {timeout:.0f}s") from None
        except httpx.HTTPError as e:
            raise LoaderError(f"HTTP error talking to {where}: {self.mask(str(e))}") from None
        if resp.status_code != 200:
            code = resp.headers.get("X-ClickHouse-Exception-Code", "?")
            msg = self.mask(resp.text.strip())[:2000]
            hint = " (check CH_ADMIN_USER / CH_ADMIN_PASSWORD)" if code in ("516", "194") else ""
            raise LoaderError(f"ClickHouse error {code} (HTTP {resp.status_code}){hint}: {msg}")
        return resp

    def execute(self, sql: str, *, settings: Optional[Mapping[str, str]] = None,
                timeout: float = 120.0) -> dict[str, Any]:
        """Run a statement; return the parsed ``X-ClickHouse-Summary`` (written_rows, ...)."""
        resp = self._post(sql, dict(settings or {}), timeout)
        try:
            return json.loads(resp.headers.get("X-ClickHouse-Summary", "{}"))
        except ValueError:
            return {}

    def rows(self, sql: str, *, settings: Optional[Mapping[str, str]] = None,
             timeout: float = 120.0) -> list[dict[str, Any]]:
        params = {"default_format": "JSON", "output_format_json_quote_64bit_integers": "0",
                  **dict(settings or {})}
        return self._post(sql, params, timeout).json().get("data", [])

    def value(self, sql: str, **kw: Any) -> Any:
        data = self.rows(sql, **kw)
        return next(iter(data[0].values())) if data else None


# --- steps ------------------------------------------------------------------------------
def fmt_rows(n: int) -> str:
    return f"{n:,}"


def apply_schema(admin: Admin, statements: Sequence[str]) -> None:
    for stmt in statements:
        admin.execute(stmt)
    print(f"schema: {len(statements)} statements applied (IF NOT EXISTS)")


def classify_zip_files(files: Iterable[Mapping[str, Any]]) -> tuple[dict[int, list[int]], list[tuple[int, int, int]]]:
    """Split a bucket listing (``f`` name, ``size`` bytes) into valid months per year and
    the (year, month, size) of files too small to be data (HTML error pages)."""
    valid: dict[int, list[int]] = {}
    junk: list[tuple[int, int, int]] = []
    for r in files:
        m = re.search(r"_(\d{4})_(\d{1,2})\.zip$", r["f"])
        if not m:
            continue
        y, mo, size = int(m.group(1)), int(m.group(2)), int(r["size"])
        if size >= MIN_ZIP_BYTES:
            valid.setdefault(y, []).append(mo)
        else:
            junk.append((y, mo, size))
    return {y: sorted(ms) for y, ms in valid.items()}, sorted(junk)


def expected_counts(admin: Admin, source: str, years: Sequence[int],
                    count_source: bool) -> tuple[dict[int, int], dict[int, list[int]]]:
    """Rows per year in the source, and (zip) which months each year reads.

    'One' lists objects without reading their data."""
    if source == "zip":
        listing = admin.rows(f"SELECT _file AS f, _size AS size FROM s3({sql_string(ZIP_PREFIX + '*.zip')}, 'One')")
        valid, junk = classify_zip_files(listing)
        junk = [j for j in junk if j[0] in years]
        if junk:
            (y0, m0, _), (y1, m1, _) = junk[0], junk[-1]
            print(f"source zip: skipping {len(junk)} files that are not zip archives "
                  f"({y0}-{m0:02d} .. {y1}-{m1:02d}, {max(s for *_, s in junk) // 1024} KB at most: "
                  f"HTML error pages stored under .zip names)")
        months = {y: valid[y] for y in years if y in valid}
        counts: dict[int, int] = {}
        to_count: dict[int, list[int]] = {}
        for y, ms in months.items():
            if not count_source and tuple(ms) == REFERENCE_MONTHS.get(y) and y in REFERENCE_YEAR_COUNTS:
                counts[y] = REFERENCE_YEAR_COUNTS[y]
            else:
                to_count[y] = ms
        if to_count:
            print(f"source zip: counting {len(to_count)} year(s) from the files "
                  f"({'--count-source' if count_source else 'months differ from the reference'}) ...",
                  flush=True)
            for r in admin.rows(count_zip_sql(to_count), settings=_READ_SETTINGS, timeout=3600):
                counts[int(r["year"])] = int(r["n"])
        return counts, months
    # csv: count only years that have a file
    listing = admin.rows(f"SELECT _file AS f FROM s3({sql_string(BUCKET + '/csv_by_year/*.csv.gz')}, 'One')")
    present = {int(m.group(1)) for r in listing if (m := re.fullmatch(r"(\d{4})\.csv\.gz", r["f"]))}
    missing = [y for y in years if y not in present]
    if missing:
        print(f"source csv: no files for {len(missing)} of the requested years "
              f"({missing[0]}..{missing[-1]}); they are skipped")
    counts = {}
    for y in (y for y in years if y in present):
        counts[y] = int(admin.value(f"SELECT count() FROM {source_table_expr('csv', y)}",
                                    settings=_READ_SETTINGS, timeout=900))
        print(f"  counted {y}: {fmt_rows(counts[y])} rows", flush=True)
    return counts, {}


def fetch_target_counts(admin: Admin, qualified_table: str) -> dict[int, int]:
    return {int(r["year"]): int(r["n"])
            for r in admin.rows(target_counts_sql(qualified_table), settings=_CONSISTENT)}


def load(admin: Admin, columns: Sequence[tuple[str, str]], args: argparse.Namespace,
         qualified_table: str) -> dict[str, Any]:
    years = list(range(args.years[0], args.years[1] + 1))
    t_src = time.perf_counter()
    source, months = expected_counts(admin, args.source, years, args.count_source)
    expected_total = sum(source.values())
    print(f"source {args.source}: {fmt_rows(expected_total)} rows in {len(source)} years "
          f"({time.perf_counter() - t_src:.1f}s to list/count)")
    if args.reload:
        admin.execute(f"TRUNCATE TABLE {qualified_table}", timeout=600)
        print(f"--reload: truncated {qualified_table}")
    target = fetch_target_counts(admin, qualified_table)
    chunks = plan_chunks(years, source, target)
    todo = [c for c in chunks if c.action != "skip"]
    report = {"source": args.source, "expected": expected_total, "loaded_chunks": len(todo),
              "skipped_chunks": len(chunks) - len(todo), "seconds": 0.0}
    if not todo:
        have = sum(target.get(y, 0) for y in years)
        print(f"load: already complete ({fmt_rows(have)} rows == source); nothing to do "
              f"(use --reload to force)")
        return report
    print(f"load: {len(todo)} of {len(chunks)} years to load, {len(chunks) - len(todo)} already match "
          f"(jobs={args.jobs})", flush=True)

    lock = threading.Lock()
    done = {"n": 0, "rows": sum(c.existing for c in chunks if c.action == "skip")}

    def run(chunk: Chunk) -> Chunk:
        t0 = time.perf_counter()
        if chunk.action == "repair":
            # Rare path (an interrupted year): a lightweight DELETE, not a mutation
            # (insert-mutation-avoid-delete). Waits until the rows are gone.
            admin.execute(f"DELETE FROM {qualified_table} WHERE Year = {chunk.year}", timeout=1800)
        summary = admin.execute(build_insert_sql(columns, args.source, chunk.year, qualified_table,
                                                 months.get(chunk.year)),
                                settings=insert_settings(), timeout=3900)
        written = int(summary.get("written_rows", -1))
        got = int(admin.value(f"SELECT count() FROM {qualified_table} WHERE Year = {chunk.year}",
                              settings=_CONSISTENT))
        elapsed = time.perf_counter() - t0
        if got != chunk.expected:
            hint = (" If a file in the bucket was replaced since the reference was measured, "
                    "re-run with --count-source." if args.source == "zip" and not args.count_source else "")
            raise LoaderError(f"{chunk.year}: the source files gave {fmt_rows(written)} rows and the "
                              f"table now holds {fmt_rows(got)}, expected {fmt_rows(chunk.expected)}. "
                              f"Re-run to retry this year.{hint}")
        with lock:
            done["n"] += 1
            done["rows"] += got
            note = f" (repaired, had {fmt_rows(chunk.existing)})" if chunk.action == "repair" else ""
            print(f"  [{done['n']:>2}/{len(todo)}] {chunk.year}  {got:>10,} rows  {elapsed:6.1f}s  "
                  f"total {done['rows'] / 1e6:6.1f}M / {expected_total / 1e6:.1f}M{note}", flush=True)
        return chunk

    failures: list[str] = []

    def run_or_record(chunk: Chunk) -> None:
        # One bad year must not hide the others: record it, load the rest, report at the end.
        try:
            run(chunk)
        except LoaderError as e:
            with lock:
                failures.append(str(e))
                print(f"  FAIL {chunk.year}: {e}", flush=True)

    t0 = time.perf_counter()
    if args.jobs == 1:
        for c in todo:
            run_or_record(c)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for f in as_completed([pool.submit(run_or_record, c) for c in todo]):
                f.result()  # anything other than a LoaderError is a bug: surface it
    report["seconds"] = time.perf_counter() - t0
    print(f"load: {len(todo) - len(failures)} of {len(todo)} years loaded in {report['seconds']:.1f}s",
          flush=True)
    if failures:
        raise LoaderError(f"{len(failures)} year(s) failed; re-run to retry only those years")
    return report


def create_user(admin: Admin, agent: ClickHouseSettings) -> None:
    for stmt in user_statements(agent.user, agent.password, agent.database):
        admin.execute(stmt)
    print(f"user: {agent.user} created/updated (readonly = 2; SELECT on {agent.database}.* and "
          f"system.columns; password synced from CH_PASSWORD)")


def verify_data(admin: Admin, qualified_table: str, expected_total: Optional[int]) -> bool:
    stats = admin.rows(
        f"SELECT count() AS rows, min(FlightDate) AS first_day, max(FlightDate) AS last_day, "
        f"countIf(position(Origin, '\\0') > 0 OR position(Dest, '\\0') > 0 "
        f"OR position(OriginState, '\\0') > 0 OR position(DestState, '\\0') > 0 "
        f"OR position(CancellationCode, '\\0') > 0) AS nul_rows, "
        f"countIf(length(Origin) != 3 OR length(Dest) != 3) AS odd_codes, "
        f"arraySort(groupUniqArray(CancellationCode)) AS cancel_codes "
        f"FROM {qualified_table}", settings=_CONSISTENT, timeout=300)[0]
    db, table = qualified_table.split(".")
    parts = admin.rows(
        f"SELECT count() AS parts, formatReadableSize(sum(data_compressed_bytes)) AS compressed, "
        f"formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed "
        f"FROM system.parts WHERE active AND database = {sql_string(db)} AND table = {sql_string(table)}")[0]
    rows = int(stats["rows"])
    ok_count = expected_total is None or rows == expected_total
    ok_nul = int(stats["nul_rows"]) == 0
    exp = "" if expected_total is None else f" (source {fmt_rows(expected_total)})"
    print(f"data: {fmt_rows(rows)} rows{exp} {'OK' if ok_count else 'MISMATCH'}; "
          f"{stats['first_day']} .. {stats['last_day']}; NUL-padded rows {stats['nul_rows']} "
          f"{'OK' if ok_nul else 'FAIL'}; codes not 3 chars {stats['odd_codes']}; "
          f"CancellationCode {stats['cancel_codes']}")
    print(f"      {parts['parts']} active parts, {parts['compressed']} compressed "
          f"({parts['uncompressed']} uncompressed)")
    return ok_count and ok_nul


async def verify_agent_user(agent: ClickHouseSettings, columns: Sequence[tuple[str, str]]) -> bool:
    """Connect as the agent and prove: SELECT works, writes and table functions don't,
    system.columns lists its table (and only its table), and the limits are fenced."""
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, mask(detail, [agent.password])))

    async def expect_refused(name: str, sql: str, *, settings: Optional[dict[str, str]] = None,
                             codes: Sequence[str] = ("164", "497")) -> None:
        try:
            await client.query(sql, settings=settings, check=False)
        except ClickHouseError as e:
            first = str(e).splitlines()[0][:160]
            record(name, e.code in codes, f"refused, code {e.code}: {first}")
            return
        except ValueError:  # an INSERT that succeeds returns an empty, non-JSON body
            pass
        record(name, False, "NOT refused")

    async with ClickHouseClient(agent) as client:
        # (a) a scenario-shaped SELECT with the agent's per-query settings
        try:
            res = await client.query(
                f"SELECT count() AS flights, round(avg(ArrDelay), 2) AS avg_arr_delay "
                f"FROM {agent.qualified_table} "
                f"WHERE Origin = {{origin:String}} AND Year >= {{from_year:UInt16}}",
                {"origin": "ATL", "from_year": 2015})
            row = res.first() or {}
            record("a. SELECT with agent settings", int(row.get("flights", 0)) > 0,
                   f"ATL since 2015: {fmt_rows(int(row.get('flights', 0)))} flights, avg arr delay "
                   f"{row.get('avg_arr_delay')} min; server {res.stats.server_ms:.0f} ms, "
                   f"{fmt_rows(res.stats.read_rows)} rows read")
        except ClickHouseError as e:
            record("a. SELECT with agent settings", False, f"failed, code {e.code}: {str(e)[:160]}")

        # (b) writes are refused. WHERE 0: even if the fence were missing, nothing is written.
        await expect_refused("b. INSERT refused",
                             f"INSERT INTO {agent.qualified_table} SELECT * FROM {agent.qualified_table} WHERE 0")
        # (c) table functions are refused by the server (check=False skips the allow-list)
        await expect_refused("c. s3() table function refused",
                             f"SELECT * FROM s3({sql_string(CSV_URL.format(year=2019))}) LIMIT 1")

        # (d) system.columns lists the agent's own table, and no table outside its grants
        # (system.columns lists itself too: that is the one system table it may read)
        try:
            own = await client.query(
                "SELECT name, type FROM system.columns "
                "WHERE database = {db:String} AND table = {t:String} ORDER BY position",
                {"db": agent.database, "t": agent.table}, check=False)
            other = await client.query(
                "SELECT groupUniqArray(concat(database, '.', table)) AS t FROM system.columns "
                "WHERE database != {db:String} AND NOT (database = 'system' AND table = 'columns')",
                {"db": agent.database}, check=False)
            names = [r[0] for r in own.rows]
            others = (other.first() or {}).get("t") or []
            want = [n for n, _ in columns]
            record("d. system.columns readable", names == want and not others,
                   f"{len(names)} columns for {agent.qualified_table} "
                   f"({'match schema' if names == want else 'DIFFER from schema'}); "
                   f"other tables visible: {others or 'none'}")
        except ClickHouseError as e:
            record("d. system.columns readable", False, f"failed, code {e.code}: {str(e)[:160]}")

        # (e) the per-user fences hold: readonly can't be lifted, limits can't be set to 0
        await expect_refused("e1. readonly can't be lowered", "SELECT 1",
                             settings={"readonly": "0"}, codes=("452", "164"))
        await expect_refused("e2. max_execution_time=0 refused", "SELECT 1",
                             settings={"max_execution_time": "0"}, codes=("452",))

    width = max(len(n) for n, _, _ in results)
    print(f"agent user {agent.user}:")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    return all(ok for _, ok, _ in results)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    load_env(require_langfuse=False)  # ClickHouse only
    try:
        admin_settings = ClickHouseSettings.from_env(admin=True)
        agent_settings = ClickHouseSettings.from_env()
    except KeyError as e:
        print(f"error: {e.args[0]} is not set in .env", file=sys.stderr)
        return 2
    schema_sql = SCHEMA_PATH.read_text()
    columns = parse_columns(schema_sql)
    secrets = [admin_settings.password, agent_settings.password]
    admin = Admin(admin_settings, secrets)
    qt = agent_settings.qualified_table
    full = args.years == (FIRST_YEAR, LAST_YEAR)
    try:
        check_columns(columns)
        statements = render_schema(schema_sql, agent_settings.database, agent_settings.table)
        if args.dry_run:
            out = [*statements,
                   build_insert_sql(columns, args.source, args.years[0], qt)
                   + f"\n-- settings: {insert_settings()}"
                   + f"\n-- one INSERT per year, {args.years[0]}-{args.years[1]}",
                   *user_statements(agent_settings.user, agent_settings.password, agent_settings.database)]
            print(mask(";\n\n".join(out) + ";", secrets))
            return 0

        t_all = time.perf_counter()
        version = admin.value("SELECT version()", timeout=30)
        print(f"connected: {admin_settings.host} as admin user {admin_settings.user!r} (ClickHouse {version})")
        expected: Optional[int] = None
        if not args.verify_only:
            apply_schema(admin, statements)
            expected = load(admin, columns, args, qt)["expected"]
            if not args.skip_user:
                create_user(admin, agent_settings)
        elif args.source == "zip":
            expected = REFERENCE_TOTAL
        # A partial --years load can't be compared with the whole-table count.
        ok = verify_data(admin, qt, expected if full else None)
        ok = asyncio.run(verify_agent_user(agent_settings, columns)) and ok
        print(f"{'done' if ok else 'FAILED'} in {time.perf_counter() - t_all:.1f}s")
        return 0 if ok else 1
    except LoaderError as e:
        print(f"error: {mask(str(e), secrets)}", file=sys.stderr)
        return 1
    finally:
        admin.close()


if __name__ == "__main__":
    sys.exit(main())
