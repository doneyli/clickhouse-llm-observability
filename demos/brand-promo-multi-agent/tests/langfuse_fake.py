"""An in-memory Langfuse public API for the seeder tests. No network.

`FakeClient` subclasses `src.langfuse_rest.Client` and replaces only `request()`, so the
real pagination, cursor and error handling run against it. Each endpoint rejects query
parameters and field groups the real API does not accept, and projects fields the way
the real one does, so a seeder that relies on a wrong parameter fails here too.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from src.langfuse_rest import ApiError, Client

OBS_FIELDS = {"core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"}
SCORE_FIELDS = {"details", "subject", "annotation"}  # core is always returned; naming it is a 400
OBS_PARAMS = {"limit", "cursor", "fields", "name", "userId", "sessionId", "type", "traceId", "level",
              "parentObservationId", "isRootObservation", "environment", "fromStartTime", "toStartTime",
              "version", "filter", "expandMetadata"}
SCORE_PARAMS = {"limit", "cursor", "fields", "id", "name", "source", "dataType", "environment", "configId",
                "queueId", "authorUserId", "value", "valueMin", "valueMax", "traceId", "sessionId",
                "observationId", "experimentId", "fromTimestamp", "toTimestamp"}


def _bad(msg: str) -> ApiError:
    return ApiError(400, msg)


class FakeClient(Client):
    def __init__(self, page_size: int = 2) -> None:
        super().__init__("https://lf.example", "pk", "sk")
        self.page_size = page_size
        self.calls: list[tuple[str, str, dict, Any]] = []
        self.fail: dict[tuple[str, str], ApiError] = {}  # (method, path) -> error to raise
        self.prompts: dict[str, list[dict]] = {}
        self.score_configs: list[dict] = []
        self.datasets: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.queues: list[dict] = []
        self.queue_items: list[dict] = []
        self.scores: list[dict] = []
        self.observations: list[dict] = []  # {"id", "traceId", "traceName", "name", "startTime"}
        self.dashboards: list[dict] = []
        self.rules: list[dict] = []
        self.v2_observations = True  # False: behave like a pinned self-hosted v3 server

    # -- bookkeeping ------------------------------------------------------------------
    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _, _ in self.calls if m != "GET"]

    def request(self, method: str, path: str, params: dict | None = None, body: Any = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        self.calls.append((method, path, params, copy.deepcopy(body)))
        for (m, prefix), err in self.fail.items():
            if m == method and path.startswith(prefix):
                raise err
        handler = self._route(method, path)
        if handler is None:
            raise ApiError(404, f"no route {method} {path}")
        return handler(path, params, body)

    def _route(self, method: str, path: str):
        table = [
            ("GET", "/api/public/v2/prompts/", self._get_prompt),
            ("POST", "/api/public/v2/prompts", self._post_prompt),
            ("GET", "/api/public/score-configs", self._get_configs),
            ("POST", "/api/public/score-configs", self._post_config),
            ("PATCH", "/api/public/score-configs/", self._patch_config),
            ("GET", "/api/public/v2/datasets/", self._get_dataset),
            ("POST", "/api/public/v2/datasets", self._post_dataset),
            ("GET", "/api/public/dataset-items", self._get_items),
            ("POST", "/api/public/dataset-items", self._post_item),
            ("GET", "/api/public/annotation-queues/", self._get_queue_items),
            ("POST", "/api/public/annotation-queues/", self._post_queue_item),
            ("GET", "/api/public/annotation-queues", self._get_queues),
            ("POST", "/api/public/annotation-queues", self._post_queue),
            ("GET", "/api/public/v3/scores", self._get_scores),
            ("GET", "/api/public/v2/observations", self._get_obs_v2),
            ("GET", "/api/public/observations", self._get_obs_v1),
            ("GET", "/api/public/unstable/dashboards", self._get_dashboards),
            ("GET", "/api/public/v2/evaluation-rules", self._get_rules),
        ]
        for m, prefix, fn in table:
            if m == method and (path == prefix or (prefix.endswith("/") and path.startswith(prefix))):
                return fn
        return None

    # -- paging helpers ---------------------------------------------------------------
    def _page(self, rows: list[dict], params: dict) -> dict:
        page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
        size = min(limit, self.page_size)
        total_pages = max(1, -(-len(rows) // size))
        return {"data": rows[(page - 1) * size: page * size],
                "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": total_pages}}

    def _cursor_page(self, rows: list[dict], params: dict) -> dict:
        """Cursor pages of page_size, with an empty page in front when there is a next page:
        the real API may return an empty page that still carries a cursor."""
        offset = int(params.get("cursor") or 0)
        if offset < 0:  # the empty page in front of offset -offset
            return {"data": [], "meta": {"cursor": str(-offset)}}
        chunk = rows[offset: offset + self.page_size]
        nxt = offset + self.page_size
        return {"data": chunk, "meta": {"cursor": str(-nxt) if nxt < len(rows) else None}}

    # -- prompts ----------------------------------------------------------------------
    def _get_prompt(self, path, params, body):
        name = path[len("/api/public/v2/prompts/"):].replace("%2F", "/")
        label = params.get("label")
        for v in reversed(self.prompts.get(name, [])):
            if label in v["labels"]:
                return copy.deepcopy(v)
        raise ApiError(404, "prompt not found")

    def _post_prompt(self, path, params, body):
        if body.get("type") != "text" or not isinstance(body.get("prompt"), str):
            raise _bad("text prompt expected")
        versions = self.prompts.setdefault(body["name"], [])
        for v in versions:  # labels are unique per prompt: assigning one moves it
            v["labels"] = [lb for lb in v["labels"] if lb not in body.get("labels", [])]
        new = {"name": body["name"], "type": "text", "prompt": body["prompt"], "config": body.get("config"),
               "labels": [*(body.get("labels") or []), "latest"], "version": len(versions) + 1}
        for v in versions:
            v["labels"] = [lb for lb in v["labels"] if lb != "latest"]
        versions.append(new)
        return copy.deepcopy(new)

    # -- score configs ----------------------------------------------------------------
    def _get_configs(self, path, params, body):
        return self._page(copy.deepcopy(self.score_configs), params)

    def _post_config(self, path, params, body):
        cfg = dict(copy.deepcopy(body), id=f"sc{len(self.score_configs) + 1}", isArchived=False)
        self.score_configs.append(cfg)
        return cfg

    def _patch_config(self, path, params, body):
        allowed = {"isArchived", "name", "categories", "minValue", "maxValue"}
        if set(body) - allowed:
            raise _bad(f"unknown fields {set(body) - allowed}")
        cid = path.rsplit("/", 1)[1]
        cfg = next(c for c in self.score_configs if c["id"] == cid)
        cfg.update(copy.deepcopy(body))
        return cfg

    # -- datasets ---------------------------------------------------------------------
    def _get_dataset(self, path, params, body):
        name = path[len("/api/public/v2/datasets/"):].replace("%2F", "/")
        if name not in self.datasets:
            raise ApiError(404, "dataset not found")
        return self.datasets[name]

    def _post_dataset(self, path, params, body):
        self.datasets[body["name"]] = {"id": f"d{len(self.datasets) + 1}", "name": body["name"]}
        return self.datasets[body["name"]]

    def _get_items(self, path, params, body):
        rows = [copy.deepcopy(i) for i in self.items.values() if i["datasetName"] == params.get("datasetName")]
        return self._page(rows, params)

    def _post_item(self, path, params, body):
        if body["datasetName"] not in self.datasets:
            raise ApiError(404, "dataset not found")
        self.items[body["id"]] = copy.deepcopy(body)
        return body

    # -- annotation queues ------------------------------------------------------------
    def _get_queues(self, path, params, body):
        return self._page(copy.deepcopy(self.queues), params)

    def _post_queue(self, path, params, body):
        if not body.get("scoreConfigIds"):
            raise _bad("scoreConfigIds is required")
        q = {"id": f"q{len(self.queues) + 1}", "name": body["name"], "scoreConfigIds": body["scoreConfigIds"]}
        self.queues.append(q)
        return q

    def _get_queue_items(self, path, params, body):
        qid = path.split("/")[4]
        rows = [i for i in self.queue_items if i["queueId"] == qid
                and (not params.get("status") or i["status"] == params["status"])]
        return self._page(copy.deepcopy(rows), params)

    def _post_queue_item(self, path, params, body):
        if set(body) - {"objectId", "objectType", "status"} or body.get("objectType") not in ("TRACE", "OBSERVATION", "SESSION"):
            raise _bad("objectId and objectType are required")
        item = {"id": f"i{len(self.queue_items) + 1}", "queueId": path.split("/")[4],
                "objectId": body["objectId"], "objectType": body["objectType"], "status": body.get("status", "PENDING")}
        self.queue_items.append(item)
        return item

    # -- scores v3 --------------------------------------------------------------------
    def _get_scores(self, path, params, body):
        if set(params) - SCORE_PARAMS:
            raise _bad(f"unknown parameters {set(params) - SCORE_PARAMS}")
        groups = set(filter(None, str(params.get("fields", "")).split(",")))
        if groups - SCORE_FIELDS:
            raise _bad(f"unknown field groups {groups - SCORE_FIELDS}")
        rows = [s for s in self.scores
                if s["name"] == params.get("name", s["name"])
                and float(params.get("valueMin", -1e9)) <= s["value"] <= float(params.get("valueMax", 1e9))]
        out = []
        for s in rows:
            r = {k: s[k] for k in ("id", "name", "value", "dataType")}
            if "subject" in groups:
                r["subject"] = s["subject"]
            out.append(r)
        return self._cursor_page(out, params)

    # -- observations -----------------------------------------------------------------
    def _get_obs_v2(self, path, params, body):
        if not self.v2_observations:
            raise ApiError(404, "not found")
        if set(params) - OBS_PARAMS:
            raise _bad(f"unknown parameters {set(params) - OBS_PARAMS}")
        groups = set(str(params.get("fields", "core,basic")).split(","))
        if groups - OBS_FIELDS:
            raise _bad(f"unknown field groups {groups - OBS_FIELDS}")
        rows = list(self.observations)
        for f in json.loads(params.get("filter", "[]")):
            rows = [r for r in rows if r.get(f["column"]) == f["value"]]
        if "traceId" in params:
            rows = [r for r in rows if r["traceId"] == params["traceId"]]
        out = []
        for r in rows:
            o = {"id": r["id"], "traceId": r["traceId"], "startTime": r["startTime"], "type": r.get("type", "SPAN")}
            if "basic" in groups:
                o["name"] = r["name"]
            out.append(o)
        return self._cursor_page(out, params)

    def _get_obs_v1(self, path, params, body):
        rows = [r for r in self.observations if r["traceId"] == params.get("traceId")]
        return self._page(rows, params)

    # -- dashboards and rules ---------------------------------------------------------
    def _get_dashboards(self, path, params, body):
        return self._page(copy.deepcopy(self.dashboards), params)

    def _get_rules(self, path, params, body):
        return self._cursor_page(copy.deepcopy(self.rules), params)
