"""Measured, async ClickHouse client over the HTTP interface.

Every query returns ClickHouse's own server-side timing next to the client's
round-trip time, so a trace can show "the database took 18 ms; the network took 70".

Why raw HTTP (httpx) instead of a driver: the HTTP interface gives exactly what the
demo needs — typed server-side parameters (``{name:Type}`` + ``param_name=``), per-query
settings as URL parameters, a caller-chosen ``query_id``, and a ``statistics`` block in
the JSON body with the server's elapsed time and rows/bytes read — with real asyncio
concurrency and no thread pool in between (thread pools drop OpenTelemetry context).
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import httpx

from .config import DEFAULT_QUERY_SETTINGS, ClickHouseSettings


class ClickHouseError(RuntimeError):
    def __init__(self, message: str, *, code: Optional[str] = None, query_id: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.query_id = query_id


class UnsafeSQLError(ValueError):
    """The SQL failed the allow-list before it was sent."""


@dataclass(frozen=True)
class QueryStats:
    server_ms: float  # ClickHouse's own elapsed time (statistics.elapsed)
    roundtrip_ms: float  # client wall clock: request out -> body parsed
    read_rows: int
    read_bytes: int
    result_rows: int
    query_id: str

    @property
    def network_ms(self) -> float:
        return max(self.roundtrip_ms - self.server_ms, 0.0)

    @property
    def rows_per_sec(self) -> float:
        return self.read_rows / (self.server_ms / 1000.0) if self.server_ms > 0 else 0.0

    def as_metadata(self) -> dict[str, Any]:
        return {
            "db_system": "clickhouse",
            "db_server_ms": round(self.server_ms, 2),
            "db_roundtrip_ms": round(self.roundtrip_ms, 2),
            "db_network_ms": round(self.network_ms, 2),
            "db_read_rows": self.read_rows,
            "db_read_bytes": self.read_bytes,
            "db_result_rows": self.result_rows,
            "db_rows_per_sec": round(self.rows_per_sec),
            "db_query_id": self.query_id,
        }


@dataclass
class QueryResult:
    columns: list[str]
    types: list[str]
    rows: list[list[Any]]
    stats: QueryStats

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def first(self) -> Optional[dict[str, Any]]:
        recs = self.records()
        return recs[0] if recs else None


# --- SQL allow-list -----------------------------------------------------------------
# Defense in depth only: the read-only user (SELECT on the demo database, nothing else)
# is the real fence. This rejects the obvious escapes before a request is even sent.
_FORBIDDEN = re.compile(
    r"\b(insert|alter|create|drop|truncate|rename|attach|detach|grant|revoke|optimize|"
    r"system|kill|set|settings|into\s+outfile|s3|s3cluster|url|file|remote|remotesecure|"
    r"cluster|clusterallreplicas|mysql|postgresql|jdbc|odbc|hdfs|input|executable|"
    r"dictionary|azureblobstorage|gcs)\s*[\(\s]",
    re.IGNORECASE,
)


def check_sql(sql: str, *, allowed_tables: Sequence[str]) -> None:
    """Raise UnsafeSQLError unless ``sql`` is one read-only SELECT over allowed tables."""
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        raise UnsafeSQLError("one statement only")
    if not re.match(r"(?is)^\s*(select|with)\b", stripped):
        raise UnsafeSQLError("must start with SELECT or WITH")
    m = _FORBIDDEN.search(stripped)
    if m:
        raise UnsafeSQLError(f"forbidden keyword or table function: {m.group(1)!r}")
    # Database-qualified references must be on the list. Bare names are left alone: they
    # resolve inside the connection's own database (which holds only the demo table), and
    # a bare-name check would trip on `extract(YEAR FROM FlightDate)` and friends.
    tables = {t.lower() for t in allowed_tables}
    for ref in re.findall(r"(?i)\b(?:from|join)\s+([a-zA-Z_]\w*\.[a-zA-Z_]\w*)", stripped):
        if ref.lower() not in tables:
            raise UnsafeSQLError(f"table {ref!r} is not allowed (allowed: {sorted(tables)})")


# --- parameter encoding -------------------------------------------------------------
def encode_param(value: Any) -> str:
    """Text form of a value for ``param_<name>=`` (ClickHouse parses it by the declared type)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_quote(v) for v in value) + "]"
    return str(value)


def _quote(v: Any) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def _num(v: Any) -> Any:
    """JSON output quotes 64-bit ints by default; turn numeric strings back into numbers.

    Also strips the NUL padding FixedString columns carry (the public playground stores
    airport codes as FixedString(5), so 'ATL' comes back as 'ATL\\x00\\x00').
    """
    if isinstance(v, str):
        v = v.rstrip("\x00")
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v
    return v


@dataclass
class ClickHouseClient:
    settings: ClickHouseSettings
    max_connections: int = 64
    timeout_s: float = 60.0
    query_settings: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_QUERY_SETTINGS))
    _http: Optional[httpx.AsyncClient] = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> "ClickHouseClient":
        self._http = httpx.AsyncClient(
            auth=(self.settings.user, self.settings.password),
            timeout=self.timeout_s,
            limits=httpx.Limits(max_connections=self.max_connections,
                                max_keepalive_connections=self.max_connections),
            http2=False,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def allowed_tables(self) -> list[str]:
        return [self.settings.qualified_table]

    async def query(
        self,
        sql: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        settings: Optional[Mapping[str, str]] = None,
        check: bool = True,
    ) -> QueryResult:
        """Run one SELECT. ``params`` bind to ``{name:Type}`` placeholders server-side."""
        if self._http is None:
            raise RuntimeError("use `async with ClickHouseClient(...)`")
        if check:
            check_sql(sql, allowed_tables=self.allowed_tables)
        query_id = f"analytics-agentic-speed-{uuid.uuid4()}"
        url_params: dict[str, str] = {
            "database": self.settings.database,
            "default_format": "JSONCompact",
            "query_id": query_id,
            **dict(self.query_settings),
            **dict(settings or {}),
        }
        for name, value in (params or {}).items():
            url_params[f"param_{name}"] = encode_param(value)

        t0 = time.perf_counter()
        resp = await self._http.post(self.settings.url, params=url_params, content=sql.encode())
        body = resp.content
        roundtrip_ms = (time.perf_counter() - t0) * 1000.0
        if resp.status_code != 200:
            msg = body.decode(errors="replace").strip()
            if msg.startswith("{"):  # JSON formats wrap the error: {"meta":[],…,"exception":"Code: …"}
                try:
                    msg = resp.json().get("exception", msg)
                except ValueError:
                    pass
            raise ClickHouseError(msg[:2000], code=resp.headers.get("X-ClickHouse-Exception-Code"),
                                  query_id=query_id)
        data = resp.json()
        stats = data.get("statistics", {})
        rows = [[_num(v) for v in row] for row in data.get("data", [])]
        meta = data.get("meta", [])
        return QueryResult(
            columns=[m["name"] for m in meta],
            types=[m["type"] for m in meta],
            rows=rows,
            stats=QueryStats(
                server_ms=float(stats.get("elapsed", 0.0)) * 1000.0,
                roundtrip_ms=roundtrip_ms,
                read_rows=int(stats.get("rows_read", 0)),
                read_bytes=int(stats.get("bytes_read", 0)),
                result_rows=len(rows),
                query_id=query_id,
            ),
        )

    async def ping(self) -> float:
        """Round-trip ms of ``SELECT 1`` (also wakes an idle service)."""
        res = await self.query("SELECT 1", check=False)
        return res.stats.roundtrip_ms


async def gather_bounded(coros: Sequence[Any], limit: int) -> list[Any]:
    """``asyncio.gather`` with at most ``limit`` in flight; exceptions are returned, not raised.

    Tasks are created inside the caller's context, so each one inherits the current
    OpenTelemetry span as its parent (that is what keeps 300 query spans nested).
    """
    sem = asyncio.Semaphore(limit)

    async def run(c: Any) -> Any:
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)
