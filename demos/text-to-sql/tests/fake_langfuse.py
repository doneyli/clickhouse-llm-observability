"""An in-memory Langfuse public API behind `urllib.request.urlopen`, for offline tests.

Patching `urlopen` (not the client's methods) means the real `verify_demo.Langfuse` builds
every URL and query string and parses every response. The fake is strict where the real
API is strict, so a wrong request fails the test instead of passing silently:

- an unknown query parameter is a 400;
- Observations v2 returns only the field groups asked for (`core` always), and an unknown
  group is a 400; Scores v3 rejects `fields=core` (its core fields are automatic);
- v2 observations and v3 scores page by `meta.cursor`, and `empty_pages` inserts empty
  pages that still carry a cursor;
- `v2_observations = False` makes `/api/public/v2/observations` a 404, like server 3.221.1;
- the trace endpoints are a 410, like Langfuse Cloud since 2026-11-16;
- `fail[(method, path)] = status` makes that route fail; a list of statuses fails the
  first calls in turn (`None` lets one through).
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

OBS_GROUPS = {
    "core": ["id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"],
    "basic": ["name", "level", "statusMessage", "version", "environment"],
    "time": ["completionStartTime", "createdAt", "updatedAt"],
    "io": ["input", "output"],
    "metadata": ["metadata"],
    "model": ["providedModelName", "modelParameters"],
    "usage": ["usageDetails", "costDetails"],
    "prompt": ["promptId", "promptName", "promptVersion"],
    "metrics": ["latency", "timeToFirstToken"],
}
ALLOWED = {
    ("GET", "/api/public/projects"): set(),
    ("GET", "prompt"): {"label", "version"},
    ("GET", "/api/public/v2/prompts"): {"name", "label", "tag", "page", "limit", "fromUpdatedAt", "toUpdatedAt"},
    ("POST", "/api/public/v2/prompts"): set(),
    ("GET", "/api/public/v2/datasets"): {"page", "limit"},
    ("POST", "/api/public/v2/datasets"): set(),
    ("GET", "/api/public/dataset-items"): {"datasetName", "page", "limit", "sourceTraceId", "sourceObservationId"},
    ("POST", "/api/public/dataset-items"): set(),
    ("GET", "/api/public/v2/observations"): {"fields", "limit", "cursor", "name", "traceId", "type",
                                             "isRootObservation", "fromStartTime", "toStartTime",
                                             "parentObservationId", "userId", "level", "environment"},
    ("GET", "/api/public/observations"): {"page", "limit", "name", "traceId", "type", "userId",
                                          "parentObservationId", "fromStartTime", "toStartTime"},
    ("GET", "/api/public/v3/scores"): {"traceId", "observationId", "name", "cursor", "limit", "fields",
                                       "fromTimestamp", "toTimestamp", "dataType", "source"},
}


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeServer:
    def __init__(self, project: Optional[str] = "LLM Observability Demo") -> None:
        self.projects = [{"id": "p1", "name": project}] if project else []
        self.prompts: Dict[str, List[dict]] = {}
        self.datasets: Dict[str, dict] = {}
        self.items: Dict[str, List[dict]] = {}
        self.observations: List[dict] = []
        self.scores: List[dict] = []
        self.v2_observations = True
        self.empty_pages = False
        self.repeat_cursor = False
        self.fail: Dict[Tuple[str, str], Any] = {}
        self.requests: List[Tuple[str, str, Dict[str, str], Any]] = []
        self._ids = 0

    # --- plumbing ------------------------------------------------------------------------

    def urlopen(self, req: Any, timeout: Optional[float] = None) -> _Response:
        url = urllib.parse.urlsplit(req.full_url)
        params = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        body = json.loads(req.data) if req.data else None
        method = req.get_method()
        self.requests.append((method, url.path, params, body))
        status, payload = self.handle(method, url.path, params, body)
        raw = json.dumps(payload).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(raw))
        return _Response(raw)

    def writes(self) -> List[Tuple[str, Any]]:
        return [(p, b) for m, p, _, b in self.requests if m == "POST"]

    def _next_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    def _injected(self, method: str, path: str) -> Optional[int]:
        key = next((k for k in self.fail if k[0] == method and path.startswith(k[1])), None)
        if key is None:
            return None
        rule = self.fail[key]
        if isinstance(rule, list):
            return rule.pop(0) if rule else None
        return rule

    def handle(self, method: str, path: str, params: Dict[str, str], body: Any) -> Tuple[int, Any]:
        injected = self._injected(method, path)
        if injected:
            return injected, {"message": "injected failure"}
        if path.startswith("/api/public/traces"):
            return 410, {"message": "removed"}
        route = (method, "prompt") if (method == "GET" and path.startswith("/api/public/v2/prompts/")) \
            else (method, path)
        if route not in ALLOWED:
            return 404, {"message": f"no route {method} {path}"}
        unknown = set(params) - ALLOWED[route]
        if unknown:
            return 400, {"message": f"unknown query parameter(s) {sorted(unknown)}"}
        return getattr(self, "_" + {
            ("GET", "/api/public/projects"): "projects",
            ("GET", "prompt"): "get_prompt",
            ("GET", "/api/public/v2/prompts"): "list_prompts",
            ("POST", "/api/public/v2/prompts"): "post_prompt",
            ("GET", "/api/public/v2/datasets"): "list_datasets",
            ("POST", "/api/public/v2/datasets"): "post_dataset",
            ("GET", "/api/public/dataset-items"): "list_items",
            ("POST", "/api/public/dataset-items"): "post_item",
            ("GET", "/api/public/v2/observations"): "obs_v2",
            ("GET", "/api/public/observations"): "obs_v1",
            ("GET", "/api/public/v3/scores"): "scores_v3",
        }[route])(path, params, body)

    @staticmethod
    def _paged(rows: List[dict], params: Dict[str, str]) -> Dict[str, Any]:
        page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
        total_pages = max(1, -(-len(rows) // limit))
        return {"data": rows[(page - 1) * limit: page * limit],
                "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": total_pages}}

    def _cursored(self, rows: List[dict], params: Dict[str, str]) -> Dict[str, Any]:
        """Cursor pages of `limit` rows; with empty_pages, an empty page precedes each one."""
        limit = int(params.get("limit", 50))
        cur = params.get("cursor", "0")
        if self.repeat_cursor and cur != "0":
            return {"data": [], "meta": {"cursor": cur}}
        if cur.startswith("e"):            # the empty page before offset N
            start = int(cur[1:])
            nxt = start + limit
            return {"data": rows[start:nxt], "meta": {"cursor": str(nxt) if nxt < len(rows) else None}}
        start = int(cur)
        if self.empty_pages:
            return {"data": [], "meta": {"cursor": f"e{start}"}}
        nxt = start + limit
        return {"data": rows[start:nxt], "meta": {"cursor": str(nxt) if nxt < len(rows) else None}}

    # --- routes --------------------------------------------------------------------------

    def _projects(self, path, params, body):
        return 200, {"data": self.projects}

    def _get_prompt(self, path, params, body):
        name = urllib.parse.unquote(path.rsplit("/", 1)[1])
        label = params.get("label", "production")
        versions = [p for p in self.prompts.get(name, []) if label in p["labels"]]
        if not versions:
            return 404, {"message": "prompt not found"}
        return 200, dict(versions[-1], name=name)

    def _list_prompts(self, path, params, body):
        rows = [{"name": n, "versions": [p["version"] for p in vs],
                 "labels": sorted({x for p in vs for x in p["labels"]})}
                for n, vs in self.prompts.items() if vs and params.get("name") in (None, n)]
        return 200, self._paged(rows, params)

    def _post_prompt(self, path, params, body):
        for key in ("name", "type", "prompt"):
            if key not in body:
                return 400, {"message": f"missing {key}"}
        versions = self.prompts.setdefault(body["name"], [])
        for old in versions:                       # a label lives on one version
            old["labels"] = [x for x in old["labels"] if x not in body.get("labels", [])]
        row = {"version": len(versions) + 1, "type": body["type"], "prompt": body["prompt"],
               "config": body.get("config") or {}, "labels": list(body.get("labels", []))}
        versions.append(row)
        return 200, dict(row, name=body["name"])

    def _list_datasets(self, path, params, body):
        return 200, self._paged(list(self.datasets.values()), params)

    def _post_dataset(self, path, params, body):
        row = self.datasets.get(body["name"]) or {"id": self._next_id("ds-"), "name": body["name"]}
        row.update(description=body.get("description"), metadata=body.get("metadata"))
        self.datasets[body["name"]] = row
        self.items.setdefault(body["name"], [])
        return 200, row

    def _list_items(self, path, params, body):
        name = params.get("datasetName")
        if name not in self.datasets:
            return 404, {"message": "dataset not found"}
        return 200, self._paged(self.items[name], params)

    def _post_item(self, path, params, body):
        name = body.get("datasetName")
        if name not in self.datasets:
            return 404, {"message": "dataset not found"}
        rows = self.items[name]
        row = next((r for r in rows if body.get("id") and r["id"] == body["id"]), None)
        if row is None:
            row = {"id": body.get("id") or self._next_id("item-"), "datasetName": name}
            rows.append(row)
        for key in ("input", "expectedOutput", "metadata", "sourceTraceId", "sourceObservationId"):
            if key in body:
                row[key] = body[key]
        row["status"] = body.get("status", row.get("status", "ACTIVE"))
        return 200, row

    def _obs_v2(self, path, params, body):
        if not self.v2_observations:
            return 404, {"message": "not found"}
        groups = (params.get("fields") or "core,basic").split(",")
        if set(groups) - set(OBS_GROUPS):
            return 400, {"message": f"unknown field group(s) {groups}"}
        rows = self._filter_obs(params)
        if params.get("isRootObservation") == "true":
            rows = [r for r in rows if not r.get("parentObservationId")]
        keep = {f for g in set(groups) | {"core"} for f in OBS_GROUPS[g]}
        rows = [{k: r[k] for k in keep if k in r} for r in rows]
        return 200, self._cursored(rows, params)

    def _obs_v1(self, path, params, body):
        return 200, self._paged(self._filter_obs(params), params)

    def _filter_obs(self, params):
        rows = [r for r in self.observations
                if all(r.get(k) == params[k] for k in ("name", "traceId", "type") if k in params)]
        if "fromStartTime" in params:
            rows = [r for r in rows if params["fromStartTime"] <= r["startTime"][:19] + "Z"]
        if "toStartTime" in params:
            rows = [r for r in rows if r["startTime"][:19] + "Z" <= params["toStartTime"]]
        return sorted(rows, key=lambda r: r["startTime"], reverse=True)

    def _scores_v3(self, path, params, body):
        if "core" in (params.get("fields") or "").split(","):
            return 400, {"message": "core fields are always returned; do not request them"}
        rows = [s for s in self.scores if "traceId" not in params or s["traceId"] == params["traceId"]]
        return 200, self._cursored(rows, params)

    # --- fixtures ------------------------------------------------------------------------

    def add_run(self, trace_id: str = "t1", start: Optional[str] = None, question: str = "Q?",
                scores: Tuple[str, ...] = ("gate-pass", "sql-risk"), children: int = 6) -> None:
        """One text-to-sql run (an hour ago unless `start` says otherwise): the root span, an
        analysis chain + generation, and more children."""
        if start is None:
            t = datetime.now(timezone.utc) - timedelta(hours=1)
            start = t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.observations.append({"id": f"{trace_id}-root", "traceId": trace_id, "startTime": start,
                                  "parentObservationId": None, "type": "SPAN", "name": "text-to-sql"})
        self.observations.append({"id": f"{trace_id}-chain", "traceId": trace_id, "startTime": start,
                                  "parentObservationId": f"{trace_id}-root", "type": "CHAIN",
                                  "name": "RunnableSequence", "input": {"question": question},
                                  "metadata": {"purpose": "query_analysis", "attempt": 1}})
        self.observations.append({"id": f"{trace_id}-gen", "traceId": trace_id, "startTime": start,
                                  "parentObservationId": f"{trace_id}-chain", "type": "GENERATION",
                                  "name": "ChatAnthropic", "input": [{"role": "user", "content": question}],
                                  "metadata": {"purpose": "query_analysis"}})
        for i in range(max(0, children - 3)):
            self.observations.append({"id": f"{trace_id}-c{i}", "traceId": trace_id, "startTime": start,
                                      "parentObservationId": f"{trace_id}-root", "type": "SPAN",
                                      "name": f"step-{i}"})
        self.scores += [{"name": n, "traceId": trace_id} for n in scores]

    def add_refine_run(self, trace_id: str = "r1", start: Optional[str] = None,
                       stages: Tuple[str, ...] = ("generate-sql", "gather-evidence", "critique-sql"),
                       iterations: int = 1, output: Optional[dict] = None,
                       scores: Tuple[str, ...] = ("converged", "iterations_to_accept", "gate-pass", "sql-risk"),
                       root: bool = True) -> None:
        """One --refine run (two hours ago unless `start` says otherwise), shaped like
        sql_refine_loop.run_refine_loop writes it: a `sql-refine-loop` span under the root,
        `iterations` copies of each stage as its direct children, and the loop's output."""
        if start is None:
            t = datetime.now(timezone.utc) - timedelta(hours=2)
            start = t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if root:
            self.observations.append({"id": f"{trace_id}-root", "traceId": trace_id, "startTime": start,
                                      "parentObservationId": None, "type": "SPAN", "name": "text-to-sql"})
        loop = f"{trace_id}-loop"
        if output is None:
            output = {"converged": True, "iterations": iterations, "stop_reason": "accepted",
                      "critic_scores": [0.95] * iterations}
        self.observations.append({"id": loop, "traceId": trace_id, "startTime": start,
                                  "parentObservationId": f"{trace_id}-root" if root else None,
                                  "type": "SPAN", "name": "sql-refine-loop", "input": "Q?", "output": output})
        kinds = {"generate-sql": "GENERATION", "gather-evidence": "TOOL", "critique-sql": "EVALUATOR"}
        for i in range(1, iterations + 1):
            for st in stages:
                self.observations.append({"id": f"{trace_id}-{st}-{i}", "traceId": trace_id, "startTime": start,
                                          "parentObservationId": loop, "type": kinds.get(st, "SPAN"),
                                          "name": st, "metadata": {"iteration": i}})
        self.scores += [{"name": n, "traceId": trace_id} for n in scores]


def ready_server(monkeypatch: Any) -> FakeServer:
    """A server holding everything a full presentation needs, behind a patched urlopen:
    the seeded prompts (written straight from seed_all's definitions), the three datasets
    (written by the real seeders), one fresh run with its scores, and an older prepared
    --refine run for Act 3."""
    import seed_all as s
    import seed_refine_datasets
    import seed_step_dataset
    import verify_demo as v

    server = FakeServer()
    monkeypatch.setattr(v.urllib.request, "urlopen", server.urlopen)
    monkeypatch.setattr(v, "SERVICES", [])
    lf = v.Langfuse("http://lf.test", "pk", "sk")
    for spec in s.prompt_specs(s.load_module("seed_app_prompts", s.PROMPT_SEEDER)):
        lf.post("/api/public/v2/prompts", {"name": spec["name"], "type": spec["type"], "prompt": spec["prompt"],
                                           "config": spec["config"], "labels": [spec["label"]]})
    quiet = lambda status, what: None  # noqa: E731
    assert seed_step_dataset.seed(lf, False, report=quiet) == 0
    assert seed_refine_datasets.seed(lf, False, quiet) == 0
    server.add_refine_run()
    server.add_run()
    server.requests.clear()
    return server
