"""An offline Langfuse public API and local services, served at the urllib.urlopen level.

verify_demo.py and seed_all.py build their real query strings and parse real JSON bodies
against it. Each endpoint accepts only the parameters and field groups the live API takes
(checked against Cloud on 2026-11-27) and projects rows by the requested field groups,
so a missing `basic` or `subject` group shows up as a missing field, as it would live.
"""
import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


ENDPOINT_PARAMS = {
    "projects": set(),
    "prompt": {"label", "version"},
    "dataset": set(),
    "dataset-items": {"datasetName", "page", "limit"},
    "observations": {"name", "isRootObservation", "environment", "fields", "limit", "cursor",
                     "fromStartTime", "toStartTime", "traceId", "type"},
    "scores": {"traceId", "fields", "limit", "cursor", "name"},
    "experiments": {"datasetId", "fields", "limit", "cursor", "fromStartTime", "toStartTime", "name"},
}
FIELD_GROUPS = {
    "observations": {"core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"},
    "scores": {"subject"},             # core fields come back without asking
    "experiments": {"core", "metadata", "scores"},
}
OBS_FIELDS = {"core": {"id", "traceId", "startTime", "type", "parentObservationId"},
              "basic": {"name", "isRootObservation", "environment", "level"}}


class Response:
    def __init__(self, status, body, headers=None):
        self.status, self._body, self.headers = status, body, dict(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAPI:
    """Mutable state of one project plus the two local services. Tests set attributes."""

    def __init__(self, *, workshop_id="t-bank", project="bank-workshop"):
        from verify_demo import PROMPT_LABELS, item_id, local_items, local_prompts
        self.workshop_id = workshop_id
        self.prompt_name = f"bank-workshop/{workshop_id}/assistant"
        self.dataset_name = f"bank-workshop/{workshop_id}/regression-v1"
        self.projects = [{"id": "p1", "name": project}]
        self.texts = local_prompts()
        self.prompts = {label: {"type": "text", "prompt": self.texts[variant], "config": {"variant": variant},
                                "version": n + 1} for n, (label, variant) in enumerate(PROMPT_LABELS.items())}
        self.dataset = {"id": "d1", "name": self.dataset_name}
        self.items = local_items()
        self.rows = [{"id": item_id(self.dataset_name, i["id"]), "status": "ACTIVE", "input": i["input"],
                      "expectedOutput": i["expected_output"], "metadata": i["metadata"]} for i in self.items]
        self.roots = [{"id": "root1", "traceId": "t1", "startTime": iso(NOW - timedelta(hours=1)),
                       "type": "AGENT", "name": "northwind-assistant", "isRootObservation": True,
                       "environment": "workshop"}]
        self.trace_obs = [{"id": f"o{n}", "traceId": "t1", "startTime": iso(NOW), "type": "SPAN"} for n in range(5)]
        self.scores = [{"id": f"s{n}", "name": name, "value": 0, "dataType": "BOOLEAN",
                        "subject": {"kind": "observation", "id": "root1", "traceId": "t1"}}
                       for n, name in enumerate(["guardrail-blocked", "pii-in-input", "all-caps-signal"])]
        self.experiments = [{"id": f"run-{label}", "name": f"{label}-run", "datasetId": "d1",
                             "startTime": iso(NOW - timedelta(days=1)),
                             "metadata": {"prompt_label": label, "prompt_version": str(p["version"])}}
                            for label, p in self.prompts.items()]
        self.portal = (200, {"status": "ok", "tracing": True, "workshop": workshop_id})
        # What answers on 127.0.0.1:8765. "banking" behaves like FastMCP's streamable HTTP
        # transport as probed on 2026-11-27 (mcp 1.30.0); "json" is its json_response mode;
        # "not-found" an unrelated server; 401/403 an auth proxy; "other-mcp" another MCP
        # server; "no-result" a 200 with no JSON-RPC result; None nothing listening.
        self.mcp = "banking"
        self.mcp_name = "northwind-core-banking"
        self.mcp_calls = []                # (method, JSON-RPC method or None, session header)
        self.mcp_sessions = set()
        self.page_size = 100
        self.empty_pages = 0               # cursor pages with no rows before the first real page
        self.root_empty_pages = 0          # the same, only for the latest-turn (root) listing
        self.repeat_cursor = False
        self.fail = {}                     # endpoint -> HTTP status to return
        self.calls = []                    # (endpoint, params)

    # --- transport -----------------------------------------------------------------

    def urlopen(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        parts = urllib.parse.urlsplit(url)
        if parts.hostname == "127.0.0.1":
            return self._service(req, parts)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        endpoint, arg = self._route(parts.path)
        self.calls.append((endpoint, params))
        unknown = set(params) - ENDPOINT_PARAMS[endpoint]
        if unknown:
            return self._error(url, 400, {"error": [{"code": "unrecognized_keys", "keys": sorted(unknown)}]})
        groups = set(params.get("fields", "").split(",")) - {""}
        if endpoint in FIELD_GROUPS and groups - FIELD_GROUPS[endpoint]:
            return self._error(url, 400, {"error": [{"code": "invalid_value", "path": ["fields"]}]})
        if endpoint in self.fail:
            return self._error(url, self.fail[endpoint], {"message": "injected"})
        status, body = getattr(self, "_" + endpoint.replace("-", "_"))(arg, params, groups)
        if status != 200:
            return self._error(url, status, body)
        return Response(200, json.dumps(body).encode())

    def _error(self, url, status, body):
        raise urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))

    def _service(self, req, parts):
        if parts.port == 8090 and parts.path == "/health" and self.portal:
            status, body = self.portal
            if status != 200:
                raise urllib.error.HTTPError(parts.geturl(), status, "error", {}, io.BytesIO(b""))
            return Response(200, json.dumps(body).encode())
        if parts.port == 8765 and self.mcp:
            return self._mcp(req, parts)
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    def _mcp(self, req, parts):
        def fail(status):
            raise urllib.error.HTTPError(parts.geturl(), status, "error", {}, io.BytesIO(b""))
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        session = req.get_header("Mcp-session-id") if hasattr(req, "get_header") else None
        body = json.loads(req.data) if getattr(req, "data", None) else None
        self.mcp_calls.append((method, (body or {}).get("method"), session))
        if self.mcp in (401, 403, 406) or self.mcp == "not-found":
            fail(404 if self.mcp == "not-found" else self.mcp)
        if parts.path != "/mcp":
            fail(404)
        if method == "DELETE":
            if session not in self.mcp_sessions:
                fail(404)
            self.mcp_sessions.discard(session)
            return Response(200, b"")
        accept = req.get_header("Accept") or ""
        if "text/event-stream" not in accept or (method == "POST" and "application/json" not in accept):
            fail(406)                      # what a bare GET or a POST without both types gets
        if method != "POST" or (req.get_header("Content-type") or "") != "application/json":
            fail(400)
        if body.get("jsonrpc") != "2.0" or body.get("method") != "initialize" or "id" not in body:
            fail(400)                      # without a session only initialize is accepted
        if self.mcp == "no-result":
            return Response(200, b"", {"content-type": "text/event-stream"})
        sid = f"sess{len(self.mcp_sessions) + 1}"
        self.mcp_sessions.add(sid)
        msg = {"jsonrpc": "2.0", "id": body["id"], "result": {
            "protocolVersion": body["params"]["protocolVersion"], "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.mcp_name if self.mcp != "other-mcp" else "some-other-server",
                           "version": "1.30.0"}}}
        if self.mcp == "json":
            return Response(200, json.dumps(msg).encode(), {"content-type": "application/json", "mcp-session-id": sid})
        return Response(200, f"event: message\ndata: {json.dumps(msg)}\n\n".encode(),
                        {"content-type": "text/event-stream", "mcp-session-id": sid})

    def _route(self, path):
        if path == "/api/public/projects":
            return "projects", None
        for prefix, name in (("/api/public/v2/prompts/", "prompt"), ("/api/public/v2/datasets/", "dataset")):
            if path.startswith(prefix):
                raw = path[len(prefix):]
                assert "/" not in raw, f"name not URL-encoded: {raw}"
                return name, urllib.parse.unquote(raw)
        for p, name in (("/api/public/dataset-items", "dataset-items"), ("/api/public/v2/observations", "observations"),
                        ("/api/public/v3/scores", "scores"), ("/api/public/experiments", "experiments")):
            if path == p:
                return name, None
        raise AssertionError(f"unexpected endpoint {path}")

    # --- endpoints -----------------------------------------------------------------

    def _projects(self, arg, params, groups):
        return 200, {"data": self.projects}

    def _prompt(self, name, params, groups):
        p = self.prompts.get(params.get("label")) if name == self.prompt_name else None
        return (200, dict(p, name=name)) if p else (404, {"message": "Prompt not found"})

    def _dataset(self, name, params, groups):
        return (200, self.dataset) if self.dataset and name == self.dataset_name else (404, {"message": "not found"})

    def _dataset_items(self, arg, params, groups):
        rows = self.rows if self.dataset and params["datasetName"] == self.dataset_name else []
        size = int(params.get("limit", 50))
        page = int(params.get("page", 1))
        total = max(1, -(-len(rows) // size))
        return 200, {"data": rows[(page - 1) * size: page * size],
                     "meta": {"page": page, "limit": size, "totalItems": len(rows), "totalPages": total}}

    def _paged(self, rows, params, extra_empty=0):
        """Cursor paging: optional empty pages first, then page_size rows per page."""
        cur = params.get("cursor")
        n = int(cur[1:]) if cur else 0
        size = min(self.page_size, int(params.get("limit", 50)))
        pages = [[]] * (self.empty_pages + extra_empty) + [rows[i:i + size] for i in range(0, len(rows), size)]
        pages = pages or [[]]
        data = pages[n] if n < len(pages) else []
        meta = {}
        if n + 1 < len(pages):
            meta["cursor"] = "c1" if self.repeat_cursor else f"c{n + 1}"
        return 200, {"data": data, "meta": meta}

    def _observations(self, arg, params, groups):
        assert params.get("fromStartTime") and params.get("toStartTime"), "observation reads must be time-bounded"
        keep = set().union(*(OBS_FIELDS.get(g, set()) for g in groups)) if groups else OBS_FIELDS["core"]
        if "traceId" in params:
            rows = [r for r in self.trace_obs if r["traceId"] == params["traceId"]]
        else:
            rows = [r for r in self.roots if r.get("name") == params.get("name")
                    and r.get("environment") == params.get("environment")
                    and str(r.get("isRootObservation")).lower() == params.get("isRootObservation", "true")]
            rows = sorted(rows, key=lambda r: r["startTime"], reverse=True)
            return self._paged([{k: v for k, v in r.items() if k in keep} for r in rows], params, self.root_empty_pages)
        return self._paged([{k: v for k, v in r.items() if k in keep} for r in rows], params)

    def _scores(self, arg, params, groups):
        rows = [s for s in self.scores if (s.get("subject") or {}).get("traceId") == params.get("traceId")]
        rows = [{k: v for k, v in s.items() if k != "subject" or "subject" in groups} for s in rows]
        return self._paged(rows, params)

    def _experiments(self, arg, params, groups):
        rows = [r for r in self.experiments if r["datasetId"] == params.get("datasetId")]
        rows = [{k: v for k, v in r.items() if k != "metadata" or "metadata" in groups} for r in rows]
        return self._paged(rows, params)

    # --- what `workshop.py prompt` and `workshop.py dataset` would write ------------

    def apply_workshop(self, args):
        """Mimic the two sub-commands' writes (helpers.publish_prompt / seed_dataset)."""
        from verify_demo import item_id
        if args[0] == "prompt":
            variant, label = args[args.index("--variant") + 1], args[args.index("--label") + 1]
            version = 1 + max([p["version"] for p in self.prompts.values()] or [0])
            self.prompts[label] = {"type": "text", "prompt": self.texts[variant], "config": {"variant": variant},
                                   "version": version}
        elif args[0] == "dataset":
            self.dataset = self.dataset or {"id": "d1", "name": self.dataset_name}
            by_id = {r["id"]: r for r in self.rows}
            for i in self.items:
                rid = item_id(self.dataset_name, i["id"])
                by_id[rid] = {"id": rid, "status": "ACTIVE", "input": i["input"],
                              "expectedOutput": i["expected_output"], "metadata": i["metadata"]}
            self.rows = list(by_id.values())
