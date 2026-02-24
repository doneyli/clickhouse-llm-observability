"""Async Langfuse REST API client with in-memory caching."""

import base64
import hashlib
import os
import time
from datetime import datetime, timezone

import httpx


# Cache: {key: (data, expires_at)}
_cache: dict[str, tuple[object, float]] = {}
_CACHE_TTL = int(os.environ.get("DASHBOARD_CACHE_TTL", "60"))


def _cache_key(method: str, url: str, params: dict | None) -> str:
    raw = f"{method}:{url}:{sorted((params or {}).items())}"
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


def _get_auth_header() -> str:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return f"Basic {credentials}"


def _get_base_url() -> str:
    # Inside Docker, use internal URL; outside, use LANGFUSE_BASE_URL
    return os.environ.get(
        "LANGFUSE_HOST",
        os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3001"),
    )


async def _api_get(path: str, params: dict | None = None) -> dict:
    """Make authenticated GET request to Langfuse API."""
    url = f"{_get_base_url()}/api/public{path}"
    key = _cache_key("GET", url, params)

    cached = _cache_get(key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            url,
            params=params,
            headers={"Authorization": _get_auth_header()},
        )
        resp.raise_for_status()
        data = resp.json()

    _cache_set(key, data)
    return data


async def fetch_traces(
    from_ts: str | None = None,
    to_ts: str | None = None,
    name: str | None = None,
    session_id: str | None = None,
    page: int = 1,
    limit: int = 100,
) -> dict:
    """Fetch traces with optional filters."""
    params = {"page": page, "limit": limit}
    if from_ts:
        params["fromTimestamp"] = from_ts
    if to_ts:
        params["toTimestamp"] = to_ts
    if name:
        params["name"] = name
    if session_id:
        params["sessionId"] = session_id
    return await _api_get("/traces", params)


async def fetch_all_traces(
    from_ts: str | None = None,
    to_ts: str | None = None,
    name: str | None = None,
) -> list[dict]:
    """Fetch all traces with pagination."""
    all_traces = []
    page = 1
    while True:
        result = await fetch_traces(
            from_ts=from_ts, to_ts=to_ts, name=name, page=page, limit=100
        )
        traces = result.get("data", [])
        all_traces.extend(traces)
        meta = result.get("meta", {})
        total = meta.get("totalItems", len(traces))
        if len(all_traces) >= total or not traces:
            break
        page += 1
    return all_traces


async def fetch_observations(
    trace_id: str | None = None,
    obs_type: str | None = None,
    page: int = 1,
    limit: int = 100,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> dict:
    """Fetch observations (spans, generations, events)."""
    params = {"page": page, "limit": limit}
    if trace_id:
        params["traceId"] = trace_id
    if obs_type:
        params["type"] = obs_type
    if from_ts:
        params["fromStartTime"] = from_ts
    if to_ts:
        params["toStartTime"] = to_ts
    return await _api_get("/observations", params)


async def fetch_all_observations(
    obs_type: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> list[dict]:
    """Fetch all observations with pagination."""
    all_obs = []
    page = 1
    while True:
        result = await fetch_observations(
            obs_type=obs_type, page=page, limit=100,
            from_ts=from_ts, to_ts=to_ts,
        )
        obs = result.get("data", [])
        all_obs.extend(obs)
        meta = result.get("meta", {})
        total = meta.get("totalItems", len(obs))
        if len(all_obs) >= total or not obs:
            break
        page += 1
    return all_obs


async def fetch_scores(
    page: int = 1,
    limit: int = 100,
) -> dict:
    """Fetch scores."""
    params = {"page": page, "limit": limit}
    return await _api_get("/scores", params)


async def fetch_all_scores() -> list[dict]:
    """Fetch all scores with pagination."""
    all_scores = []
    page = 1
    while True:
        result = await fetch_scores(page=page, limit=100)
        scores = result.get("data", [])
        all_scores.extend(scores)
        meta = result.get("meta", {})
        total = meta.get("totalItems", len(scores))
        if len(all_scores) >= total or not scores:
            break
        page += 1
    return all_scores


async def fetch_sessions(
    page: int = 1,
    limit: int = 50,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> dict:
    """Fetch sessions from Langfuse API."""
    params = {"page": page, "limit": limit}
    if from_ts:
        params["fromTimestamp"] = from_ts
    if to_ts:
        params["toTimestamp"] = to_ts
    return await _api_get("/sessions", params)


def clear_cache() -> None:
    """Clear the entire cache."""
    _cache.clear()
