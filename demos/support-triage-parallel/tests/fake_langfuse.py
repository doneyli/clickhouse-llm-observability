"""An offline Langfuse public API at the urllib level, for verify_demo.py and seed_prompts.py.

Install it with `server.install(monkeypatch)`: it replaces `urllib.request.urlopen`, so the
scripts build their real URLs, query strings, headers and bodies, and parse real JSON. It
rejects query parameters an endpoint does not take, enforces the documented limits and
required parameters, honours field projection (Observations v2 `basic` carries `name`;
Scores v3 takes only the `subject` group) and paginates: cursor pages for v2/v3 and
experiments, with an optional empty page in the middle, page numbers for the rest.

No network: every URL is answered here or raises.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

PAGE_V2_OBS = {"name", "userId", "sessionId", "type", "traceId", "level", "parentObservationId",
               "isRootObservation", "environment", "fromStartTime", "toStartTime", "version", "fields",
               "limit", "cursor", "filter"}
PAGE_V1_OBS = {"name", "userId", "type", "traceId", "level", "parentObservationId", "environment",
               "fromStartTime", "toStartTime", "version", "page", "limit"}
SCORES_V3 = {"name", "traceId", "sessionId", "fromTimestamp", "toTimestamp", "environment", "source",
             "dataType", "fields", "limit", "cursor"}
EXPERIMENTS = {"datasetId", "name", "fromStartTime", "toStartTime", "fields", "limit", "cursor"}
PAGED = {"page", "limit"}
ITEMS = {"datasetName", "sourceTraceId", "sourceObservationId", "version", "page", "limit"}
PROMPT_GET = {"label", "version"}

OBS_CORE = {"id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"}
OBS_BASIC = {"name", "level", "statusMessage", "version", "environment", "isRootObservation"}
V2_GROUPS = {"core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"}


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url: str, code: int, message: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, message, {}, io.BytesIO(json.dumps({"message": message}).encode()))


class FakeLangfuse:
    """State of one project. Tests mutate the attributes, then call the scripts."""

    def __init__(self) -> None:
        self.project = {"id": "p1", "name": "LLM Observability Demo"}
        self.v2_observations = True        # False: a v3 server, v2 answers 404
        self.experiments_api = True        # False: a v3 server, /experiments answers 404
        self.page_size = 2                 # small, so pagination is exercised
        self.empty_page_after_first = True
        self.repeat_cursor = False
        self.prompts: Dict[str, List[dict]] = {}   # name -> versions, oldest first
        self.datasets: List[dict] = []
        self.items: Dict[str, List[dict]] = {}
        self.observations: List[dict] = []
        self.scores: List[dict] = []
        self.experiments: List[dict] = []          # rows with datasetId, name
        self.dataset_runs: Dict[str, List[dict]] = {}
        self.fail: Dict[str, int] = {}             # path prefix -> HTTP status to answer
        self.fail_posts_for: set = set()           # prompt names whose POST fails
        self.requests: List[str] = []
        self.auth: List[str] = []
        self.posts: List[dict] = []

    def install(self, monkeypatch) -> "FakeLangfuse":
        monkeypatch.setattr(urllib.request, "urlopen", self.urlopen)
        return self

    # --- transport -----------------------------------------------------------------------

    def urlopen(self, req, timeout=None):
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != "lf.test":
            raise urllib.error.URLError(f"offline test: no route to {parts.netloc}")
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}
        path = urllib.parse.unquote(parts.path)
        method = req.get_method() if isinstance(req, urllib.request.Request) else "GET"
        self.requests.append(f"{method} {path}?{parts.query}")
        if isinstance(req, urllib.request.Request):
            self.auth.append(req.get_header("Authorization"))
        for prefix, code in self.fail.items():
            if path.startswith(prefix):
                raise _http_error(url, code, "injected failure")
        body = json.loads(req.data) if getattr(req, "data", None) else None
        status, payload = self.route(method, path, params, body)
        if status >= 400:
            raise _http_error(url, status, payload)
        return _Response(json.dumps(payload).encode())

    @staticmethod
    def _allow(params: Dict[str, str], allowed: set) -> None:
        bad = sorted(set(params) - allowed)
        if bad:
            raise AssertionError(f"invalid query parameter(s) {bad}")

    def _cursor_page(self, rows: List[dict], params: Dict[str, str], max_limit: int) -> dict:
        limit = int(params.get("limit", 50))
        if limit > max_limit:
            raise AssertionError(f"limit {limit} > {max_limit}")
        size = min(limit, self.page_size)
        state = params.get("cursor")
        if self.repeat_cursor and state:
            return {"data": rows[:size], "meta": {"cursor": state}}
        if state and state.startswith("empty-"):
            return {"data": [], "meta": {"cursor": state[len("empty-"):]}}
        start = int(state) if state else 0
        page = rows[start:start + size]
        nxt = start + size
        meta: Dict[str, Any] = {}
        if nxt < len(rows):
            meta["cursor"] = (f"empty-{nxt}" if self.empty_page_after_first and start == 0 else str(nxt))
        return {"data": page, "meta": meta}

    def _numbered_page(self, rows: List[dict], params: Dict[str, str]) -> dict:
        limit = min(int(params.get("limit", 50)), self.page_size)
        page = int(params.get("page", 1))
        total_pages = max(1, -(-len(rows) // limit))
        return {"data": rows[(page - 1) * limit:page * limit],
                "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": total_pages}}

    @staticmethod
    def _in_window(row: dict, params: Dict[str, str], key: str = "startTime") -> bool:
        lo, hi = params.get("fromStartTime"), params.get("toStartTime")
        return (not lo or row[key] >= lo) and (not hi or row[key] <= hi)

    # --- routes --------------------------------------------------------------------------

    def route(self, method: str, path: str, params: Dict[str, str], body: Optional[dict]):
        if path == "/ping":  # the ClickHouse playground's HTTP interface
            return 200, "Ok."
        if path == "/api/public/projects":
            self._allow(params, set())
            return 200, {"data": [self.project] if self.project else []}
        if path.startswith("/api/public/v2/prompts"):
            return self.route_prompts(method, path, params, body)
        if path == "/api/public/v2/datasets":
            self._allow(params, PAGED)
            return 200, self._numbered_page(self.datasets, params)
        if path == "/api/public/dataset-items":
            self._allow(params, ITEMS)
            return 200, self._numbered_page(self.items.get(params["datasetName"], []), params)
        if path == "/api/public/v2/observations":
            if not self.v2_observations:
                return 404, "The observations v2 API is only available in a Langfuse v4 write mode"
            self._allow(params, PAGE_V2_OBS)
            groups = set((params.get("fields") or "core,basic").split(","))
            assert groups <= V2_GROUPS, groups
            keep = (OBS_CORE if "core" in groups else set()) | (OBS_BASIC if "basic" in groups else set())
            rows = [self._v2_row(o) for o in self._observations(params)]
            return 200, self._cursor_page([{k: v for k, v in r.items() if k in keep} for r in rows], params, 1000)
        if path == "/api/public/observations":
            self._allow(params, PAGE_V1_OBS)
            rows = [dict(o, parentObservationId=o.get("parentObservationId")) for o in self._observations(params)]
            return 200, self._numbered_page(rows, params)
        if path == "/api/public/v3/scores":
            self._allow(params, SCORES_V3)
            fields = set(filter(None, (params.get("fields") or "").split(",")))
            assert fields <= {"subject", "metadata"}, f"Scores v3 core fields are automatic, got {fields}"
            rows = [s for s in self.scores
                    if (not params.get("traceId") or s["subject"]["traceId"] == params["traceId"])
                    and (not params.get("name") or s["name"] in params["name"].split(","))
                    and (not params.get("fromTimestamp") or s["timestamp"] >= params["fromTimestamp"])]
            out = [{k: v for k, v in s.items() if k != "subject" or "subject" in fields} for s in rows]
            return 200, self._cursor_page(out, params, 100)
        if path == "/api/public/experiments":
            if not self.experiments_api:
                return 404, "not found"
            self._allow(params, EXPERIMENTS)
            assert params.get("fromStartTime"), "experiments require fromStartTime"
            rows = [e for e in self.experiments if e["datasetId"] == params.get("datasetId")]
            return 200, self._cursor_page(rows, params, 100)
        if path.startswith("/api/public/datasets/") and path.endswith("/runs"):
            self._allow(params, PAGED)
            name = path[len("/api/public/datasets/"):-len("/runs")]
            return 200, self._numbered_page(self.dataset_runs.get(name, []), params)
        raise AssertionError(f"unexpected route {method} {path}")

    def _observations(self, params: Dict[str, str]) -> List[dict]:
        assert params.get("fromStartTime") and params.get("toStartTime"), "observation reads are time-bounded"
        return [o for o in self.observations
                if (not params.get("name") or o["name"] == params["name"])
                and (not params.get("traceId") or o["traceId"] == params["traceId"])
                and self._in_window(o, params)]

    @staticmethod
    def _v2_row(o: dict) -> dict:
        return dict(o, isRootObservation=not o.get("parentObservationId"))

    def route_prompts(self, method: str, path: str, params: Dict[str, str], body: Optional[dict]):
        if method == "POST":
            self._allow(params, set())
            if body["name"] in self.fail_posts_for:
                return 500, "write failed"
            self.posts.append(body)
            versions = self.prompts.setdefault(body["name"], [])
            for v in versions:  # a label lives on one version at a time
                v["labels"] = [lab for lab in v["labels"] if lab not in body.get("labels", [])]
            row = {"name": body["name"], "type": body["type"], "prompt": body["prompt"],
                   "config": body.get("config") or {}, "labels": list(body.get("labels") or []),
                   "version": len(versions) + 1}
            versions.append(row)
            return 201, row
        self._allow(params, PROMPT_GET)
        name = path[len("/api/public/v2/prompts/"):]
        versions = self.prompts.get(name, [])
        if "version" in params:
            hit = [v for v in versions if v["version"] == int(params["version"])]
        else:
            hit = [v for v in versions if params.get("label", "production") in v["labels"]]
        return (200, dict(hit[-1])) if hit else (404, "prompt not found")
