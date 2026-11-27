"""An in-memory Langfuse public API behind a fake `urllib.request.urlopen`.

The scripts under test build real URLs, query strings and JSON bodies; this server parses
them, rejects query parameters an endpoint does not take (400, as Langfuse does), honours
Observations v2 field groups and Scores v3 projection, pages lists by cursor (with empty
pages) or by page number, and can play a Langfuse 3.x server, where Observations v2 and
the experiments API answer 404. Scores carry the published Scores v3 subject shapes (a trace
subject has only an id; an observation subject adds its traceId; an experiment subject is
the dataset run id) and are filtered the way the API filters them. No network, Docker or
model calls.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

NOW = datetime.now(timezone.utc)


def ts(delta: timedelta = timedelta()) -> str:
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")


OBS_CORE = ("id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type")
OBS_GROUPS = {"core": OBS_CORE, "basic": ("name", "level", "statusMessage", "version", "environment",
                                         "userId", "sessionId", "isRootObservation"),
              "time": (), "io": ("input", "output"), "metadata": ("metadata",), "model": (), "usage": (),
              "prompt": (), "metrics": (), "trace_context": ()}
SCORE_CORE = ("id", "projectId", "name", "value", "dataType", "source", "timestamp", "environment",
              "createdAt", "updatedAt")
SCORE_GROUPS = {"details": ("comment", "configId", "metadata"), "subject": ("subject",),
                "annotation": ("authorUserId", "queueId")}
COMMON = {"limit", "page"}
CURSOR = {"limit", "cursor"}
PARAMS = {  # (method, path regex) -> accepted query parameters
    ("GET", r"/api/public/projects"): set(),
    ("GET", r"/api/public/v2/prompts/[^/]+"): {"label", "version"},
    ("POST", r"/api/public/v2/prompts"): set(),
    ("GET", r"/api/public/v2/datasets/[^/]+"): set(),
    ("POST", r"/api/public/v2/datasets"): set(),
    ("GET", r"/api/public/dataset-items"): COMMON | {"datasetName", "sourceTraceId", "sourceObservationId"},
    ("POST", r"/api/public/dataset-items"): set(),
    ("GET", r"/api/public/v2/observations"): CURSOR | {"fields", "name", "traceId", "type", "fromStartTime",
                                                      "toStartTime", "parentObservationId", "filter"},
    ("GET", r"/api/public/observations"): COMMON | {"name", "traceId", "type", "fromStartTime", "toStartTime",
                                                   "parentObservationId"},
    ("GET", r"/api/public/v3/scores"): CURSOR | {"fields", "name", "traceId", "observationId", "sessionId",
                                                "experimentId", "fromTimestamp", "toTimestamp"},
    ("GET", r"/api/public/experiments"): CURSOR | {"datasetId", "fromStartTime", "toStartTime", "fields", "name"},
    ("GET", r"/api/public/experiment-items"): CURSOR | {"experimentId", "fromStartTime", "toStartTime", "fields"},
    ("GET", r"/api/public/datasets/[^/]+/runs"): COMMON,
    ("GET", r"/api/public/datasets/[^/]+/runs/[^/]+"): set(),
    ("GET", r"/api/public/unstable/dashboards"): COMMON,
    ("POST", r"/api/public/unstable/dashboards"): set(),
    ("GET", r"/api/public/unstable/dashboards/[^/]+"): set(),
    ("PATCH", r"/api/public/unstable/dashboards/[^/]+"): set(),
    ("POST", r"/api/public/unstable/dashboards/[^/]+/placements"): set(),
    ("GET", r"/api/public/unstable/dashboard-widgets"): COMMON,
    ("POST", r"/api/public/unstable/dashboard-widgets"): set(),
    ("PATCH", r"/api/public/unstable/dashboard-widgets/[^/]+"): set(),
}


class _Resp:
    def __init__(self, status: int, body: Any):
        self.status = status
        self._raw = b"" if body is None else json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeLangfuse:
    """State plus a urlopen() that serves it. Tests mutate the state directly."""

    base = "https://lf.example"

    def __init__(self) -> None:
        self.project = {"id": "p1", "name": "LLM Observability Demo"}
        self.prompts: List[Dict[str, Any]] = []       # versions: name, version, type, prompt, config, labels
        self.datasets: Dict[str, Dict[str, Any]] = {}
        self.items: Dict[str, List[Dict[str, Any]]] = {}
        self.observations: List[Dict[str, Any]] = []
        self.scores: List[Dict[str, Any]] = []
        self.experiments: List[Dict[str, Any]] = []
        self.experiment_items: Dict[str, List[Dict[str, Any]]] = {}
        self.dataset_runs: Dict[str, List[Dict[str, Any]]] = {}      # dataset name -> run rows
        self.run_items: Dict[str, List[Dict[str, Any]]] = {}         # run id -> datasetRunItems
        self.dashboards: List[Dict[str, Any]] = []
        self.widgets: List[Dict[str, Any]] = []
        self.health: Dict[str, Tuple[Optional[int], Any]] = {}
        self.v3_server = False          # Observations v2 and experiments answer 404
        self.dashboards_api = True
        self.page_size = 100            # server-side cap per page
        self.empty_cursor_pages = 0     # empty pages served before data, each with a cursor
        self.repeat_cursor = False
        self.fail: Callable[[str, str, Dict[str, Any]], Optional[int]] = lambda method, path, body: None
        self.requests: List[Tuple[str, str, Dict[str, str]]] = []
        self.writes: List[Tuple[str, str, Any]] = []
        self._n = 0

    # --- helpers for tests ---------------------------------------------------------------

    def nid(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def add_obs(self, name: str, trace_id: str, parent: Optional[str], start: str, **extra: Any) -> dict:
        row = {"id": self.nid("o"), "traceId": trace_id, "parentObservationId": parent, "name": name,
               "startTime": start, "endTime": start, "projectId": "p1", "type": extra.pop("type", "SPAN"), **extra}
        self.observations.append(row)
        return row

    # --- urlopen ---------------------------------------------------------------------------

    def urlopen(self, req: Any, timeout: float = 0) -> _Resp:
        if isinstance(req, str):  # a /health probe
            status, body = self.health.get(req, (None, None))
            if status is None:
                raise urllib.error.URLError(ConnectionRefusedError("refused"))
            if status >= 400:
                raise urllib.error.HTTPError(req, status, "error", {}, io.BytesIO(b""))
            return _Resp(status, body)
        url = urllib.parse.urlsplit(req.full_url)
        method = req.get_method()
        query = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        path = urllib.parse.unquote(url.path)
        body = json.loads(req.data) if req.data else None
        self.requests.append((method, path, query))
        status, out = self._route(method, path, query, body)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", {},
                                         io.BytesIO(json.dumps({"message": out}).encode()))
        return _Resp(status, out)

    def _route(self, method: str, path: str, q: Dict[str, str], body: Any) -> Tuple[int, Any]:
        spec = next((allowed for (m, rx), allowed in PARAMS.items() if m == method and re.fullmatch(rx, path)), None)
        if spec is None:
            return 404, f"no route {method} {path}"
        bad = set(q) - spec
        if bad:
            return 400, f"invalid query parameter(s) {sorted(bad)}"
        if "limit" in q and not 1 <= int(q["limit"]) <= 100:
            return 400, "limit must be 1..100"
        injected = self.fail(method, path, body)
        if injected:
            return injected, "injected failure"
        if method != "GET":
            self.writes.append((method, path, body))
        handler = getattr(self, "_" + method.lower() + "_" + self._name(path))
        return handler(path, q, body)

    @staticmethod
    def _name(path: str) -> str:
        p = path.replace("/api/public/", "")
        rules = [(r"v2/prompts/.+", "prompt"), (r"v2/prompts", "prompts"), (r"v2/datasets/.+", "dataset"),
                 (r"v2/datasets", "datasets"), (r"dataset-items", "items"), (r"v2/observations", "obs_v2"),
                 (r"observations", "obs_v1"), (r"v3/scores", "scores"), (r"experiments", "experiments"),
                 (r"experiment-items", "experiment_items"), (r"datasets/[^/]+/runs", "runs"),
                 (r"datasets/[^/]+/runs/[^/]+", "run"),
                 (r"unstable/dashboards/.+/placements", "placement"), (r"unstable/dashboards/.+", "dashboard"),
                 (r"unstable/dashboards", "dashboards"), (r"unstable/dashboard-widgets/.+", "widget"),
                 (r"unstable/dashboard-widgets", "widgets"), (r"projects", "projects")]
        return next(name for rx, name in rules if re.fullmatch(rx, p))

    # --- paging ------------------------------------------------------------------------------

    def _cursor_page(self, rows: List[dict], q: Dict[str, str]) -> Tuple[int, Any]:
        limit = min(int(q.get("limit", 50)), self.page_size)
        cur = q.get("cursor")
        if cur and cur.startswith("empty"):
            n = int(cur[5:])
            if n < self.empty_cursor_pages:
                return 200, {"data": [], "meta": {"cursor": f"empty{n + 1}"}}
            cur = None
        elif cur is None and self.empty_cursor_pages:
            return 200, {"data": [], "meta": {"cursor": "empty1"}}
        offset = int(cur) if cur else 0
        page = rows[offset:offset + limit]
        meta = {}
        if offset + limit < len(rows):
            meta["cursor"] = "0" if self.repeat_cursor else str(offset + limit)
        return 200, {"data": page, "meta": meta}

    def _page(self, rows: List[dict], q: Dict[str, str]) -> Tuple[int, Any]:
        limit = min(int(q.get("limit", 50)), self.page_size)
        page = int(q.get("page", 1))
        total_pages = max(1, -(-len(rows) // limit))
        return 200, {"data": rows[(page - 1) * limit:page * limit],
                     "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": total_pages}}

    # --- projects, prompts, datasets ---------------------------------------------------------

    def _get_projects(self, path, q, body):
        return 200, {"data": [self.project] if self.project else []}

    def _get_prompt(self, path, q, body):
        name = path.rsplit("/", 1)[1]
        rows = [p for p in self.prompts if p["name"] == name and q.get("label", "production") in p["labels"]]
        return (200, dict(rows[-1])) if rows else (404, "prompt not found")

    def _post_prompts(self, path, q, body):
        versions = [p for p in self.prompts if p["name"] == body["name"]]
        for p in versions:
            p["labels"] = [lb for lb in p["labels"] if lb not in body.get("labels", [])]
        row = {"name": body["name"], "version": len(versions) + 1, "type": body["type"], "prompt": body["prompt"],
               "config": body.get("config") or {}, "labels": list(body.get("labels", []))}
        self.prompts.append(row)
        return 201, row

    def _get_dataset(self, path, q, body):
        ds = self.datasets.get(path.rsplit("/", 1)[1])
        return (200, ds) if ds else (404, "dataset not found")

    def _post_datasets(self, path, q, body):  # upsert by name, as Langfuse does
        ds = self.datasets.setdefault(body["name"], {"id": self.nid("ds"), "name": body["name"]})
        ds.update(description=body.get("description"), metadata=body.get("metadata"))
        self.items.setdefault(body["name"], [])
        return 200, ds

    def _get_items(self, path, q, body):
        return self._page(self.items.get(q["datasetName"], []), q)

    def _post_items(self, path, q, body):
        rows = self.items.setdefault(body["datasetName"], [])
        row = next((r for r in rows if r["id"] == body.get("id")), None)
        if row is None:
            row = {"id": body.get("id") or self.nid("item"), "status": "ACTIVE"}
            rows.append(row)
        row.update(input=body.get("input"), expectedOutput=body.get("expectedOutput"),
                   metadata=body.get("metadata"), status=body.get("status", row["status"]))
        return 200, row

    # --- observations --------------------------------------------------------------------------

    def _match_obs(self, q: Dict[str, str]) -> List[dict]:
        rows = [o for o in self.observations
                if (not q.get("name") or o["name"] == q["name"])
                and (not q.get("traceId") or o["traceId"] == q["traceId"])
                and (not q.get("fromStartTime") or o["startTime"] >= q["fromStartTime"])
                and (not q.get("toStartTime") or o["startTime"] < q["toStartTime"])]
        return sorted(rows, key=lambda o: o["startTime"], reverse=True)

    def _get_obs_v2(self, path, q, body):
        if self.v3_server:
            return 404, "The observations v2 API is only available in a Langfuse v4 write mode."
        groups = set((q.get("fields") or "core").split(","))
        if groups - set(OBS_GROUPS):
            return 400, f"invalid field groups {sorted(groups - set(OBS_GROUPS))}"
        keep = set(OBS_CORE).union(*(OBS_GROUPS[g] for g in groups))
        rows = [{k: v for k, v in o.items() if k in keep} for o in self._match_obs(q)]
        return self._cursor_page(rows, q)

    def _get_obs_v1(self, path, q, body):
        return self._page(self._match_obs(q), q)

    def _get_scores(self, path, q, body):
        groups = set((q.get("fields") or "").split(",")) - {""}
        if groups - set(SCORE_GROUPS):
            return 400, f"invalid field groups {sorted(groups - set(SCORE_GROUPS))}"
        scoped = [k for k in ("traceId", "sessionId", "experimentId") if q.get(k)]
        if len(scoped) > 1:
            return 400, f"{' and '.join(scoped)} are mutually exclusive"
        if q.get("observationId") and not q.get("traceId"):
            return 400, "observationId requires traceId"
        listed = lambda k: set(q[k].split(",")) if q.get(k) else None  # noqa: E731
        names, traces, observations, runs = listed("name"), listed("traceId"), listed("observationId"), listed("experimentId")
        keep = set(SCORE_CORE).union(*(SCORE_GROUPS[g] for g in groups))
        rows = []
        for sc in self.scores:
            subject = sc["subject"]
            trace = subject["id"] if subject["kind"] == "trace" else subject.get("traceId")
            if ((names is None or sc["name"] in names)
                    and (traces is None or trace in traces)
                    and (observations is None or (subject["kind"] == "observation" and subject["id"] in observations))
                    and (runs is None or (subject["kind"] == "experiment" and subject["id"] in runs))
                    and (not q.get("fromTimestamp") or sc["timestamp"] >= q["fromTimestamp"])
                    and (not q.get("toTimestamp") or sc["timestamp"] < q["toTimestamp"])):
                rows.append({k: v for k, v in sc.items() if k in keep})
        return self._cursor_page(rows, q)

    def add_score(self, name: str, subject_id: str, value: Any = 1, when: str = "", kind: str = "trace",
                  trace_id: Optional[str] = None) -> dict:
        """A score on a trace (the default), an observation (pass its trace_id) or an experiment run."""
        subject = {"kind": kind, "id": subject_id}
        if kind == "observation":
            subject["traceId"] = trace_id
        row = {"id": self.nid("s"), "projectId": "p1", "name": name, "value": value, "dataType": "NUMERIC",
               "source": "API", "timestamp": when or ts(), "environment": "default", "createdAt": when or ts(),
               "updatedAt": when or ts(), "comment": None, "configId": None, "metadata": {},
               "authorUserId": None, "queueId": None, "subject": subject}
        self.scores.append(row)
        return row

    # --- experiments -----------------------------------------------------------------------------

    def _get_experiments(self, path, q, body):
        if self.v3_server:
            return 404, "only available in a Langfuse v4 write mode"
        if "fromStartTime" not in q:
            return 400, "fromStartTime is required"
        rows = [e for e in self.experiments if e["datasetId"] == q.get("datasetId") and e["startTime"] >= q["fromStartTime"]]
        groups = set(q.get("fields", "core").split(","))
        rows = [{k: v for k, v in e.items() if k != "scores" or "scores" in groups} for e in rows]
        return self._cursor_page(rows, q)

    def _get_experiment_items(self, path, q, body):
        if self.v3_server:
            return 404, "only available in a Langfuse v4 write mode"
        if "fromStartTime" not in q:
            return 400, "fromStartTime is required"
        groups = set(q.get("fields", "core").split(","))
        rows = [{k: v for k, v in i.items() if k != "scores" or "scores" in groups}
                for i in self.experiment_items.get(q["experimentId"], [])]
        return self._cursor_page(rows, q)

    def _get_runs(self, path, q, body):
        name = path.split("/")[-2]
        if name not in self.datasets:
            return 404, "dataset not found"
        return self._page(self.dataset_runs.get(name, []), q)

    def _get_run(self, path, q, body):  # GET /datasets/{name}/runs/{runName}: the run and its items
        _, name, _, run_name = path.replace("/api/public/", "").split("/")
        run = next((r for r in self.dataset_runs.get(name, []) if r["name"] == run_name), None)
        if run is None:
            return 404, "dataset run not found"
        return 200, dict(run, datasetRunItems=list(self.run_items.get(run["id"], [])))

    # --- dashboards ------------------------------------------------------------------------------

    def _dash_api(self):
        return None if self.dashboards_api else (404, "no route")

    def _get_dashboards(self, path, q, body):
        return self._dash_api() or self._page([{k: v for k, v in d.items() if k != "definition"} for d in self.dashboards], q)

    def _post_dashboards(self, path, q, body):
        if not self.dashboards_api:
            return 404, "no route"
        d = {"id": self.nid("dash"), "name": body["name"], "description": body.get("description"),
             "definition": {"widgets": []}}
        self.dashboards.append(d)
        return 200, d

    def _find(self, rows: List[dict], path: str) -> Optional[dict]:
        return next((r for r in rows if r["id"] == path.rstrip("/").split("/")[-1]), None)

    def _get_dashboard(self, path, q, body):
        d = self._find(self.dashboards, path)
        return self._dash_api() or ((200, d) if d else (404, "dashboard not found"))

    def _patch_dashboard(self, path, q, body):
        d = self._find(self.dashboards, path)
        if not d:
            return 404, "dashboard not found"
        d.update(body)
        return 200, d

    def _post_placement(self, path, q, body):
        d = self._find(self.dashboards, path.rsplit("/placements", 1)[0])
        if not d or body.get("type") != "widget" or not {"x", "y", "width", "height"} <= set(body):
            return 400, "bad placement"
        d["definition"]["widgets"].append({"id": self.nid("pl"), "type": "widget", "widgetId": body["widgetId"],
                                           "x": body["x"], "y": body["y"], "x_size": body["width"],
                                           "y_size": body["height"]})
        return 200, d

    def _get_widgets(self, path, q, body):
        return self._dash_api() or self._page(self.widgets, q)

    def _post_widgets(self, path, q, body):
        if body.get("view") not in ("observations", "scores-numeric", "scores-boolean", "scores-categorical"):
            return 400, f"invalid view {body.get('view')}"
        w = dict(body, id=self.nid("w"))
        self.widgets.append(w)
        return 200, w

    def _patch_widget(self, path, q, body):
        w = self._find(self.widgets, path)
        if not w:
            return 404, "widget not found"
        w.update(body)
        return 200, w


# --- a project that is ready for the full script ---------------------------------------------

def ready_project(fake: FakeLangfuse, v: Any, seed: Any) -> FakeLangfuse:
    """Fill `fake` with everything the full script needs, using the scripts' own definitions."""
    mod = seed.load_prompt_seeder()
    for spec in seed.prompt_specs(mod):
        fake.prompts.append({"name": spec["name"], "version": len(fake.prompts) + 1, "type": "text",
                             "prompt": spec["prompt"], "config": spec["config"], "labels": [spec["label"]]})
    fake.datasets[v.DATASET_NAME] = {"id": "ds1", "name": v.DATASET_NAME, "description": seed.DATASET_DESCRIPTION,
                                     "metadata": seed.DATASET_METADATA}
    fake.items[v.DATASET_NAME] = [
        {"id": i["id"], "status": "ACTIVE", "input": i["input"], "expectedOutput": i["expected_output"],
         "metadata": i["metadata"]} for i in seed.dataset_items(seed.DATASET_SEEDER.read_text())]
    # 14 days of synthetic history: route-query rows with no parent, both weeks well filled.
    for day in range(14):
        for n in range(16):
            t = f"h{day}-{n}"
            fake.add_obs("route-query", t, None, ts(-timedelta(days=day, hours=1, minutes=n)), type="GENERATION")
    fake.add_score("routing_correct", "h1-0", 0, ts(-timedelta(days=1)))
    # Live runs: one per route plus a fallback, the newest first.
    for i, (route, extra) in enumerate([("docs_complex", 3), ("analytics_sql", 3), ("docs_simple", 1), (None, 0)]):
        trace = f"live-{route or 'fallback'}"
        start = ts(-timedelta(minutes=10 * (i + 1)))
        root = fake.add_obs("route-and-dispatch", trace, None, start)
        fake.add_obs("route-query", trace, root["id"], start, type="GENERATION")
        if route:
            d = fake.add_obs(f"dispatch-{route}", trace, root["id"], start)
            for k in range(extra):
                fake.add_obs(f"handler-step-{k}", trace, d["id"], start)
        else:
            fake.add_obs("fallback-handler", trace, root["id"], start, type="GENERATION")
            fake.add_obs("escalate-to-human", trace, root["id"], start, type="EVENT")
        fake.add_score("router_confidence", trace, 0.9)
    fake.add_score("route-plausibility", "live-docs_complex", "docs_complex")
    # The Router Ops dashboard, as seed_all creates it.
    dash = {"id": "dash1", "name": v.DASHBOARD_NAME, "description": v.DASHBOARD_DESCRIPTION, "definition": {"widgets": []}}
    for spec in v.WIDGETS:
        w = {k: spec[k] for k in ("name",) + v.WIDGET_FIELDS}
        w.update(id=fake.nid("w"), chartConfig={"type": spec["chartType"]})
        fake.widgets.append(w)
        dash["definition"]["widgets"].append({"id": fake.nid("pl"), "type": "widget", "widgetId": w["id"]})
    fake.dashboards.append(dash)
    # Act 5: the three experiment runs, each with run- and item-level scores. The experiments
    # API embeds them; on a 3.x server they live in Scores v3, bound to the run's own traces
    # (observation subjects, as the SDK writes them) and to the run (experiment subject).
    for run in v.experiment_run_names():
        e = {"id": fake.nid("exp"), "name": run, "datasetId": "ds1", "startTime": ts(-timedelta(days=1)),
             "scores": [{"name": "avg_route_accuracy", "value": 0.93}]}
        fake.experiments.append(e)
        fake.experiment_items[e["id"]] = [{"id": fake.nid("ei"), "traceId": fake.nid("t"),
                                           "scores": [{"name": "route-match", "value": 1}]} for _ in range(3)]
        fake.dataset_runs.setdefault(v.DATASET_NAME, []).append(
            {"id": e["id"], "name": run, "datasetId": "ds1", "datasetName": v.DATASET_NAME,
             "createdAt": e["startTime"], "updatedAt": e["startTime"]})
        fake.run_items[e["id"]] = [
            {"id": fake.nid("dri"), "datasetRunId": e["id"], "datasetRunName": run, "datasetItemId": f"item-{k}",
             "traceId": item["traceId"], "observationId": None, "createdAt": e["startTime"]}
            for k, item in enumerate(fake.experiment_items[e["id"]])]
        for item in fake.experiment_items[e["id"]]:
            fake.add_score("route-match", fake.nid("task"), 1, kind="observation", trace_id=item["traceId"])
        fake.add_score("avg_route_accuracy", e["id"], 0.93, kind="experiment")
    # Services.
    handlers = {r: "ok" for r in v.ROUTES}
    for name, url in v.services({}):
        fake.health[url] = (200, {"status": "ok", "langfuse": True, "handlers": handlers} if name == "query-router"
                            else {"status": "ok"})
    return fake
