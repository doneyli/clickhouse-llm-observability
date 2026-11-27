"""An in-memory Langfuse public API for the offline tests, at the HTTP-request level.

Install it with `FakeLangfuse().install(monkeypatch)`: every `verify_demo.Langfuse.request`
call is routed here, so the real parameter construction, pagination and parsing run. Like
the real server it rejects unknown query parameters and out-of-range limits with a 400,
honours field projection (Observations v2 only returns the groups asked for, with input
and output as JSON strings; Scores v3 only returns `subject` when asked), requires
`fromStartTime` on experiments, and can play a 3.x server (no Observations v2, no
Experiments API). No network.
"""
from __future__ import annotations

import copy
import json
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import verify_demo as v

OBS_V2_PARAMS = {"fields", "expandMetadata", "limit", "cursor", "parseIoAsJson", "name", "userId", "sessionId",
                 "type", "traceId", "level", "parentObservationId", "isRootObservation", "environment",
                 "fromStartTime", "toStartTime", "version", "filter"}
OBS_V2_GROUPS = {"core": ["id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"],
                 "basic": ["name", "level", "statusMessage", "version", "environment"],
                 "time": [], "io": ["input", "output"], "metadata": ["metadata"], "model": [], "usage": [],
                 "prompt": [], "metrics": [], "trace_context": []}
OBS_V1_PARAMS = {"page", "limit", "name", "userId", "type", "traceId", "parentObservationId", "environment",
                 "fromStartTime", "toStartTime", "version"}
SCORES_V3_PARAMS = {"limit", "cursor", "fields", "id", "name", "source", "dataType", "environment", "configId",
                    "queueId", "authorUserId", "value", "valueMin", "valueMax", "traceId", "sessionId",
                    "observationId", "experimentId", "fromTimestamp", "toTimestamp"}
SCORES_V3_GROUPS = {"details", "subject", "annotation"}
EXPERIMENT_PARAMS = {"fields", "limit", "scoreLimit", "cursor", "fromStartTime", "toStartTime", "id", "name",
                     "datasetId", "filter"}
DATASET_ITEM_PARAMS = {"datasetName", "sourceTraceId", "sourceObservationId", "version", "page", "limit"}
PROMPT_LIST_PARAMS = {"name", "label", "tag", "page", "limit", "fromUpdatedAt", "toUpdatedAt", "filter"}


def _bad(msg: str) -> v.ApiError:
    return v.ApiError(400, msg)


def _allow(params: Dict[str, Any], allowed: set, path: str) -> None:
    unknown = set(params) - allowed
    if unknown:
        raise _bad(f"{path}: unknown query parameter(s) {sorted(unknown)}")


class FakeLangfuse:
    def __init__(self, project: str = "LLM Observability Demo", *, v3_server: bool = False,
                 page_size: Optional[int] = None, empty_pages: int = 0) -> None:
        self.project = project
        self.projects = [{"id": "p1", "name": project}]
        self.v3_server = v3_server          # no Observations v2 and no Experiments API
        self.page_size = page_size          # force small pages to exercise pagination
        self.empty_pages = empty_pages      # empty pages (with a cursor) before each cursor result
        self.observations: List[dict] = []  # full rows: id, traceId, name, type, startTime, input, output, metadata
        self.scores: List[dict] = []        # id, name, value, dataType, subject {kind, id, traceId}
        self.prompts: Dict[str, Dict[int, dict]] = {}   # name -> version -> prompt
        self.datasets: Dict[str, dict] = {}
        self.items: Dict[str, Dict[str, dict]] = {}     # dataset -> id -> item
        self.experiments: List[dict] = []               # name, datasetId
        self.fail: Dict[Tuple[str, str], v.ApiError] = {}  # (method, path prefix) -> error to raise
        self.calls: List[Tuple[str, str, dict, Any]] = []
        self.bases: List[str] = []          # the base URL of every client that made a request

    # --- wiring ---------------------------------------------------------------------------
    def install(self, monkeypatch) -> "FakeLangfuse":
        fake = self

        def request(client, method, path, params=None, body=None):
            fake.bases.append(client.base)
            return fake.handle(method, path, dict(params or {}), copy.deepcopy(body))

        monkeypatch.setattr(v.Langfuse, "request", request)
        return self

    def writes(self) -> List[Tuple[str, str]]:
        return [(m, p) for m, p, _, _ in self.calls if m != "GET"]

    # --- seeding helpers ------------------------------------------------------------------
    def add_run(self, trace_id: str, workers: List[str], *, rounds: int = 1, start: str = "2099-01-01T00:00:00.000Z",
                symptom: str = "symptom", scores: Optional[List[str]] = None, worker_count: Optional[float] = None,
                overplan: bool = False) -> None:
        def obs(name, typ, **kw):
            self.observations.append(dict({"id": f"{trace_id}-{name}-{len(self.observations)}", "traceId": trace_id,
                                            "name": name, "type": typ, "startTime": start,
                                            "input": None, "output": None, "metadata": {}}, **kw))
        obs("LangGraph", "SPAN")
        for r in range(rounds):
            obs("orchestrator", "AGENT", input={"symptom": symptom, "round": r + 1},
                output={"tasks": [{"analysis_type": w} for w in workers], "reasoning": "r"})
            if not overplan:
                obs("plan-analyses", "GENERATION")
            obs("replan-gate", "EVALUATOR", output={"sufficient": r == rounds - 1})
        for w in workers:
            obs("worker", "AGENT", input={"analysis_type": w, "focus": "f"},
                output={"analysis_type": w, "summary": f"{w} healthy"}, metadata={"analysis_type": w, "focus": "f"})
            obs("run-system-query", "TOOL")
            obs("interpret-findings", "GENERATION")
        obs("synthesize-diagnosis", "GENERATION", output="diagnosis")
        wc = float(len(workers)) if worker_count is None else worker_count
        for name in (list(v.EXPECTED_SCORES) if scores is None else scores):
            value = wc if name == "worker_count" else 1.0
            self.add_score(name, value, trace_id)

    def add_score(self, name: str, value: float, trace_id: str, observation_id: Optional[str] = None) -> None:
        subject = ({"kind": "observation", "id": observation_id, "traceId": trace_id} if observation_id
                   else {"kind": "trace", "id": trace_id})
        self.scores.append({"id": f"s{len(self.scores)}", "name": name, "value": value, "dataType": "NUMERIC",
                            "subject": subject})

    def add_prompt(self, name: str, text: str, labels: List[str], config: Optional[dict] = None,
                   type_: str = "text") -> int:
        versions = self.prompts.setdefault(name, {})
        n = max(versions, default=0) + 1
        for p in versions.values():
            p["labels"] = [lab for lab in p["labels"] if lab not in labels]
        versions[n] = {"name": name, "version": n, "type": type_, "prompt": text, "config": config or {},
                       "labels": list(labels)}
        return n

    # --- pagination -----------------------------------------------------------------------
    def _cursor_page(self, rows: List[dict], params: Dict[str, Any], max_limit: int) -> dict:
        limit = int(params.get("limit", 50))
        if limit > max_limit:
            raise _bad(f"limit {limit} > {max_limit}")
        size = self.page_size or limit
        cur = params.get("cursor") or "c0"
        if cur.startswith("e"):  # an empty page, then the real offset
            n, off = cur[1:].split(":")
            nxt = f"e{int(n) + 1}:{off}" if int(n) + 1 < self.empty_pages else f"c{off}"
            return {"data": [], "meta": {"cursor": nxt}}
        off = int(cur[1:])
        if self.empty_pages and not params.get("cursor"):
            return {"data": [], "meta": {"cursor": f"e1:{off}" if self.empty_pages > 1 else f"c{off}"}}
        page = rows[off:off + size]
        meta = {"cursor": f"c{off + size}"} if off + size < len(rows) else {}
        return {"data": page, "meta": meta}

    def _page(self, rows: List[dict], params: Dict[str, Any], max_limit: int = 100) -> dict:
        limit = int(params.get("limit", 50))
        if limit > max_limit:
            raise _bad(f"limit {limit} > {max_limit}")
        size = self.page_size or limit
        page = int(params.get("page", 1))
        total = max(1, -(-len(rows) // size))
        return {"data": rows[(page - 1) * size:page * size],
                "meta": {"page": page, "limit": size, "totalItems": len(rows), "totalPages": total}}

    # --- routing --------------------------------------------------------------------------
    def handle(self, method: str, path: str, params: Dict[str, Any], body: Any) -> Any:
        self.calls.append((method, path, params, body))
        for (m, prefix), err in self.fail.items():
            if m == method and path.startswith(prefix):
                raise err
        seg = [urllib.parse.unquote(s) for s in path.split("/")[3:]]  # after /api/public
        if method == "GET" and path == "/api/public/projects":
            return {"data": self.projects}
        if path == "/api/public/v2/observations":
            if self.v3_server:
                raise v.ApiError(404, "The observations v2 API is only available in a Langfuse v4 write mode")
            return self._obs_v2(params)
        if path == "/api/public/observations":
            return self._obs_v1(params)
        if path == "/api/public/v3/scores":
            return self._scores(params)
        if method == "POST" and path == "/api/public/scores":
            self.add_score(body["name"], body["value"], body["traceId"], body.get("observationId"))
            return {"id": self.scores[-1]["id"]}
        if seg[:2] == ["v2", "prompts"]:
            return self._prompts(method, seg[2:], params, body)
        if seg[:2] == ["v2", "datasets"]:
            return self._datasets(method, seg[2:], body)
        if path == "/api/public/dataset-items":
            return self._items(method, params, body)
        if path == "/api/public/experiments":
            if self.v3_server:
                raise v.ApiError(404, "not found")
            _allow(params, EXPERIMENT_PARAMS, path)
            if not params.get("fromStartTime"):
                raise _bad("fromStartTime is required")
            rows = [e for e in self.experiments if e["datasetId"] in str(params.get("datasetId", "")).split(",")]
            return self._cursor_page(rows, params, 100)
        if seg[0] == "datasets" and seg[-1] == "runs":
            name = "/".join(seg[1:-1])
            ds = self.datasets.get(name)
            if not ds:
                raise v.ApiError(404, "dataset not found")
            return self._page([e for e in self.experiments if e["datasetId"] == ds["id"]], params)
        raise AssertionError(f"unexpected {method} {path}")

    def _filter_obs(self, params: Dict[str, Any]) -> List[dict]:
        for k in ("fromStartTime", "toStartTime"):
            if not params.get(k):
                raise _bad(f"{k} is required by these tests: every read is time-bounded")
            v.parse_time(params[k])
        rows = [o for o in self.observations
                if all(o.get(k) == params[k] for k in ("name", "traceId", "type") if k in params)]
        return sorted(rows, key=lambda o: o["startTime"], reverse=True)

    def _obs_v2(self, params: Dict[str, Any]) -> dict:
        _allow(params, OBS_V2_PARAMS, "v2/observations")
        if "parseIoAsJson" in params:
            raise _bad("parseIoAsJson is not supported")
        groups = str(params.get("fields") or "core,basic").split(",")
        if set(groups) - set(OBS_V2_GROUPS):
            raise _bad(f"unknown field group(s) {set(groups) - set(OBS_V2_GROUPS)}")
        keep = {f for g in groups for f in OBS_V2_GROUPS[g]}
        rows = []
        for o in self._filter_obs(params):
            r = {k: o.get(k) for k in keep if k in o}
            for k in ("input", "output"):
                if k in r and r[k] is not None:
                    r[k] = json.dumps(r[k])
            rows.append(r)
        return self._cursor_page(rows, params, 1000)

    def _obs_v1(self, params: Dict[str, Any]) -> dict:
        _allow(params, OBS_V1_PARAMS, "observations")
        return self._page([copy.deepcopy(o) for o in self._filter_obs(params)], params)

    def _scores(self, params: Dict[str, Any]) -> dict:
        _allow(params, SCORES_V3_PARAMS, "v3/scores")
        groups = [g for g in str(params.get("fields") or "").split(",") if g]
        if set(groups) - SCORES_V3_GROUPS:
            raise _bad(f"unknown score field group(s) {set(groups) - SCORES_V3_GROUPS}")
        rows = []
        for s in self.scores:
            if "name" in params and s["name"] != params["name"]:
                continue
            if "traceId" in params and s["subject"].get("traceId", s["subject"]["id"]) != params["traceId"]:
                continue
            r = {k: s[k] for k in ("id", "name", "value", "dataType")}
            if "subject" in groups:
                r["subject"] = dict(s["subject"])
            rows.append(r)
        return self._cursor_page(rows, params, 100)

    def _prompts(self, method: str, seg: List[str], params: Dict[str, Any], body: Any) -> Any:
        if not seg:
            if method == "GET":
                _allow(params, PROMPT_LIST_PARAMS, "v2/prompts")
                rows = [{"name": n, "type": next(iter(vs.values()))["type"], "versions": sorted(vs),
                         "labels": sorted({lab for p in vs.values() for lab in p["labels"]})}
                        for n, vs in self.prompts.items() if params.get("name") in (None, n)]
                return self._page(rows, params)
            if method == "POST":
                assert set(body) <= {"name", "type", "prompt", "labels", "config", "commitMessage", "tags"}, body
                existing = self.prompts.get(body["name"])
                if existing and next(iter(existing.values()))["type"] != body["type"]:
                    raise _bad("prompt type cannot change between versions")
                n = self.add_prompt(body["name"], body["prompt"], body.get("labels") or [], body.get("config"),
                                    body["type"])
                return copy.deepcopy(self.prompts[body["name"]][n])
        name = seg[0]
        versions = self.prompts.get(name, {})
        if method == "GET" and len(seg) == 1:
            if set(params) - {"label", "version"}:
                raise _bad(f"unknown prompt params {set(params)}")
            if "version" in params:
                p = versions.get(int(params["version"]))
            else:
                label = params.get("label", "production")
                p = next((p for p in versions.values() if label in p["labels"]), None)
            if not p:
                raise v.ApiError(404, "prompt not found")
            return copy.deepcopy(p)
        if method == "PATCH" and len(seg) == 3 and seg[1] == "versions":
            n = int(seg[2])
            if n not in versions:
                raise v.ApiError(404, "version not found")
            new = body["newLabels"]
            for p in versions.values():
                p["labels"] = [lab for lab in p["labels"] if lab not in new]
            versions[n]["labels"] = sorted(set(versions[n]["labels"]) | set(new))
            return copy.deepcopy(versions[n])
        raise AssertionError(f"unexpected prompt call {method} {seg}")

    def _datasets(self, method: str, seg: List[str], body: Any) -> Any:
        if method == "GET" and seg:
            name = "/".join(seg)
            if name not in self.datasets:
                raise v.ApiError(404, "dataset not found")
            return copy.deepcopy(self.datasets[name])
        if method == "POST" and not seg:
            ds = self.datasets.setdefault(body["name"], {"id": f"ds-{len(self.datasets) + 1}", "name": body["name"]})
            ds.update(description=body.get("description"), metadata=body.get("metadata"))
            return copy.deepcopy(ds)
        raise AssertionError(f"unexpected dataset call {method} {seg}")

    def _items(self, method: str, params: Dict[str, Any], body: Any) -> Any:
        if method == "GET":
            _allow(params, DATASET_ITEM_PARAMS, "dataset-items")
            name = params.get("datasetName")
            if name not in self.datasets:
                raise v.ApiError(404, "dataset not found")
            return self._page([copy.deepcopy(i) for i in self.items.get(name, {}).values()], params)
        if method == "POST":
            name = body["datasetName"]
            if name not in self.datasets:
                raise v.ApiError(404, "dataset not found")
            item = {"id": body.get("id") or f"auto-{len(self.calls)}", "datasetName": name,
                    "status": body.get("status") or "ACTIVE",
                    **{k: body.get(k) for k in ("input", "expectedOutput", "metadata", "sourceTraceId",
                                                "sourceObservationId")}}
            self.items.setdefault(name, {})[item["id"]] = item
            return copy.deepcopy(item)
        raise AssertionError(f"unexpected dataset-items call {method}")
