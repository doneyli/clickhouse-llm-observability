#!/usr/bin/env python
"""Pre-flight: warm the presenter's ClickHouse service with scenario-shaped queries.

Runs five queries shaped like the agent's scenario templates (per hub, per carrier,
per month), each twice, as the agent's read-only user through the same client the agent
uses (typed ``{name:Type}`` parameters, the agent's per-query settings, query cache off).
Prints ClickHouse's own server time next to the round-trip time for the first (cold) and
second (warm) run.

A cold first query is a real effect, not a bug: run this before the audience is watching,
and say so in the script rather than hide it.

    .venv/bin/python scripts/warmup.py            # 2 runs per query
    .venv/bin/python scripts/warmup.py --runs 3
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parent.parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from explorer.ch_client import ClickHouseClient, ClickHouseError, QueryStats  # noqa: E402
from explorer.config import ClickHouseSettings, load_env  # noqa: E402

# Phase 0 exit criterion (SPEC §11): a representative template runs warm in < 100 ms server time.
WARM_TARGET_MS = 100.0
EVENING = ["1700-1759", "1800-1859", "1900-1959", "2000-2059"]

# (name, what it stands for, SQL with {table}, params)
QUERIES: list[tuple[str, str, str, dict[str, Any]]] = [
    ("one_scenario", "one sweep cell: hub x departure block x buffer",
     """SELECT count() AS flights,
       round(countIf(ArrDelay <= 15 + {buffer:UInt8}) / count(), 4) AS on_time_share
FROM {table}
WHERE Origin = {hub:String} AND DepTimeBlk = {block:String} AND Year >= {from_year:UInt16}
  AND Cancelled = 0 AND Diverted = 0""",
     {"hub": "ATL", "block": "1800-1859", "buffer": 10, "from_year": 2015}),
    ("hubs_evening", "5 hubs, evening departures, 2015 onward (SPEC §2)",
     """SELECT Origin, count() AS flights, round(avg(ArrDel15), 4) AS late_share,
       round(countIf(ArrDelay <= 15 + {buffer:UInt8}) / count(), 4) AS on_time_with_buffer
FROM {table}
WHERE Origin IN {hubs:Array(String)} AND Year >= {from_year:UInt16}
  AND DepTimeBlk IN {blocks:Array(String)} AND Cancelled = 0 AND Diverted = 0
GROUP BY Origin ORDER BY on_time_with_buffer DESC""",
     {"hubs": ["ATL", "ORD", "DFW", "DEN", "LAX"], "blocks": EVENING, "buffer": 10, "from_year": 2015}),
    ("carriers_at_hub", "per carrier at one hub, 2018-2024",
     """SELECT Reporting_Airline AS carrier, count() AS flights,
       round(avg(ArrDelay), 2) AS avg_arr_delay, round(avg(ArrDel15), 4) AS late_share
FROM {table}
WHERE Origin = {hub:String} AND Year BETWEEN {y0:UInt16} AND {y1:UInt16} AND Cancelled = 0
GROUP BY carrier ORDER BY flights DESC LIMIT 20""",
     {"hub": "DEN", "y0": 2018, "y1": 2024}),
    ("month_causes", "delay causes by year, 3 hubs, December",
     """SELECT Year, count() AS flights, round(avg(WeatherDelay), 2) AS weather,
       round(avg(NASDelay), 2) AS nas, round(avg(CarrierDelay), 2) AS carrier,
       round(avg(LateAircraftDelay), 2) AS late_aircraft
FROM {table}
WHERE Origin IN {hubs:Array(String)} AND Month = {month:UInt8} AND Year >= {from_year:UInt16}
GROUP BY Year ORDER BY Year""",
     {"hubs": ["ORD", "EWR", "SFO"], "month": 12, "from_year": 2010}),
    ("carrier_network", "one carrier, all hubs, summer 2019 (Origin not filtered)",
     """SELECT Origin, count() AS flights, round(avg(DepDelay), 2) AS avg_dep_delay
FROM {table}
WHERE Reporting_Airline = {carrier:String} AND Year = {year:UInt16} AND Month IN {months:Array(UInt8)}
GROUP BY Origin ORDER BY flights DESC LIMIT 10""",
     {"carrier": "WN", "year": 2019, "months": [6, 7, 8]}),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Warm the ClickHouse service with scenario-shaped queries.")
    p.add_argument("--runs", type=int, default=2, choices=range(2, 11), metavar="N",
                   help="runs per query (2-10, default 2)")
    return p.parse_args(argv)


def _fmt_rows(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1_000_000 else f"{n:,}"


async def run(runs: int) -> int:
    load_env()
    settings = ClickHouseSettings.from_env()
    results: list[tuple[str, list[QueryStats], int]] = []
    async with ClickHouseClient(settings) as client:
        try:
            ping_ms = await client.ping()
        except Exception as e:  # noqa: BLE001 — any failure here means "not ready"
            print(f"error: cannot query ClickHouse as {settings.user}: {str(e)[:300]}", file=sys.stderr)
            return 1
        print(f"ClickHouse as {settings.user}: SELECT 1 round trip {ping_ms:.0f} ms "
              f"(query cache off; {runs} runs per query)\n")
        for name, _, sql, params in QUERIES:
            stats: list[QueryStats] = []
            result_rows = 0
            for _ in range(runs):
                try:
                    res = await client.query(sql.replace("{table}", settings.qualified_table), params)
                except ClickHouseError as e:
                    print(f"error: {name}: {str(e).splitlines()[0][:300]}", file=sys.stderr)
                    return 1
                stats.append(res.stats)
                result_rows = res.stats.result_rows
            results.append((name, stats, result_rows))

    header = (f"{'query':<16} {'rows read':>10} {'server ms':>21} {'round trip ms':>21} {'result':>7}\n"
              f"{'':<16} {'':>10} {'cold':>10} {'warm':>10} {'cold':>10} {'warm':>10} {'rows':>7}")
    print(header)
    print("-" * len(header.splitlines()[0]))
    for name, stats, result_rows in results:
        cold, warm = stats[0], min(stats[1:], key=lambda s: s.server_ms)
        print(f"{name:<16} {_fmt_rows(cold.read_rows):>10} {cold.server_ms:>10.0f} {warm.server_ms:>10.0f} "
              f"{cold.roundtrip_ms:>10.0f} {warm.roundtrip_ms:>10.0f} {result_rows:>7}")
    print()
    for name, desc, _, _ in QUERIES:
        print(f"  {name:<16} {desc}")

    scenario = next(s for n, s, _ in results if n == "one_scenario")
    warm_ms = min(s.server_ms for s in scenario[1:])
    verdict = "met" if warm_ms < WARM_TARGET_MS else "MISSED"
    print(f"\nPhase 0 target (one scenario warm < {WARM_TARGET_MS:.0f} ms server time): "
          f"{verdict} at {warm_ms:.0f} ms")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv).runs))


if __name__ == "__main__":
    sys.exit(main())
