"""An in-process stand-in for Langfuse, clickhouse-vectors, the demo services and the
`docker exec` Postgres read, for the offline tests of verify_demo.py and seed_all.py.

It replaces `urllib.request.urlopen` and `subprocess.run`, so the scripts' real request
building, URL encoding, auth header, JSON parsing and pagination all run. Like the real
APIs it honours field projection (an observation's `name` is in the `basic` group, a
score's `subject` only comes with `fields=subject`) and answers unknown query parameters,
unknown field groups and oversized limits with HTTP 400.
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import urllib.error
import urllib.parse
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

NOW = "2099-01-01T00:00:00.000Z"  # always inside the lookback window
LF_HOST = "lf.example"
LF_BASE = f"https://{LF_HOST}"

OBS_GROUPS = {
    "core": {"id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"},
    "basic": {"name", "level", "statusMessage", "version", "environment", "isRootObservation"},
    **{g: set() for g in ("time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context")},
}
V2_OBS_PARAMS = {"fields", "limit", "cursor", "fromStartTime", "toStartTime", "traceId", "name", "type",
                 "userId", "level", "parentObservationId", "environment", "version", "filter",
                 "isRootObservation"}
V1_OBS_PARAMS = {"page", "limit", "name", "userId", "type", "traceId", "parentObservationId",
                 "environment", "fromStartTime", "toStartTime", "version"}
SCORE_CORE = {"id", "projectId", "name", "value", "dataType", "source", "timestamp", "environment",
              "createdAt", "updatedAt"}
SCORE_GROUPS = {"details", "subject", "annotation"}
V3_SCORE_PARAMS = {"limit", "cursor", "fields", "id", "name", "traceId", "sessionId", "observationId",
                   "experimentId", "fromTimestamp", "toTimestamp", "source", "dataType", "userId"}
PROMPT_BODY_KEYS = {"name", "type", "prompt", "labels", "config", "commitMessage", "tags"}


def obs(oid: str, trace: str, name: str, start: str = NOW, type_: str = "SPAN") -> dict:
    return {"id": oid, "traceId": trace, "startTime": start, "type": type_, "name": name,
            "parentObservationId": None, "projectId": "p1", "level": "DEFAULT", "isRootObservation": False}


def good_trace(trace: str = "t1", start: str = NOW) -> List[dict]:
    names = ["LangGraph", "route", "retrieve", "grade-relevance", "rewrite-query", "retrieve",
             "grade-relevance", "generate", "reflect-groundedness", "ChatAnthropic"]
    return [obs(f"{trace}-o{i}", trace, n, start) for i, n in enumerate(names)]


def score(name: str, value: float, trace: str, observation: Optional[str] = None, ts: str = NOW) -> dict:
    subject = ({"kind": "observation", "id": observation, "traceId": trace} if observation
               else {"kind": "trace", "id": trace})
    return {"id": f"s-{name}-{trace}-{value}", "projectId": "p1", "name": name, "value": value,
            "dataType": "NUMERIC", "source": "API", "timestamp": ts, "subject": subject}


def default_scores(trace: str = "t1") -> List[dict]:
    return [score("retrieval_relevance", 0, trace, f"{trace}-o3"), score("retrieval_relevance", 1, trace, f"{trace}-o6"),
            score("groundedness", 1, trace),
            *(score(n, 1, trace, f"{trace}-o7") for n in ("faithfulness", "context-relevance", "answer-relevance"))]


class Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status = body, status

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class Reject(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class FakeServer:
    """Mutable state per test; flip attributes to stage a failure."""

    def __init__(self) -> None:
        self.auth = "Basic " + base64.b64encode(b"pk:sk").decode()
        self.projects = [{"id": "p1", "name": "LLM Observability Demo"}]
        self.v2 = True                                    # False plays the self-hosted 3.221.1 server
        self.anchor_pages: List[List[dict]] = [[obs("t1-o3", "t1", "grade-relevance")]]
        self.repeat_cursor = False
        self.traces: Dict[str, List[dict]] = {"t1": good_trace("t1")}
        self.scores: List[dict] = default_scores("t1")
        self.prompt_versions: List[dict] = []             # [{version, type, prompt, config, labels}]
        self.post_status: List[int] = []                  # status per POST, in order; 200 when exhausted
        self.fail: Dict[str, int] = {}                    # path -> HTTP status to answer with
        self.ch_count, self.ch_fingerprint = 28, "1234567890"
        self.ch_index = "vector_similarity('hnsw', 'cosineDistance', 384)"
        self.ch_down = False
        self.down_ports: set = set()
        self.rules = {r: "ACTIVE" for r in ("arag-faithfulness", "arag-context-relevance", "arag-answer-relevance")}
        self.docker = "ok"                                # ok | missing | denied | timeout
        self.requests: List[tuple] = []                   # (method, path, params)
        self.posts: List[dict] = []
        self.commands: List[list] = []
        self.urls: List[str] = []                         # every urlopen, any host
        self.raise_on: Dict[str, BaseException] = {}      # path -> exception urlopen raises (transport faults)

    # --- helpers for tests ----------------------------------------------------------------
    def seed_prompt(self, prompt: str, config: dict, versions: int = 2) -> None:
        self.prompt_versions = [{"version": i + 1, "type": "text", "prompt": "baseline", "config": config,
                                 "labels": []} for i in range(versions - 1)]
        self.prompt_versions.append({"version": versions, "type": "text", "prompt": prompt, "config": config,
                                     "labels": ["production"]})

    def paths(self, method: str = "GET") -> List[str]:
        return [p for m, p, _ in self.requests if m == method]

    # --- urllib.request.urlopen -----------------------------------------------------------
    def urlopen(self, req: Any, timeout: Optional[float] = None) -> Response:
        if isinstance(req, str):
            url, method, headers, data = req, "GET", {}, None
        else:
            url, method, data = req.full_url, req.get_method(), req.data
            headers = {k.lower(): v for k, v in req.header_items()}
        self.urls.append(url)
        u = urllib.parse.urlsplit(url)  # as urllib does, this raises ValueError on a malformed host
        if u.path in self.raise_on:
            raise self.raise_on[u.path]
        params = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        try:
            if u.netloc == LF_HOST:
                self.requests.append((method, u.path, params))
                return self._json(self._langfuse(method, u.path, params, headers, data))
            if u.netloc == "localhost:8125" and u.path == "/":
                return Response(self._clickhouse(params, headers).encode())
            port = u.port or 80
            if port in self.down_ports or (port == 8125 and self.ch_down):
                raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
            return Response(b"Ok.\n")
        except Reject as e:
            raise urllib.error.HTTPError(url, e.status, str(e), {}, io.BytesIO(json.dumps({"message": str(e)}).encode()))

    @staticmethod
    def _json(body: Any) -> Response:
        return Response(json.dumps(body).encode())

    # --- Langfuse -------------------------------------------------------------------------
    def _langfuse(self, method: str, path: str, params: dict, headers: dict, data: Optional[bytes]) -> Any:
        if headers.get("authorization") != self.auth:
            raise Reject(401, "Invalid credentials")
        if path in self.fail:
            raise Reject(self.fail[path], "staged failure")
        if path == "/api/public/projects":
            return {"data": self.projects}
        if path == "/api/public/v2/prompts" and method == "POST":
            return self._create_prompt(json.loads(data or b"{}"))
        if path.startswith("/api/public/v2/prompts/"):
            allowed(params, {"label", "version"})
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            label = params.get("label", "production")
            served = next((p for p in reversed(self.prompt_versions) if label in p["labels"]), None)
            if served is None or name != "agentic-rag-generation":
                raise Reject(404, f"Prompt not found: '{name}' with label '{label}'")
            return dict(served, name=name)
        if path == "/api/public/v2/prompts":
            allowed(params, {"name", "label", "tag", "page", "limit", "fromUpdatedAt", "toUpdatedAt"})
            if not self.prompt_versions:
                return {"data": [], "meta": {"page": 1, "totalPages": 0}}
            return {"data": [{"name": params.get("name"), "type": "text",
                              "versions": [p["version"] for p in self.prompt_versions],
                              "labels": sorted({lb for p in self.prompt_versions for lb in p["labels"]})}],
                    "meta": {"page": 1, "totalPages": 1}}
        if path == "/api/public/v2/observations":
            if not self.v2:
                raise Reject(404, "The observations v2 API is only available in a Langfuse v4 write mode")
            return self._observations_v2(params)
        if path == "/api/public/observations":
            return self._observations_v1(params)
        if path == "/api/public/v3/scores":
            return self._scores_v3(params)
        raise Reject(404, f"no route {path}")

    def _create_prompt(self, body: dict) -> dict:
        extra = set(body) - PROMPT_BODY_KEYS
        if extra or not {"name", "type", "prompt"} <= set(body):
            raise Reject(400, f"invalid prompt body keys {sorted(body)}")
        status = self.post_status.pop(0) if self.post_status else 200
        if status != 200:
            raise Reject(status, "staged write failure")
        self.posts.append(body)
        for p in self.prompt_versions:  # a label lives on one version at a time
            p["labels"] = [lb for lb in p["labels"] if lb not in body.get("labels", [])]
        version = {"version": len(self.prompt_versions) + 1, "type": body["type"], "prompt": body["prompt"],
                   "config": body.get("config") or {}, "labels": list(body.get("labels") or [])}
        self.prompt_versions.append(version)
        return dict(version, name=body["name"])

    def _observations_v2(self, params: dict) -> dict:
        allowed(params, V2_OBS_PARAMS)
        groups = field_groups(params.get("fields", "core"), set(OBS_GROUPS))
        keys = set().union(*(OBS_GROUPS[g] for g in groups)) | OBS_GROUPS["core"]
        limit = int(params.get("limit", 50))
        if not 1 <= limit <= 1000:
            raise Reject(400, "limit must be between 1 and 1000")
        if "filter" in params:
            json.loads(params["filter"])  # a malformed filter is a 400 on the real API
        if "traceId" in params:
            rows = self.traces.get(params["traceId"], [])
            return {"data": [project(r, keys) for r in rows], "meta": {}}
        filt = json.loads(params.get("filter", "[]"))
        if params.get("name") != "grade-relevance" or not any(f.get("column") == "traceName" for f in filt):
            raise Reject(400, "unexpected anchor query")
        if not (params.get("fromStartTime") and params.get("toStartTime")):
            raise Reject(400, "anchor query must be time-bounded")
        idx = int(params["cursor"][1:]) if params.get("cursor") else 0
        page = self.anchor_pages[idx] if idx < len(self.anchor_pages) else []
        meta: dict = {}
        if self.repeat_cursor:
            meta["cursor"] = "c1"
        elif idx + 1 < len(self.anchor_pages):
            meta["cursor"] = f"c{idx + 1}"
        return {"data": [project(r, keys) for r in page], "meta": meta}

    def _observations_v1(self, params: dict) -> dict:
        allowed(params, V1_OBS_PARAMS)
        limit, page = int(params.get("limit", 50)), int(params.get("page", 1))
        if not 1 <= limit <= 100:
            raise Reject(400, "limit must be between 1 and 100")
        if "traceId" in params:
            rows = self.traces.get(params["traceId"], [])
        else:
            rows = [r for p in self.anchor_pages for r in p if r["name"] == params.get("name")]
        total = max(1, -(-len(rows) // limit))
        return {"data": rows[(page - 1) * limit: page * limit],
                "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": total}}

    def _scores_v3(self, params: dict) -> dict:
        allowed(params, V3_SCORE_PARAMS)
        groups = field_groups(params.get("fields", ""), SCORE_GROUPS)
        if not 1 <= int(params.get("limit", 50)) <= 100:
            raise Reject(400, "limit must be between 1 and 100")
        rows = self.scores
        if "traceId" in params:
            wanted = set(params["traceId"].split(","))
            rows = [s for s in rows if (s["subject"].get("traceId") or s["subject"]["id"]) in wanted]
        if "name" in params:
            rows = [s for s in rows if s["name"] in params["name"].split(",")]
        keys = SCORE_CORE | ({"subject"} if "subject" in groups else set())
        return {"data": [project(s, keys) for s in rows], "meta": {}}

    # --- ClickHouse HTTP ------------------------------------------------------------------
    def _clickhouse(self, params: dict, headers: dict) -> str:
        if self.ch_down:
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        if (headers.get("x-clickhouse-user"), headers.get("x-clickhouse-key")) != ("agentic", "agentic123"):
            raise Reject(403, "Code: 516. Authentication failed")
        sql = params.get("query", "")
        if self.ch_count is None and "kb_chunks" in sql and "system." not in sql:
            raise Reject(404, "Code: 60. DB::Exception: Unknown table expression identifier 'agentic_rag.kb_chunks'")
        if "groupBitXor" in sql:
            return f"{self.ch_count}\t{self.ch_fingerprint}\n"
        if "count()" in sql:
            return f"{self.ch_count}\n"
        if "data_skipping_indices" in sql:
            return f"{self.ch_index}\n" if self.ch_index else ""
        raise Reject(400, f"unexpected query {sql}")

    # --- subprocess.run (docker exec ... psql) -------------------------------------------
    def run(self, cmd: list, **kwargs: Any) -> SimpleNamespace:
        self.commands.append(cmd)
        if cmd[:3] != ["docker", "exec", "langfuse-postgres"]:
            raise AssertionError(f"unexpected command {cmd}")
        if self.docker == "missing":
            raise FileNotFoundError(2, "No such file or directory: 'docker'")
        if self.docker == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        if self.docker == "denied":
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="permission denied while trying to connect to the Docker daemon socket")
        sql = cmd[-1]
        if "project_id = 'p1'" not in sql:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="".join(f"{r}|{s}\n" for r, s in self.rules.items()), stderr="")


def allowed(params: dict, names: set) -> None:
    extra = set(params) - names
    if extra:
        raise Reject(400, f"unknown query parameter(s): {', '.join(sorted(extra))}")


def field_groups(value: str, valid: set) -> set:
    groups = {g for g in value.split(",") if g}
    bad = groups - valid
    if bad:
        raise Reject(400, f"unknown field group(s): {', '.join(sorted(bad))}")
    return groups


def project(row: dict, keys: set) -> dict:
    return {k: v for k, v in row.items() if k in keys}
