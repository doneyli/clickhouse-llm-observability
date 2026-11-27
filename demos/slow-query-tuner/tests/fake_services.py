"""An offline stand-in for the Langfuse public API and the ClickHouse HTTP interface.

It replaces urllib.request.urlopen, so the scripts' real URL building, auth headers,
pagination and JSON parsing all run. Each Langfuse route accepts only the query
parameters the current API reference (https://api.reference.langfuse.com/openapi.yml)
lists for it, or the pinned v3 server serves, and answers 400 for anything else.
Observations v2 return only the field groups asked for; Scores v3 reject unknown groups.
A server with `version` below 4 behaves like the pinned self-hosted 3.221.1: Observations
v2 and experiments are 404, the v1 observations read and dataset runs work.
"""
from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional

LANGFUSE = "http://localhost:3001"
CLICKHOUSE = "http://localhost:8126"
NOW = "2099-01-01T00:00:00.000Z"

PESSIMAL_DDL = ("CREATE TABLE tuning_lab.web_events\\n(\\n    `event_id` String,\\n    `user_id` UInt64\\n)\\n"
                "ENGINE = MergeTree\\nORDER BY tuple()\\nSETTINGS index_granularity = 8192\n")
PROJECTED_DDL = ("CREATE TABLE tuning_lab.web_events\\n(\\n    `user_id` UInt64,\\n    PROJECTION p_user\\n    (\\n"
                 "        SELECT *\\n        ORDER BY user_id\\n    )\\n)\\nENGINE = MergeTree\\nORDER BY tuple()\n")

ALLOWED = {
    "v2_observations": {"fields", "expandMetadata", "limit", "cursor", "parseIoAsJson", "name", "userId",
                        "sessionId", "type", "traceId", "level", "parentObservationId", "isRootObservation",
                        "environment", "fromStartTime", "toStartTime", "version", "filter"},
    "v1_observations": {"page", "limit", "name", "userId", "type", "traceId", "level", "parentObservationId",
                        "environment", "fromStartTime", "toStartTime", "version"},
    "v3_scores": {"limit", "cursor", "fields", "id", "name", "source", "dataType", "environment", "configId",
                  "queueId", "authorUserId", "value", "valueMin", "valueMax", "traceId", "sessionId",
                  "observationId", "experimentId", "fromTimestamp", "toTimestamp"},
    "prompt": {"version", "label", "resolve"},
    "dataset_items": {"datasetName", "sourceTraceId", "sourceObservationId", "version", "page", "limit"},
    "experiments": {"fields", "limit", "scoreLimit", "cursor", "fromStartTime", "toStartTime", "id", "name",
                    "datasetId", "filter"},
    "dataset_runs": {"page", "limit"},
}
OBS_GROUPS = {
    "core": ["id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"],
    "basic": ["name", "level", "statusMessage", "version", "environment", "bookmarked", "public", "userId",
              "sessionId", "isRootObservation"],
}
SCORE_CORE = ["id", "name", "value", "dataType", "source", "timestamp"]


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    def __init__(self, version: str = "4.30.0") -> None:
        self.version = version
        self.down: set = set()             # "langfuse" / "clickhouse": refuse connections
        self.fail: Dict[str, int] = {}      # route -> HTTP status to answer with
        self.public_key, self.secret_key = "pk-lf-file", "sk-lf-file"
        self.projects = [{"id": "p1", "name": "LLM Observability Demo"}]
        self.prompts: Dict[tuple, dict] = {}
        self.datasets: Dict[str, dict] = {}
        self.items: List[dict] = []
        self.experiments: List[dict] = []
        self.observations: List[dict] = []
        self.scores: List[dict] = []
        self.empty_pages_first = 0          # cursor pages served empty before the data
        self.repeat_cursor = False          # answer every page with the same cursor
        self.ch_users = {"tuner_agent": "tuner_agent123", "tuner_admin": "tuner_admin123"}
        self.ch_rows = 30_000_000
        self.ch_ddl = PESSIMAL_DDL
        self.ch_writes: List[str] = []
        self.requests: List[str] = []

    # -- fixtures -------------------------------------------------------------------------
    def seed_ready(self) -> "FakeServer":
        import seed_dataset
        import seed_prompts
        version = 1
        for name, text, labels, _ in seed_prompts.DEFINITIONS:
            row = {"name": name, "type": "text", "prompt": text, "config": {}, "version": version,
                   "labels": list(labels), "tags": []}
            for label in labels:
                self.prompts[(name, label)] = row
            version += 1
        self.datasets[seed_dataset.DATASET_NAME] = {"id": "d1", "name": seed_dataset.DATASET_NAME}
        self.items = [{"id": w["id"], "status": "ACTIVE", "input": w["input"], "expectedOutput": w["expected_output"],
                       "metadata": w["metadata"], "datasetName": seed_dataset.DATASET_NAME}
                      for w in seed_dataset.desired_items()]
        self.experiments = [{"id": "e1", "name": "query-tuner prompt v2-disciplined", "datasetId": "d1"}]
        root = {"id": "r1", "traceId": "t1", "startTime": NOW, "endTime": NOW, "projectId": "p1",
                "parentObservationId": None, "type": "AGENT", "name": "tune-clickhouse-query",
                "isRootObservation": True, "level": "DEFAULT"}
        old = dict(root, id="r0", traceId="t0", startTime="2098-12-31T00:00:00.000Z")
        child = dict(root, id="g1", parentObservationId="r1", type="GENERATION", name="plan-next-action",
                     isRootObservation=False)
        self.observations = [old, root, child, dict(child, id="g2")]
        import verify_demo as v
        self.scores = [{"id": f"s{i}", "name": n, "traceId": "t1", "value": 1, "dataType": "NUMERIC"}
                       for i, n in enumerate(v.EXPECTED_SCORES + v.EVALUATOR_SCORES + v.OPTIONAL_SCORES)]
        return self

    # -- transport --------------------------------------------------------------------------
    def __call__(self, req, timeout=None):
        url = req.full_url
        self.requests.append(f"{req.get_method()} {url}")
        if url.startswith(CLICKHOUSE):
            return self._clickhouse(req, url)
        if not url.startswith(LANGFUSE):
            raise urllib.error.URLError("unknown host")
        if "langfuse" in self.down:
            raise urllib.error.URLError("connection refused")
        parsed = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        path = urllib.parse.unquote(parsed.path)
        if path != "/api/public/health":
            want = "Basic " + base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
            if req.get_header("Authorization") != want:
                return self._error(url, 401, "Invalid credentials")
        status, body = self._route(path, params, parsed.path)
        if status >= 400:
            return self._error(url, status, json.dumps(body))
        return Response(json.dumps(body).encode())

    def _error(self, url, code, message):
        raise urllib.error.HTTPError(url, code, message, {}, io.BytesIO(str(message).encode()))

    def _check(self, route: str, params: Dict[str, str]) -> Optional[tuple]:
        if route in self.fail:
            return self.fail[route], {"message": "injected failure"}
        unknown = set(params) - ALLOWED[route]
        if unknown:
            return 400, {"message": f"unrecognized_keys: {sorted(unknown)}"}
        return None

    def _route(self, path: str, params: Dict[str, str], raw_path: str):
        v3 = int(self.version.split(".")[0]) < 4
        if path == "/api/public/health":
            if "health" in self.fail:
                return self.fail["health"], {"message": "down"}
            return 200, {"status": "OK", "version": self.version}
        if path == "/api/public/projects":
            return 200, {"data": self.projects}
        if path.startswith("/api/public/v2/prompts/"):
            err = self._check("prompt", params)
            if err:
                return err
            row = self.prompts.get((path[len("/api/public/v2/prompts/"):], params.get("label", "production")))
            return (200, row) if row else (404, {"message": "Prompt not found"})
        if raw_path.startswith("/api/public/v2/datasets/"):
            name = urllib.parse.unquote(raw_path[len("/api/public/v2/datasets/"):])
            if "datasets" in self.fail:
                return self.fail["datasets"], {"message": "injected failure"}
            row = self.datasets.get(name)
            return (200, row) if row else (404, {"message": "Dataset not found"})
        if path == "/api/public/dataset-items":
            err = self._check("dataset_items", params)
            if err:
                return err
            rows = [i for i in self.items if i["datasetName"] == params.get("datasetName")]
            return 200, self._paged(rows, params)
        if path == "/api/public/experiments":
            if v3:
                return 404, {"message": "Not found"}
            err = self._check("experiments", params)
            if err:
                return err
            if "fromStartTime" not in params:
                return 400, {"message": "fromStartTime is required"}
            rows = [e for e in self.experiments if not params.get("datasetId") or e["datasetId"] == params["datasetId"]]
            return 200, self._cursored(rows, params)
        if raw_path.startswith("/api/public/datasets/") and raw_path.endswith("/runs"):
            if not v3:
                return 404, {"message": "Not found"}
            err = self._check("dataset_runs", params)
            if err:
                return err
            return 200, self._paged(self.experiments, params)
        if path == "/api/public/v2/observations":
            if v3:
                return 404, {"message": "The observations v2 API is only available in a Langfuse v4 write mode."}
            err = self._check("v2_observations", params)
            if err:
                return err
            if int(params.get("limit", 50)) > 1000:
                return 400, {"message": "limit > 1000"}
            groups = (params.get("fields") or "core,basic").split(",")
            if set(groups) - {"core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics",
                              "trace_context"}:
                return 400, {"message": "unknown field group"}
            keep = [k for g in ["core"] + groups for k in OBS_GROUPS.get(g, [])]
            rows = [{k: o.get(k) for k in keep} for o in self._observations(params)]
            return 200, self._cursored(rows, params)
        if path == "/api/public/observations":
            if not v3:
                return 404, {"message": "Not found"}
            err = self._check("v1_observations", params)
            if err:
                return err
            return 200, self._paged(self._observations(params), params)
        if path == "/api/public/v3/scores":
            err = self._check("v3_scores", params)
            if err:
                return err
            if int(params.get("limit", 50)) > 100:
                return 400, {"message": "limit > 100"}
            groups = [g for g in (params.get("fields") or "").split(",") if g]
            if set(groups) - {"details", "subject", "annotation"}:
                return 400, {"message": "unknown field group"}
            rows = [{k: s.get(k) for k in SCORE_CORE} for s in self.scores
                    if not params.get("traceId") or s["traceId"] == params["traceId"]]
            return 200, self._cursored(rows, params)
        return 404, {"message": f"no route {path}"}

    def _observations(self, params):
        rows = self.observations
        if "name" in params:
            rows = [o for o in rows if o.get("name") == params["name"]]
        if "type" in params:
            rows = [o for o in rows if o.get("type") == params["type"]]
        if "traceId" in params:
            rows = [o for o in rows if o.get("traceId") == params["traceId"]]
        if params.get("isRootObservation") == "true":
            rows = [o for o in rows if o.get("isRootObservation")]
        assert params.get("fromStartTime") and params.get("toStartTime"), "every observation read is time-bounded"
        return rows

    def _cursored(self, rows, params):
        limit = int(params.get("limit", 50))
        pos = int(params.get("cursor", "c0")[1:])
        if self.repeat_cursor:
            return {"data": rows[:limit], "meta": {"cursor": "c1"}}
        if pos < self.empty_pages_first:
            return {"data": [], "meta": {"cursor": f"c{pos + 1}"}}
        start = (pos - self.empty_pages_first) * limit
        page = rows[start:start + limit]
        more = start + limit < len(rows)
        return {"data": page, "meta": {"cursor": f"c{pos + 1}"} if more else {}}

    def _paged(self, rows, params):
        page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
        total_pages = max(1, -(-len(rows) // limit))
        return {"data": rows[(page - 1) * limit: page * limit],
                "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": total_pages}}

    # -- ClickHouse ---------------------------------------------------------------------------
    def _clickhouse(self, req, url):
        if "clickhouse" in self.down:
            raise urllib.error.URLError("connection refused")
        user, key = req.get_header("X-clickhouse-user"), req.get_header("X-clickhouse-key")
        if self.ch_users.get(user) != key:
            return self._error(url, 403, "Code: 516. DB::Exception: Authentication failed")
        if req.get_method() == "POST":
            sql = req.data.decode()
            if user != "tuner_admin":
                return self._error(url, 403, "Code: 164. readonly")
            self.ch_writes.append(sql)
            self.ch_ddl = PESSIMAL_DDL
            return Response(b"")
        sql = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["query"]
        if sql.startswith("SELECT count()"):
            return Response(f"{self.ch_rows}\n".encode())
        if sql.startswith("SHOW CREATE TABLE"):
            return Response(self.ch_ddl.encode())
        if sql == "SELECT 1":
            return Response(b"1\n")
        return self._error(url, 400, "unexpected query")
