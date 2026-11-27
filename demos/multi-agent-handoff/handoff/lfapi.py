"""
A thin client for the Langfuse public REST API, for the scripts that need endpoints the
Python SDK does not wrap (dashboards, annotation queues, prompt labels, evaluators).

Keys come from the demo's .env through handoff.config, never from the shell.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import httpx

from handoff import config


class LangfuseAPIError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, text: str):
        super().__init__(f"{method} {path} → {status}: {text[:600]}")
        self.status = status


def api(method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None,
        timeout: float = 60) -> dict:
    auth = (config.setting("LANGFUSE_PUBLIC_KEY"), config.setting("LANGFUSE_SECRET_KEY"))
    r = httpx.request(method, f"{config.LANGFUSE_BASE_URL}{path}", json=body, params=params, auth=auth,
                      timeout=timeout)
    if r.status_code >= 400:
        raise LangfuseAPIError(method, path, r.status_code, r.text)
    return r.json() if r.content else {}


def paginate(path: str, params: Optional[dict] = None, limit: int = 100) -> list[dict]:
    """Page-numbered (most endpoints) and cursor-paged (v2/v3) lists, both read to the end."""
    out, cursor, page = [], None, 1
    cursor_paged = "/v2/" in path or "/v3/" in path
    while True:
        p = {"limit": limit, **(params or {})}
        if cursor_paged:
            if cursor:
                p["cursor"] = cursor
        else:
            p["page"] = page
        d = api("GET", path, params=p)
        out += d.get("data") or []
        meta = d.get("meta") or {}
        if cursor_paged:
            cursor = meta.get("cursor") or meta.get("nextCursor")
            if not cursor or not d.get("data"):
                return out
        else:
            if not d.get("data") or page >= (meta.get("totalPages") or 1):
                return out
            page += 1


@lru_cache(maxsize=1)
def project_id() -> str:
    return api("GET", "/api/public/projects")["data"][0]["id"]


def ui(path: str = "") -> str:
    return f"{config.LANGFUSE_BASE_URL}/project/{project_id()}{path}"
