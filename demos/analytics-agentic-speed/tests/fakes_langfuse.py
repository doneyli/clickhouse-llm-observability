"""In-memory Langfuse stand-ins for the offline tests. No network.

``FakeSeedAPI`` replaces ``seed_langfuse.API``'s transport (``__call__``), so the real
``paginate``, planners and seeding steps run against it. ``FakeLangfuseServer`` is an
``httpx.MockTransport`` handler for ``verify_demo.Langfuse``: it validates the query
parameters the way the public API does (unknown field groups, limits, required bounds)
and honours field projection, so a request the real server would reject fails here too.
"""
from __future__ import annotations

import copy
import itertools
import json
from typing import Any, Callable, Optional
from urllib.parse import unquote

import httpx

import seed_langfuse as seed


class SeedAPIError(seed.LangfuseAPIError):
    pass


class FakeSeedAPI(seed.API):
    """Score configs, prompts, widgets, dashboards and placements, held in memory."""

    def __init__(self, project: str = seed.EXPECTED_PROJECT) -> None:  # noqa: D107  (no httpx client)
        self.base_url = "https://fake.langfuse.test"
        self.project = project
        self.configs: list[dict] = []
        self.prompts: dict[str, list[dict]] = {}
        self.widgets: list[dict] = []
        self.dashboards: list[dict] = []
        self.placements: dict[str, list[dict]] = {}
        self.writes: list[tuple[str, str]] = []
        self.fail: Callable[[str, str, Optional[dict]], Optional[int]] = lambda m, p, b: None
        self.ignore_patches = False  # a server that answers 200 but changes nothing
        self._ids = (f"id{i}" for i in itertools.count(1))

    # ---- helpers for tests
    def seed_everything(self) -> None:
        """State as a successful seed leaves it."""
        for c in seed.SCORE_CONFIGS:
            self.configs.append({**copy.deepcopy(c), "id": next(self._ids), "isArchived": False})
        for name, text in (seed.load_prompts() or {}).items():
            self.prompts[name] = [{"name": name, "version": 1, "type": "text", "prompt": text,
                                   "config": {}, "labels": ["production", "latest"]}]

    def production(self, name: str) -> Optional[dict]:
        return next((p for p in self.prompts.get(name, []) if "production" in p["labels"]), None)

    # ---- the transport
    def __call__(self, method: str, path: str, body: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict:
        code = self.fail(method, path, body)
        if code:
            raise SeedAPIError(method, path, code, "injected failure")
        if method != "GET":
            self.writes.append((method, path))
        params = params or {}
        U = seed.U
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}] if self.project else []}
        if path == "/api/public/score-configs":
            if method == "GET":
                return self._page(self.configs, params)
            row = {**copy.deepcopy(body), "id": next(self._ids), "isArchived": False}
            self.configs.append(row)
            return row
        if path.startswith("/api/public/score-configs/") and method == "PATCH":
            row = next(c for c in self.configs if c["id"] == path.rsplit("/", 1)[1])
            if not self.ignore_patches:
                for k, v in body.items():
                    if v is None:
                        row.pop(k, None)
                    else:
                        row[k] = copy.deepcopy(v)
            return row
        if path == "/api/public/v2/prompts" and method == "POST":
            versions = self.prompts.setdefault(body["name"], [])
            for p in versions:
                p["labels"] = [lab for lab in p["labels"] if lab not in body["labels"] and lab != "latest"]
            row = {**copy.deepcopy(body), "version": len(versions) + 1, "config": body.get("config") or {},
                   "labels": [*body["labels"], "latest"]}
            versions.append(row)
            return row
        if path.startswith("/api/public/v2/prompts/"):
            name = unquote(path.rsplit("/", 1)[1])
            hit = next((p for p in self.prompts.get(name, []) if params.get("label") in p["labels"]), None)
            if hit is None:
                raise SeedAPIError(method, path, 404, "not found")
            return copy.deepcopy(hit)
        if path == "/api/public/v2/metrics":
            return {"data": []}
        if path == f"{U}/dashboard-widgets":
            if method == "GET":
                return self._page(self.widgets, params)
            row = {**copy.deepcopy(body), "id": next(self._ids)}
            self.widgets.append(row)
            return row
        if path.startswith(f"{U}/dashboard-widgets/") and method == "PATCH":
            row = next(w for w in self.widgets if w["id"] == path.rsplit("/", 1)[1])
            row.update(copy.deepcopy(body))
            return row
        if path == f"{U}/dashboards":
            if method == "GET":
                return self._page(self.dashboards, params)
            row = {**copy.deepcopy(body), "id": next(self._ids)}
            self.dashboards.append(row)
            self.placements[row["id"]] = []
            return row
        if path.startswith(f"{U}/dashboards/"):
            parts = path[len(f"{U}/dashboards/"):].split("/")
            dash_id = parts[0]
            if len(parts) == 1 and method == "GET":
                return {"id": dash_id, "definition": {"widgets": copy.deepcopy(self.placements[dash_id])}}
            if len(parts) == 1 and method == "PATCH":
                return {}
            if parts[1] == "placements" and method == "POST":
                self.placements[dash_id].append({**copy.deepcopy(body), "id": next(self._ids)})
                return {}
            if parts[1] == "placements" and method == "DELETE":
                self.placements[dash_id] = [p for p in self.placements[dash_id] if p["id"] != parts[2]]
                return {}
        raise AssertionError(f"FakeSeedAPI: unexpected {method} {path}")

    @staticmethod
    def _page(rows: list[dict], params: dict) -> dict:
        limit, page = int(params.get("limit", 50)), int(params.get("page", 1))
        total = max(1, -(-len(rows) // limit))
        return {"data": copy.deepcopy(rows[(page - 1) * limit: page * limit]),
                "meta": {"page": page, "limit": limit, "totalPages": total, "totalItems": len(rows)}}


# ---------------------------------------------------------------- verify_demo's HTTP fake

SCORE_GROUPS = {"details", "subject", "annotation"}
OBS_GROUPS = {"core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"}
OBS_FIELDS = {"core": {"id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"},
              "basic": {"name", "level", "statusMessage", "version", "environment", "bookmarked", "public",
                        "userId", "sessionId", "isRootObservation"}}
SCORE_CORE = {"id", "projectId", "name", "value", "dataType", "source", "timestamp", "environment",
              "createdAt", "updatedAt"}


def _bad(msg: str) -> httpx.Response:
    return httpx.Response(400, json={"message": msg})


class FakeLangfuseServer:
    """State for verify_demo's reads, served over ``httpx.MockTransport``."""

    def __init__(self) -> None:
        self.project = seed.EXPECTED_PROJECT
        self.prompts: dict[tuple[str, str], dict] = {}
        self.dataset: Optional[dict] = {"id": "ds1", "name": "scenario-explorer-questions"}
        self.items: list[dict] = []
        self.configs: list[dict] = []
        self.dashboards: list[dict] = []
        self.dashboard_widgets: dict[str, list[dict]] = {}
        self.observation_pages: list[list[dict]] = []  # pages served in order; cursors link them
        self.scores: list[dict] = []
        self.experiments: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.override: Callable[[httpx.Request], Optional[httpx.Response]] = lambda r: None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        forced = self.override(req)
        if forced is not None:
            return forced
        q = dict(req.url.params)
        path = req.url.path
        if "limit" in q and not (1 <= int(q["limit"]) <= 100):
            return _bad("limit must be 1..100")
        if path == "/api/public/projects":
            return httpx.Response(200, json={"data": [{"id": "p1", "name": self.project}] if self.project else []})
        if path.startswith("/api/public/v2/prompts/"):
            hit = self.prompts.get((unquote(path.rsplit("/", 1)[1]), q.get("label", "production")))
            return httpx.Response(200, json=hit) if hit else httpx.Response(404, json={"message": "not found"})
        if path.startswith("/api/public/v2/datasets/"):
            if self.dataset and unquote(path.rsplit("/", 1)[1]) == self.dataset["name"]:
                return httpx.Response(200, json=self.dataset)
            return httpx.Response(404, json={"message": "not found"})
        if path == "/api/public/dataset-items":
            if q.get("datasetName") != (self.dataset or {}).get("name"):
                return httpx.Response(404, json={"message": "dataset not found"})
            return self._page(self.items, q)
        if path == "/api/public/score-configs":
            return self._page(self.configs, q)
        if path == f"{seed.U}/dashboards":
            return self._page(self.dashboards, q)
        if path.startswith(f"{seed.U}/dashboards/"):
            dash_id = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": dash_id,
                                             "definition": {"widgets": self.dashboard_widgets.get(dash_id, [])}})
        if path == "/api/public/v2/observations":
            return self._observations(q)
        if path == "/api/public/v3/scores":
            return self._scores(q)
        if path == "/api/public/experiments":
            if "fromStartTime" not in q:
                return _bad("fromStartTime is required")
            rows = [e for e in self.experiments if e.get("datasetId") == q.get("datasetId")]
            return httpx.Response(200, json={"data": rows, "meta": {}})
        return httpx.Response(404, json={"message": f"no route {path}"})

    def _page(self, rows: list[dict], q: dict) -> httpx.Response:
        limit, page = int(q.get("limit", 50)), int(q.get("page", 1))
        total = max(1, -(-len(rows) // limit))
        return httpx.Response(200, json={"data": rows[(page - 1) * limit: page * limit],
                                         "meta": {"page": page, "limit": limit, "totalPages": total}})

    def _observations(self, q: dict) -> httpx.Response:
        groups = set((q.get("fields") or "core,basic").split(","))
        if not groups <= OBS_GROUPS:
            return _bad(f"unknown field groups {sorted(groups - OBS_GROUPS)}")
        if q.get("parseIoAsJson") == "true":
            return _bad("parseIoAsJson is deprecated")
        filters = json.loads(q.get("filter") or "[]")
        page = int(q.get("cursor", "c0")[1:]) if q.get("cursor") else 0
        rows = self.observation_pages[page] if page < len(self.observation_pages) else []
        keep = set().union(*(OBS_FIELDS.get(g, set()) for g in groups | {"core"}))
        rows = [r for r in rows if _matches(r, filters)]
        out = [{k: v for k, v in r.items() if k in keep} for r in rows]
        meta = {"cursor": f"c{page + 1}"} if page + 1 < len(self.observation_pages) else {}
        return httpx.Response(200, json={"data": out, "meta": meta})

    def _scores(self, q: dict) -> httpx.Response:
        groups = {g for g in (q.get("fields") or "").split(",") if g}
        if not groups <= SCORE_GROUPS:
            return _bad(f"unknown field groups {sorted(groups - SCORE_GROUPS)}; core fields are always returned")
        rows = [s for s in self.scores if s.get("traceId") == q.get("traceId")]
        keep = SCORE_CORE | ({"subject"} if "subject" in groups else set())
        return httpx.Response(200, json={"data": [{k: v for k, v in s.items() if k in keep} for s in rows],
                                         "meta": {}})


def _matches(row: dict, filters: list[dict]) -> bool:
    for f in filters:
        col = f["column"]
        if col == "name" and row.get("name") not in f["value"]:
            return False
        if col == "metadata" and (row.get("metadata") or {}).get(f["key"]) != f["value"]:
            return False
        if col == "tags" and not set(f["value"]) & set(row.get("tags") or []):
            return False
    return True
