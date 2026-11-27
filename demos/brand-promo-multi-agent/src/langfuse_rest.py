"""The few Langfuse public-API calls the seed scripts make, with failures that surface.

Standard library only. Every HTTP or network failure raises `ApiError`, so a seeder can
count it instead of printing a warning and carrying on. Pagination is bounded and guards
against a server that hands back the same cursor twice.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TIMEOUT_S = 15
MAX_PAGES = 50


class ApiError(Exception):
    """An HTTP error (status set) or a network failure (status None)."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


class LangfuseEnvError(Exception):
    """The demo's .env is missing, or lacks a required Langfuse value."""


def langfuse_settings(environ: dict[str, str] | None = None) -> tuple[str, str, str]:
    """(base_url, public_key, secret_key) from the environment `src.config.load_env()` set.

    Raises LangfuseEnvError before any request when a value is missing.
    """
    env = os.environ if environ is None else environ
    base = env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or ""
    pk, sk = env.get("LANGFUSE_PUBLIC_KEY", ""), env.get("LANGFUSE_SECRET_KEY", "")
    missing = [n for n, v in (("LANGFUSE_HOST", base), ("LANGFUSE_PUBLIC_KEY", pk),
                              ("LANGFUSE_SECRET_KEY", sk)) if not v]
    if missing:
        raise LangfuseEnvError(
            f"missing {', '.join(missing)} in demos/brand-promo-multi-agent/.env "
            "(shell-exported LANGFUSE_* are ignored); cp .env.example .env and fill it in")
    return base.rstrip("/"), pk, sk


def quote(name: str) -> str:
    """A path segment for a name that may contain slashes, such as `promo-planner/a/b`."""
    return urllib.parse.quote(name, safe="")


class Client:
    def __init__(self, base_url: str, public_key: str, secret_key: str,
                 timeout: float = TIMEOUT_S) -> None:
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "brand-promo-seed/1"}
        self.timeout = timeout

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Client:
        return cls(*langfuse_settings(environ))

    def request(self, method: str, path: str, params: dict[str, Any] | None = None,
                body: Any = None) -> Any:
        query = {k: (str(v).lower() if isinstance(v, bool) else v)
                 for k, v in (params or {}).items() if v is not None}
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
            raise ApiError(e.code, detail or str(e.reason)) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body=body)

    def paged(self, path: str, params: dict[str, Any] | None = None,
              max_pages: int = MAX_PAGES) -> list[dict]:
        """Follow page/totalPages (score configs, queues, queue items, dataset items)."""
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **dict(params or {}, limit=100, page=page)) or {}
            out += d.get("data") or []
            if page >= int((d.get("meta") or {}).get("totalPages") or 1):
                return out
        raise ApiError(None, f"{path}: more than {max_pages} pages")

    def cursor(self, path: str, params: dict[str, Any], cap: int = 1000,
               max_pages: int = MAX_PAGES) -> list[dict]:
        """Follow `meta.cursor` (Observations v2, Scores v3, experiments).

        A page may be empty while a cursor is still returned, so stop on a missing cursor,
        not on an empty page. A cursor seen twice would loop forever: raise instead.
        """
        out: list[dict] = []
        cur, seen = None, set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:
                raise ApiError(None, f"{path}: the server repeated cursor {cur[:16]}…")
            seen.add(cur)
        raise ApiError(None, f"{path}: more than {max_pages} pages")


class Tally:
    """Created / updated / unchanged / failed counts for one seeder, printed truthfully."""

    def __init__(self, what: str) -> None:
        self.what = what
        self.counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
        self.errors: list[str] = []

    def add(self, outcome: str, detail: str = "") -> None:
        self.counts[outcome] += 1
        if outcome == "failed":
            self.errors.append(detail)

    @property
    def failed(self) -> bool:
        return self.counts["failed"] > 0

    def line(self) -> str:
        c = self.counts
        return (f"{self.what}: {c['created']} created, {c['updated']} updated, "
                f"{c['unchanged']} unchanged, {c['failed']} failed")
