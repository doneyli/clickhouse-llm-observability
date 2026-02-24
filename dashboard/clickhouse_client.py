"""Direct ClickHouse client for fast analytics queries."""

import os
import hashlib
import time
from datetime import datetime

import httpx

_cache: dict[str, tuple[object, float]] = {}
_CACHE_TTL = int(os.environ.get("DASHBOARD_CACHE_TTL", "60"))


def _cache_key(query: str, params: dict | None) -> str:
    raw = f"{query}:{sorted((params or {}).items())}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> object | None:
    if key in _cache:
        data, expires = _cache[key]
        if time.time() < expires:
            return data
        del _cache[key]
    return None


def _cache_set(key: str, data: object) -> None:
    _cache[key] = (data, time.time() + _CACHE_TTL)


def _get_ch_url() -> str:
    host = os.environ.get("CLICKHOUSE_HOST", "langfuse-clickhouse")
    port = os.environ.get("CLICKHOUSE_HTTP_PORT", "8123")
    return f"http://{host}:{port}"


def _get_ch_auth() -> tuple[str, str]:
    user = os.environ.get("CLICKHOUSE_USER", "langfuse")
    password = os.environ.get("CLICKHOUSE_PASSWORD", "langfuse123")
    return user, password


def _normalize_ts(ts: str) -> str:
    """Convert ISO 8601 (2026-02-17T04:03:55.365Z) to ClickHouse format (2026-02-17 04:03:55.365)."""
    return ts.replace("T", " ").rstrip("Z")


def _where_clause(from_ts: str | None, to_ts: str | None, project: str | None,
                  table_alias: str = "", ts_col: str = "timestamp") -> tuple[str, dict]:
    """Build WHERE conditions and params for time range + project filter."""
    prefix = f"{table_alias}." if table_alias else ""
    conditions = [f"{prefix}is_deleted = 0"]
    params = {}

    if from_ts:
        conditions.append(f"{prefix}{ts_col} >= {{from_ts:DateTime64(3)}}")
        params["from_ts"] = _normalize_ts(from_ts)
    if to_ts:
        conditions.append(f"{prefix}{ts_col} <= {{to_ts:DateTime64(3)}}")
        params["to_ts"] = _normalize_ts(to_ts)
    if project:
        conditions.append(f"{prefix}name = {{project:String}}")
        params["project"] = project

    return " AND ".join(conditions), params


async def query(sql: str, params: dict | None = None, use_cache: bool = True) -> list[dict]:
    """Execute a ClickHouse query and return rows as list of dicts."""
    key = _cache_key(sql, params)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    url = _get_ch_url()
    user, password = _get_ch_auth()

    # Use JSON format for easy parsing
    full_sql = sql.rstrip(";") + " FORMAT JSONEachRow"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            content=full_sql,
            params={"user": user, "password": password,
                    **({"param_" + k: v for k, v in (params or {}).items()})},
            headers={"Content-Type": "text/plain"},
        )
        resp.raise_for_status()

    text = resp.text.strip()
    if not text:
        result = []
    else:
        import json
        result = [json.loads(line) for line in text.split("\n") if line.strip()]

    if use_cache:
        _cache_set(key, result)
    return result


async def query_one(sql: str, params: dict | None = None) -> dict | None:
    """Execute query and return first row."""
    rows = await query(sql, params)
    return rows[0] if rows else None


# ============== Pre-built analytics queries ==============

async def get_kpi(from_ts: str | None = None, to_ts: str | None = None,
                  project: str | None = None) -> dict:
    """All KPI metrics in two fast queries."""
    where, params = _where_clause(from_ts, to_ts, project)

    # Main counts
    row = await query_one(f"""
        SELECT
            count() as traces_count,
            uniqExact(session_id) as sessions_count,
            uniqExact(name) as projects_count,
            uniqExact(toDate(timestamp)) as active_days
        FROM default.traces
        WHERE {where}
    """, params)

    # Traces per session percentiles
    tps = await query_one(f"""
        SELECT
            medianExact(cnt) as median_tps,
            quantileExact(0.9)(cnt) as p90_tps
        FROM (
            SELECT session_id, count() as cnt
            FROM default.traces
            WHERE {where} AND session_id IS NOT NULL AND session_id != ''
            GROUP BY session_id
        )
    """, params)

    # Cost + latency from observations (joined to traces for filtering)
    obs_where, obs_params = _where_clause(from_ts, to_ts, project, "t")
    cost = await query_one(f"""
        SELECT
            sum(coalesce(o.total_cost, 0)) as total_cost,
            avg(if(o.end_time IS NOT NULL,
                dateDiff('millisecond', o.start_time, o.end_time), NULL)) as avg_latency_ms
        FROM default.observations o
        JOIN default.traces t ON o.trace_id = t.id
        WHERE {obs_where} AND o.is_deleted = 0
    """, obs_params)

    # Avg score
    score_where, score_params = _where_clause(from_ts, to_ts, None, "", "timestamp")
    score = await query_one(f"""
        SELECT avg(value) as avg_score
        FROM default.scores
        WHERE {score_where}
    """, score_params)

    return {
        "traces_count": int(row["traces_count"]) if row else 0,
        "sessions_count": int(row["sessions_count"]) if row else 0,
        "projects_count": int(row["projects_count"]) if row else 0,
        "active_days": int(row["active_days"]) if row else 0,
        "median_traces_per_session": float(tps["median_tps"]) if tps else 0,
        "p90_traces_per_session": float(tps["p90_tps"]) if tps else 0,
        "total_cost": round(float(cost["total_cost"] or 0), 6) if cost else 0,
        "avg_latency_ms": round(float(cost["avg_latency_ms"] or 0), 1) if cost else 0,
        "avg_score": round(float(score["avg_score"]), 3) if score and score["avg_score"] else None,
    }


async def get_sessions(from_ts: str | None = None, to_ts: str | None = None,
                       project: str | None = None, search: str | None = None,
                       limit: int = 200) -> list[dict]:
    """Session list with pre-aggregated metrics."""
    where, params = _where_clause(from_ts, to_ts, project)
    params["limit"] = limit

    search_clause = ""
    if search:
        search_clause = "AND (session_id ILIKE {search:String} OR name ILIKE {search:String})"
        params["search"] = f"%{search}%"

    rows = await query(f"""
        SELECT
            session_id as id,
            count() as trace_count,
            min(timestamp) as first_trace,
            max(timestamp) as last_trace,
            any(name) as project,
            coalesce(sum(trace_cost), 0) as total_cost,
            coalesce(sum(trace_latency_ms), 0) as total_latency_ms,
            toUInt64(coalesce(sum(trace_tokens), 0)) as total_tokens
        FROM (
            SELECT
                t.id as tid,
                t.session_id,
                t.timestamp,
                t.name,
                coalesce(sum(coalesce(o.total_cost, 0)), 0) as trace_cost,
                coalesce(sum(if(o.end_time IS NOT NULL,
                    dateDiff('millisecond', o.start_time, o.end_time), 0)), 0) as trace_latency_ms,
                toUInt64(coalesce(sum(o.usage_details['total']), 0)) as trace_tokens
            FROM default.traces t
            LEFT JOIN default.observations o ON t.id = o.trace_id AND o.is_deleted = 0
            WHERE {where} AND t.session_id IS NOT NULL AND t.session_id != ''
                {search_clause}
            GROUP BY t.id, t.session_id, t.timestamp, t.name
        )
        GROUP BY session_id
        ORDER BY last_trace DESC
        LIMIT {{limit:UInt32}}
    """, params)

    for r in rows:
        r["total_cost"] = round(float(r.get("total_cost", 0)), 6)
        r["total_latency_ms"] = round(float(r.get("total_latency_ms", 0)), 1)
        r["total_tokens"] = int(r.get("total_tokens", 0))
        r["trace_count"] = int(r.get("trace_count", 0))
    return rows


async def get_heatmap(from_ts: str | None = None, to_ts: str | None = None,
                      project: str | None = None) -> dict:
    """Daily trace counts for heatmap."""
    where, params = _where_clause(from_ts, to_ts, project)

    rows = await query(f"""
        SELECT toString(toDate(timestamp)) as date, count() as count
        FROM default.traces
        WHERE {where}
        GROUP BY date
        ORDER BY date
    """, params)

    max_count = max((int(r["count"]) for r in rows), default=0)
    return {
        "days": [{"date": r["date"], "count": int(r["count"])} for r in rows],
        "max_count": max_count,
    }


async def get_hourly(from_ts: str | None = None, to_ts: str | None = None,
                     project: str | None = None) -> dict:
    """7x24 activity matrix."""
    where, params = _where_clause(from_ts, to_ts, project)

    rows = await query(f"""
        SELECT
            toDayOfWeek(timestamp) as dow,
            toHour(timestamp) as hour,
            count() as cnt
        FROM default.traces
        WHERE {where}
        GROUP BY dow, hour
    """, params)

    # toDayOfWeek: 1=Mon, 7=Sun → map to 0-indexed
    matrix = [[0] * 24 for _ in range(7)]
    for r in rows:
        d = int(r["dow"]) - 1  # 0=Mon
        h = int(r["hour"])
        matrix[d][h] = int(r["cnt"])

    day_totals = [sum(row) for row in matrix]
    max_count = max(max(row) for row in matrix) if any(any(row) for row in matrix) else 0

    return {"matrix": matrix, "day_totals": day_totals, "max_count": max_count}


async def get_top_sessions(sort: str = "traces", limit: int = 10,
                           from_ts: str | None = None, to_ts: str | None = None,
                           project: str | None = None) -> list[dict]:
    """Top sessions ranked by metric."""
    where, params = _where_clause(from_ts, to_ts, project)
    params["limit"] = limit

    order = {
        "traces": "trace_count DESC",
        "duration": "total_duration_ms DESC",
        "cost": "total_cost DESC",
    }.get(sort, "trace_count DESC")

    rows = await query(f"""
        SELECT
            session_id as id,
            any(name) as project,
            count() as trace_count,
            coalesce(sum(trace_latency), 0) as total_duration_ms,
            coalesce(sum(trace_cost), 0) as total_cost,
            min(timestamp) as first_trace
        FROM (
            SELECT
                t.id as tid,
                t.session_id,
                t.timestamp,
                t.name,
                coalesce(sum(if(o.end_time IS NOT NULL,
                    dateDiff('millisecond', o.start_time, o.end_time), 0)), 0) as trace_latency,
                coalesce(sum(coalesce(o.total_cost, 0)), 0) as trace_cost
            FROM default.traces t
            LEFT JOIN default.observations o ON t.id = o.trace_id AND o.is_deleted = 0
            WHERE {where} AND t.session_id IS NOT NULL AND t.session_id != ''
            GROUP BY t.id, t.session_id, t.timestamp, t.name
        )
        GROUP BY session_id
        ORDER BY {order}
        LIMIT {{limit:UInt32}}
    """, params)

    for r in rows:
        r["total_cost"] = round(float(r.get("total_cost", 0)), 6)
        r["total_duration_ms"] = round(float(r.get("total_duration_ms", 0)), 1)
        r["trace_count"] = int(r.get("trace_count", 0))
    return rows


async def get_tools(from_ts: str | None = None, to_ts: str | None = None) -> list[dict]:
    """Span usage analytics."""
    where, params = _where_clause(from_ts, to_ts, None, "", "start_time")

    rows = await query(f"""
        SELECT
            name,
            count() as count,
            avg(if(end_time IS NOT NULL,
                dateDiff('millisecond', start_time, end_time), NULL)) as avg_latency_ms
        FROM default.observations
        WHERE {where} AND type = 'SPAN'
        GROUP BY name
        ORDER BY count DESC
    """, params)

    return [{
        "name": r["name"],
        "count": int(r["count"]),
        "avg_latency_ms": round(float(r.get("avg_latency_ms") or 0), 1),
    } for r in rows]


async def get_scores() -> list[dict]:
    """Score distributions."""
    rows = await query("""
        SELECT
            name,
            count() as count,
            avg(value) as avg,
            min(value) as min,
            max(value) as max
        FROM default.scores
        WHERE is_deleted = 0
        GROUP BY name
        ORDER BY name
    """)

    return [{
        "name": r["name"],
        "count": int(r["count"]),
        "avg": round(float(r["avg"]), 3),
        "min": round(float(r["min"]), 3),
        "max": round(float(r["max"]), 3),
        "values": [],
    } for r in rows]


async def get_projects(from_ts: str | None = None, to_ts: str | None = None) -> list[str]:
    """Distinct project names."""
    where, params = _where_clause(from_ts, to_ts, None)

    rows = await query(f"""
        SELECT DISTINCT name
        FROM default.traces
        WHERE {where} AND name != ''
        ORDER BY name
    """, params)

    return [r["name"] for r in rows]


async def get_session_detail(session_id: str) -> dict:
    """Full session detail with traces and scores."""
    rows = await query("""
        SELECT
            t.id,
            t.name,
            t.timestamp,
            substring(coalesce(t.input, ''), 1, 500) as input,
            substring(coalesce(t.output, ''), 1, 500) as output,
            coalesce(sum(if(o.end_time IS NOT NULL,
                dateDiff('millisecond', o.start_time, o.end_time), 0)), 0) as latency_ms,
            coalesce(sum(coalesce(o.total_cost, 0)), 0) as cost,
            toUInt64(coalesce(sum(o.usage_details['total']), 0)) as tokens
        FROM default.traces t
        LEFT JOIN default.observations o ON t.id = o.trace_id AND o.is_deleted = 0
        WHERE t.is_deleted = 0 AND t.session_id = {session_id:String}
        GROUP BY t.id, t.name, t.timestamp, t.input, t.output
        ORDER BY t.timestamp
    """, {"session_id": session_id})

    # Get scores for these traces
    trace_ids = [r["id"] for r in rows]
    scores_map: dict[str, dict[str, float]] = {}
    if trace_ids:
        score_rows = await query("""
            SELECT trace_id, name, value
            FROM default.scores
            WHERE is_deleted = 0 AND trace_id IN {trace_ids:Array(String)}
        """, {"trace_ids": trace_ids})
        for s in score_rows:
            tid = s["trace_id"]
            if tid not in scores_map:
                scores_map[tid] = {}
            scores_map[tid][s["name"]] = round(float(s["value"]), 3)

    traces = []
    total_cost = 0.0
    total_tokens = 0
    total_latency = 0.0

    for r in rows:
        cost = float(r.get("cost", 0))
        lat = float(r.get("latency_ms", 0))
        tok = int(r.get("tokens", 0))
        total_cost += cost
        total_tokens += tok
        total_latency += lat

        traces.append({
            "id": r["id"],
            "name": r.get("name"),
            "timestamp": r.get("timestamp"),
            "input": r.get("input") or None,
            "output": r.get("output") or None,
            "latency_ms": round(lat, 1),
            "cost": round(cost, 6),
            "tokens": tok,
            "scores": scores_map.get(r["id"], {}),
        })

    return {
        "id": session_id,
        "traces": traces,
        "total_cost": round(total_cost, 6),
        "total_tokens": total_tokens,
        "total_latency_ms": round(total_latency, 1),
        "trace_count": len(traces),
    }


async def get_export_traces(from_ts: str | None = None, to_ts: str | None = None,
                            project: str | None = None) -> list[dict]:
    """All traces for CSV export."""
    where, params = _where_clause(from_ts, to_ts, project)

    return await query(f"""
        SELECT
            t.id as trace_id,
            t.session_id,
            t.name,
            t.timestamp,
            coalesce(sum(if(o.end_time IS NOT NULL,
                dateDiff('millisecond', o.start_time, o.end_time), 0)), 0) / 1000.0 as latency_s,
            coalesce(sum(coalesce(o.total_cost, 0)), 0) as total_cost,
            toUInt64(coalesce(sum(o.usage_details['input']), 0)) as input_tokens,
            toUInt64(coalesce(sum(o.usage_details['output']), 0)) as output_tokens,
            toUInt64(coalesce(sum(o.usage_details['total']), 0)) as total_tokens,
            substring(coalesce(t.input, ''), 1, 1000) as input,
            substring(coalesce(t.output, ''), 1, 1000) as output
        FROM default.traces t
        LEFT JOIN default.observations o ON t.id = o.trace_id AND o.is_deleted = 0
        WHERE {where}
        GROUP BY t.id, t.session_id, t.name, t.timestamp, t.input, t.output
        ORDER BY t.timestamp DESC
    """, params, use_cache=False)
