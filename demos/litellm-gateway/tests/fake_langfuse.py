"""An offline stand-in for the Langfuse public API and the LiteLLM liveliness probe.

It is installed in place of `urllib.request.urlopen`, so the code under test builds real
URLs and query strings. It honours field projection, filters, cursor and page traversal,
and answers 400 for a query parameter the real endpoint does not take. `mode="v3"` models
the pinned self-hosted 3.221.1 server: Observations v2 answers 404 and only the v1 list
(no session, user or trace-name fields on its rows) is served.
"""
import base64
import io
import json
import urllib.error
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

V2_GROUPS = {
    "core": ["id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"],
    "basic": ["name", "level", "statusMessage", "version", "environment", "userId", "sessionId",
              "isRootObservation"],
    "time": [], "io": [], "metadata": [], "model": [], "usage": [], "prompt": [], "metrics": [],
    "trace_context": ["tags", "release", "traceName"],
}
V2_PARAMS = {"fields", "expandMetadata", "limit", "cursor", "name", "userId", "sessionId", "type",
             "traceId", "level", "parentObservationId", "isRootObservation", "environment",
             "fromStartTime", "toStartTime", "version", "filter"}
V1_PARAMS = {"page", "limit", "name", "userId", "type", "traceId", "level", "parentObservationId",
             "environment", "fromStartTime", "toStartTime", "version"}
V1_FIELDS = ["id", "traceId", "type", "name", "startTime", "endTime", "parentObservationId"]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class StubResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.payload = json.dumps(payload).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self.payload


class FakeLangfuse:
    def __init__(self, *, mode: str = "v4", projects: Optional[List[dict]] = None,
                 observations: Optional[List[dict]] = None, page_size: int = 1000,
                 empty_pages: int = 0, repeat_cursor: bool = False,
                 fail: Optional[Dict[str, int]] = None, gateway_up: bool = True,
                 keys: tuple = ("pk-good", "sk-good")) -> None:
        self.mode = mode
        self.projects = [{"id": "proj-1", "name": "LLM Observability Demo"}] if projects is None else projects
        self.observations = observations or []
        self.page_size = page_size
        self.empty_pages = empty_pages
        self.repeat_cursor = repeat_cursor
        self.fail = fail or {}
        self.gateway_up = gateway_up
        self.keys = keys
        self.requests: List[str] = []
        self.auth_headers: List[str] = []

    # -- transport --------------------------------------------------------------------
    def urlopen(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.requests.append(url)
        parts = urlsplit(url)
        path = parts.path
        if path.endswith("/health/liveliness"):
            if not self.gateway_up:
                raise urllib.error.URLError("Connection refused")
            return StubResponse("I'm alive!")
        auth = req.get_header("Authorization") if hasattr(req, "get_header") else None
        self.auth_headers.append(auth or "")
        q = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        for prefix, status in self.fail.items():
            if path.startswith(prefix):
                return self._error(url, status, "forced failure")
        expected = "Basic " + base64.b64encode(f"{self.keys[0]}:{self.keys[1]}".encode()).decode()
        if auth != expected:
            return self._error(url, 401, "Invalid credentials")
        if path == "/api/public/projects":
            if q:
                return self._error(url, 400, f"unexpected params {sorted(q)}")
            return StubResponse({"data": self.projects})
        if path == "/api/public/v2/observations":
            if self.mode == "v3":
                return self._error(url, 404, "only available in a Langfuse v4 write mode")
            return self._v2(url, q)
        if path == "/api/public/observations":
            return self._v1(url, q)
        return self._error(url, 404, f"no route {path}")

    def _error(self, url: str, status: int, msg: str):
        raise urllib.error.HTTPError(url, status, msg, {}, io.BytesIO(json.dumps({"message": msg}).encode()))

    # -- filtering --------------------------------------------------------------------
    def _matching(self, q: Dict[str, str]) -> List[dict]:
        rows = list(self.observations)
        conditions = json.loads(q["filter"]) if "filter" in q else []
        filtered_cols = {c["column"] for c in conditions}
        for c in conditions:
            if c["type"] != "stringOptions" or c["operator"] != "any of":
                raise ValueError(f"unsupported filter {c}")
            rows = [r for r in rows if r.get(c["column"]) in c["value"]]
        for key in ("userId", "sessionId", "traceId", "name", "type"):
            if key in q and key not in filtered_cols:
                rows = [r for r in rows if r.get(key) == q[key]]
        if "fromStartTime" in q:
            rows = [r for r in rows if _ts(r["startTime"]) >= _ts(q["fromStartTime"])]
        if "toStartTime" in q:
            rows = [r for r in rows if _ts(r["startTime"]) <= _ts(q["toStartTime"])]
        return sorted(rows, key=lambda r: r["startTime"], reverse=True)

    def _v2(self, url: str, q: Dict[str, str]):
        bad = set(q) - V2_PARAMS
        if bad:
            return self._error(url, 400, f"unknown params {sorted(bad)}")
        groups = (q.get("fields") or "core,basic").split(",")
        if any(g not in V2_GROUPS for g in groups):
            return self._error(url, 400, f"bad fields {groups}")
        if int(q.get("limit", 50)) > 1000:
            return self._error(url, 400, "limit over 1000")
        keep = set(V2_GROUPS["core"])
        for g in groups:
            keep |= set(V2_GROUPS[g])
        try:
            rows = self._matching(q)
        except ValueError as e:
            return self._error(url, 400, str(e))
        # The cursor encodes a page index. Optional empty pages precede the data.
        idx = int(base64.b64decode(q["cursor"]).decode()) if q.get("cursor") else 0
        if idx < self.empty_pages:
            page = []
        else:
            start = (idx - self.empty_pages) * self.page_size
            page = rows[start:start + self.page_size]
        remaining = idx < self.empty_pages or (idx - self.empty_pages + 1) * self.page_size < len(rows)
        meta: Dict[str, Any] = {}
        if remaining or self.repeat_cursor:
            nxt = 0 if self.repeat_cursor else idx + 1
            meta["cursor"] = base64.b64encode(str(nxt).encode()).decode()
        data = [{k: r.get(k) for k in keep if k in r} for r in page]
        return StubResponse({"data": data, "meta": meta})

    def _v1(self, url: str, q: Dict[str, str]):
        bad = set(q) - V1_PARAMS
        if bad:
            return self._error(url, 400, f"unknown params {sorted(bad)}")
        if int(q.get("limit", 50)) > 100:
            return self._error(url, 400, "limit over 100")
        rows = self._matching(q)
        limit = int(q.get("limit", 50))
        page = int(q.get("page", 1))
        chunk = rows[(page - 1) * limit:page * limit]
        total_pages = max(1, -(-len(rows) // limit))
        data = [{k: r.get(k) for k in V1_FIELDS if k in r} for r in chunk]
        return StubResponse({"data": data, "meta": {"page": page, "limit": limit,
                                                    "totalItems": len(rows), "totalPages": total_pages}})


def obs(oid: str, trace: str, start: str, *, session: str = "litellm-gateway-abc12345",
        user: str = "gateway-demo-user", root: bool = True, name: str = "litellm-gateway-completion") -> dict:
    """A full observation row; the fake projects it per request."""
    return {"id": oid, "traceId": trace, "startTime": start, "endTime": start, "projectId": "proj-1",
            "parentObservationId": None, "type": "GENERATION", "name": name, "userId": user,
            "sessionId": session, "isRootObservation": root, "traceName": "litellm-gateway-demo",
            "tags": ["litellm", "gateway", "gateway:litellm", "path:proxy", "demo"]}
