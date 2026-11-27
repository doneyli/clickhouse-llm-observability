"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
finding the latest run on Observations v2 and on the v1 fallback, and full runs against a
fake Langfuse. No network, no Langfuse, no model calls."""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

import verify_demo as v

NOW = "2099-01-01T00:00:00.000Z"  # always newer than the lookback window start (unit tests only)


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


def test_check_records_pass_fail_and_warn(capsys):
    assert v.check(True, "a") is True
    assert v.check(False, "b") is False
    assert v.check(False, "c", required=False, hint="do x") is False
    assert [s for s, _, _ in v.RESULTS] == ["PASS", "FAIL", "WARN"]
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["PASS", "a"] and out[1].split() == ["FAIL", "b"]
    assert out[2].startswith("  WARN  c") and out[2].endswith("->  do x")


def test_summary_ready_when_only_optional_checks_fail():
    v.check(True, "a")
    v.check(False, "b", required=False)
    assert v.summary() == ("READY (1/2 checks passed, 1 warning)", 0)


def test_summary_not_ready_when_a_required_check_fails():
    v.check(True, "a")
    v.check(False, "b")
    v.check(False, "c", required=False)
    v.check(False, "d", required=False)
    assert v.summary() == ("NOT READY (1/4 checks passed, 1 required failed, 2 warnings)", 1)


def test_parse_env_handles_comments_quotes_and_export():
    text = '# comment\n\nexport A=1\nB="two words"  \nC=three # trailing\nD=\'x # y\'\nE=\nnot a line\n'
    assert v.parse_env(text) == {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""}


def test_env_file_overrides_shell_langfuse_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://shell.example")  # not in the file: must be dropped
    monkeypatch.setenv("UNRELATED", "kept")
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=https://file.example\n")
    assert v.load_env(env) is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-file"
    assert os.environ["LANGFUSE_BASE_URL"] == "https://file.example"
    assert "LANGFUSE_HOST" not in os.environ
    assert os.environ["UNRELATED"] == "kept"


def test_missing_env_file_still_drops_shell_langfuse_keys(tmp_path):
    environ = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "OTHER": "kept"}
    assert v.load_env(tmp_path / ".env", environ) is False
    assert environ == {"OTHER": "kept"}


def test_env_file_follows_the_profile():
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    assert v.env_file("standalone") == v.DEMO_DIR / ".env"
    assert v.env_file(None) == v.DEMO_DIR / ".env"


def test_manifest_value_reads_nested_and_top_level_keys():
    text = ('name: x  # c\nprofile: standalone\nlangfuse:\n  target: cloud\n'
            '  project: "My Project"   # the real name\nlength:\n  full_min: 15\n')
    assert v.manifest_value(text, "langfuse", "project") == "My Project"
    assert v.manifest_value(text, "", "profile") == "standalone"
    assert v.manifest_value(text, "length", "project") is None
    assert v.manifest_value(text, "", "project") is None


def test_own_manifest_names_a_project_and_profile():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    project, profile = v.manifest_value(text, "langfuse", "project"), v.manifest_value(text, "", "profile")
    if "{{" in (project or "") + (profile or ""):
        pytest.skip("unfilled template")
    assert project
    assert profile in {"container", "standalone", "workshop"}


def test_parse_time_accepts_api_timestamps():
    assert v.parse_time("2026-11-26T10:00:00.123Z").microsecond == 123000
    assert v.parse_time("2026-11-26T10:00:00Z").tzinfo is not None
    assert v.parse_time("2026-11-26T10:00:00.123456789+00:00").microsecond == 123456


def test_network_failure_is_an_api_error_not_a_traceback(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(v.urllib.request, "urlopen", refuse)
    with pytest.raises(v.ApiError) as e:
        v.Langfuse("http://langfuse.invalid", "pk", "sk").get("/api/public/projects")
    assert e.value.status is None


def test_own_manifest_is_a_container_demo_reading_the_root_env():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "", "profile") == "container"
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    assert v.manifest_value(text, "commands", "verify").startswith("python demos/vector-rag/scripts/verify_demo.py")


def test_constants_match_the_app_and_its_seeders():
    """The names verify_demo expects are the ones the code writes and the seeders create."""
    root = v.REPO_ROOT
    assert f'trace_name="{v.TRACE_NAME}"' in (v.DEMO_DIR / "langfuse_config.py").read_text()
    assert f'"{v.PROMPT_NAME}"' in (v.DEMO_DIR / "rag_pipeline.py").read_text()
    assert f'"{v.PROMPT_NAME}"' in (root / "scripts" / "seed-app-prompts.py").read_text()
    seeded = [l for l in (root / "scripts" / "seed-code-evaluators.sh").read_text().splitlines()
              if l.startswith("seed_evaluator")]
    assert any('"response-structure-check"' in l for l in seeded)
    assert any('"credential-leak-guard"' in l for l in seeded)
    ts = (root / "evaluators" / "response-structure-check.ts").read_text() \
        + (root / "evaluators" / "credential-leak-guard.ts").read_text()
    for name in v.EXPECTED_SCORES:
        assert f'name: "{name}"' in ts, name


def test_trace_name_filter_carries_the_trace_name_and_both_time_bounds():
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)
    until = datetime(2026, 11, 2, tzinfo=timezone.utc)
    f = json.loads(v.trace_name_filter(since, until, {"column": "type", "value": "GENERATION"}))
    assert f[0] == {"type": "string", "column": "traceName", "operator": "=", "value": "vector-rag"}
    # a startTime condition replaces BOTH query bounds, so the filter must carry both
    assert f[1] == {"type": "datetime", "column": "startTime", "operator": ">=", "value": "2026-11-01T00:00:00Z"}
    assert f[2] == {"type": "datetime", "column": "startTime", "operator": "<=", "value": "2026-11-02T00:00:00Z"}
    assert f[3]["value"] == "GENERATION"


def test_linked_generations_keeps_only_generations_stamped_with_the_prompt():
    obs = [{"type": "GENERATION", "promptName": "vector-rag-generation"},
           {"type": "GENERATION", "promptName": None},                     # ran on the inline fallback
           {"type": "GENERATION", "promptName": "text-to-sql-response"},  # another demo, same project
           {"type": "SPAN", "promptName": "vector-rag-generation"}]
    assert v.linked_generations(obs) == obs[:1]


def test_newest_ignores_api_order():
    obs = [{"id": "a", "startTime": "2026-11-26T10:00:00Z"},
           {"id": "b", "startTime": "2026-11-26T12:00:00.5Z"},
           {"id": "c"}]
    assert v.newest(obs)["id"] == "b"
    assert v.newest([{"id": "c"}]) is None



# --- Full runs against an HTTP-level fake Langfuse ----------------------------------------
# The fake sits behind the real Langfuse client (a fake urlopen), so URL and query-string
# construction, JSON parsing and cursor / page traversal are the real code. It rejects
# query parameters the endpoint does not take and projects v2 observations by field group.

SEED = v.seeded_definitions()[("vector-rag-generation", "production")]
V2_GROUPS = {"core": {"id", "traceId", "startTime", "endTime", "projectId", "parentObservationId", "type"},
             "basic": {"name", "level", "statusMessage", "version", "environment", "isRootObservation"},
             "prompt": {"promptId", "promptName", "promptVersion"},
             "trace_context": {"traceName", "tags", "release"}}
V2_PARAMS = {"fields", "limit", "cursor", "filter", "fromStartTime", "toStartTime", "traceId", "type",
             "name", "parentObservationId", "level", "environment", "version", "userId", "expandMetadata"}
V1_PARAMS = {"page", "limit", "name", "userId", "type", "traceId", "level", "parentObservationId",
             "environment", "fromStartTime", "toStartTime", "version", "filter"}
SCORE_PARAMS = {"traceId", "limit", "cursor", "fields", "name", "fromTimestamp", "toTimestamp"}


def recent(minutes=10):
    return v.iso(datetime.now(timezone.utc) - timedelta(minutes=minutes))


def obs_rows(trace="t1", linked=True, start=None, trace_name="vector-rag"):
    start = start or recent()
    return [
        {"id": f"{trace}-root", "traceId": trace, "type": "CHAIN", "name": "RunnableSequence", "startTime": start,
         "parentObservationId": None, "isRootObservation": True, "promptName": None, "traceName": trace_name},
        {"id": f"{trace}-gen", "traceId": trace, "type": "GENERATION", "name": "ChatAnthropic", "startTime": start,
         "parentObservationId": f"{trace}-root", "isRootObservation": False, "traceName": trace_name,
         "promptName": "vector-rag-generation" if linked else None, "promptVersion": 3 if linked else None},
    ]


def matches(o, cond):
    """One structured filter condition, as the server evaluates it."""
    value = o.get(cond["column"])
    if cond["type"] == "datetime":
        if value is None:
            return False
        a, b = v.parse_time(value), v.parse_time(cond["value"])
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[cond["operator"]]
    assert cond["operator"] == "=", cond
    return value == cond["value"]


def in_window(o, q, conditions, whole_filter_replaces):
    """The time bounds the server applies. v2 (published contract): a structured startTime
    condition replaces BOTH fromStartTime and toStartTime; other fields' query bounds still
    apply. Older v1: any filter replaces every query-parameter filter."""
    if conditions and (whole_filter_replaces or any(c["column"] == "startTime" for c in conditions)):
        return True  # the query bounds are replaced; the filter's own conditions decide
    t = v.parse_time(o["startTime"])
    return v.parse_time(q["fromStartTime"]) <= t <= v.parse_time(q["toStartTime"])


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeServer:
    def __init__(self):
        self.projects = [{"id": "p1", "name": "LLM Observability Demo"}]
        self.prompt = dict(SEED, version=3, labels=["production", "latest"], name="vector-rag-generation")
        self.observations = obs_rows()
        self.scores = list(v.EXPECTED_SCORES)
        self.v2 = True           # False: a self-hosted v3 server, which 404s Observations v2
        self.fail = {}           # path -> HTTP status
        self.empty_root_pages = 0   # empty v2 pages, each with a cursor, before the roots
        self.empty_trace_pages = 1  # the same for the per-trace read
        self.repeat_cursor = False  # True: every empty page returns the same cursor
        self.calls = []

    def __call__(self, req, timeout=None):
        if isinstance(req, str):  # the optional local API check opens a plain URL
            assert urllib.parse.urlsplit(req).port == 1, req
            raise urllib.error.URLError("connection refused")
        url = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(url.query))
        path = url.path
        assert req.get_header("Authorization") == "Basic " + base64.b64encode(b"pk:sk").decode()
        self.calls.append((path, q))
        if path in self.fail:
            raise urllib.error.HTTPError(req.full_url, self.fail[path], "err", {}, io.BytesIO(b"{}"))
        return _Resp(json.dumps(self.route(path, q)).encode())

    def _404(self, path):
        raise urllib.error.HTTPError(path, 404, "Not Found", {}, io.BytesIO(b'{"message":"not found"}'))

    def route(self, path, q):
        if path == "/api/public/projects":
            assert not q
            return {"data": self.projects}
        if path == "/api/public/v2/prompts/vector-rag-generation":
            assert set(q) == {"label"}
            if self.prompt is None or q["label"] not in self.prompt["labels"]:
                self._404(path)
            return self.prompt
        if path == "/api/public/v2/observations":
            if not self.v2:
                self._404(path)
            assert set(q) <= V2_PARAMS, set(q) - V2_PARAMS
            assert q.get("fromStartTime") and q.get("toStartTime")
            assert "traceName" not in q  # not a query parameter: must go in `filter`
            conditions = json.loads(q["filter"]) if "filter" in q else []
            rows = [o for o in self.observations
                    if ("traceId" not in q or o["traceId"] == q["traceId"])
                    and in_window(o, q, conditions, whole_filter_replaces=False)
                    and all(matches(o, c) for c in conditions)]
            rows.sort(key=lambda o: v.parse_time(o["startTime"]), reverse=True)  # newest first
            keep = set().union(*(V2_GROUPS[g] for g in q.get("fields", "core,basic").split(",")))
            rows = [{k: o[k] for k in o if k in keep} for o in rows]
            # cursor paging, with empty pages before the data while a cursor is followed
            empty = self.empty_trace_pages if "traceId" in q else self.empty_root_pages
            n = int(q["cursor"][1:]) if q.get("cursor") else 0
            if n < empty:
                return {"data": [], "meta": {"cursor": "c1" if self.repeat_cursor else f"c{n + 1}"}}
            return {"data": rows[: int(q["limit"])], "meta": {}}
        if path == "/api/public/observations":
            assert not self.v2
            assert set(q) <= V1_PARAMS, set(q) - V1_PARAMS
            # v1 returns every field, and no traceName or root flag
            if "traceId" not in q:
                assert q["type"] == "GENERATION" and q["fromStartTime"]
            conditions = [c for c in json.loads(q.get("filter", "[]")) if c["column"] != "traceName"]
            rows = [o for o in self.observations
                    if ("traceId" not in q or o["traceId"] == q["traceId"])
                    and ("type" not in q or "filter" in q or o["type"] == q["type"])
                    and ("traceId" in q or in_window(o, q, conditions, whole_filter_replaces=True))
                    and all(matches(o, c) for c in conditions)]
            rows = [{k: val for k, val in o.items() if k not in ("isRootObservation", "traceName")} for o in rows]
            return {"data": rows, "meta": {"page": int(q["page"]), "totalPages": 1}}
        if path == "/api/public/v3/scores":
            assert set(q) <= SCORE_PARAMS, set(q) - SCORE_PARAMS
            return {"data": [{"name": n, "traceId": q["traceId"]} for n in self.scores], "meta": {}}
        raise AssertionError(f"unexpected request {path}")


@pytest.fixture
def server(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example/\n"
                   "VECTOR_RAG_PORT=1\n")  # nothing listens on port 1: the optional API check warns
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.delenv("DEMO_LANGFUSE_PROJECT", raising=False)
    fake = FakeServer()
    monkeypatch.setattr(v.urllib.request, "urlopen", fake)
    return fake


def run(capsys, *argv):
    code = v.main(list(argv))
    return code, capsys.readouterr().out


def test_ready_on_observations_v2(server, capsys):
    code, out = run(capsys, "--links")
    assert code == 0, out
    assert "READY (" in out and "NOT READY" not in out
    assert "matches the seeded definition" in out
    assert "linked to vector-rag-generation v3" in out
    assert "read with observations v2" in out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert "WARN  vector-rag API" in out  # optional: the script does not need the server
    assert not any("/traces" in p for p, _ in server.calls)  # no legacy trace endpoints


def test_ready_on_a_v3_server_through_the_v1_fallback(server, capsys):
    server.v2 = False
    code, out = run(capsys)
    assert code == 0, out
    assert "read with observations v1" in out
    assert any(p == "/api/public/observations" for p, _ in server.calls)
    assert not any("/traces" in p for p, _ in server.calls)


def test_hostile_shell_exports_are_ignored(server, monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-another-project")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-another-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://elsewhere.example")
    code, out = run(capsys)
    assert code == 0, out  # the fake asserts the .env keys were the ones sent


def test_missing_env_file_fails_before_any_request(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "absent.env")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    code, out = run(capsys)
    assert code == 1 and "NOT READY" in out
    assert server.calls == []


def test_partial_env_fails_before_any_request(server, tmp_path, monkeypatch, capsys):
    partial = tmp_path / "partial.env"
    partial.write_text("LANGFUSE_PUBLIC_KEY=pk\n")
    monkeypatch.setattr(v, "env_file", lambda profile: partial)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")  # must not fill the gap
    code, out = run(capsys)
    assert code == 1 and "FAIL  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set" in out
    assert server.calls == []


def test_non_container_manifest_fails(server, monkeypatch, capsys):
    monkeypatch.setattr(v, "manifest_value", lambda text, section, key: "standalone" if key == "profile" else None)
    code, out = run(capsys)
    assert code == 1 and "FAIL  demo.yaml declares profile container" in out
    assert server.calls == []


@pytest.mark.parametrize("projects", [[{"id": "p9", "name": "someone-elses-project"}], []])
def test_wrong_or_empty_project_is_not_ready(server, capsys, projects):
    server.projects = projects
    code, out = run(capsys)
    assert code == 1 and "FAIL  keys map to project" in out


def test_unauthorised_keys_are_not_ready(server, capsys):
    server.fail["/api/public/projects"] = 401
    code, out = run(capsys)
    assert code == 1 and "HTTP 401" in out


def test_unseeded_prompt_is_not_ready(server, capsys):
    server.prompt = None
    code, out = run(capsys)
    assert code == 1 and "FAIL  prompt vector-rag-generation with label production: HTTP 404" in out


def test_missing_label_is_not_ready(server, capsys):
    server.prompt["labels"] = ["latest"]
    code, out = run(capsys)
    assert code == 1 and "HTTP 404" in out


@pytest.mark.parametrize("change, part", [({"config": {"model": "claude-old", "temperature": 0.2}}, "config"),
                                          ({"prompt": "Answer in three bullets. {{context}} {{question}}"}, "text"),
                                          ({"type": "chat"}, "type")])
def test_drifted_prompt_is_not_ready(server, capsys, change, part):
    server.prompt.update(change)
    code, out = run(capsys)
    assert code == 1 and f"differs from the seeded definition: {part}" in out


def test_no_recent_run_is_not_ready(server, capsys):
    server.observations = []
    code, out = run(capsys)
    assert code == 1 and "FAIL  a 'vector-rag' run" in out


def test_run_on_the_inline_fallback_prompt_is_not_ready(server, capsys):
    server.observations = obs_rows(linked=False)
    code, out = run(capsys)
    assert code == 1 and "FAIL  latest run's generation is linked" in out


def test_v1_fallback_finds_no_run_when_nothing_is_linked(server, capsys):
    server.v2 = False
    server.observations = obs_rows(linked=False)
    code, out = run(capsys)
    assert code == 1 and "FAIL  a 'vector-rag' run" in out


def test_missing_scores_are_not_ready(server, capsys):
    server.scores = ["output-present"]
    code, out = run(capsys)
    assert code == 1 and "missing structure-clean, response-length, credential-leak" in out


def test_stale_run_only_warns(server, capsys):
    server.observations = obs_rows(start=recent(minutes=60 * 30))
    code, out = run(capsys)
    assert code == 0 and "WARN  latest run is" in out


def test_required_read_failure_reaches_the_verdict(server, capsys):
    server.fail["/api/public/v3/scores"] = 500
    code, out = run(capsys)
    assert code == 1 and "Langfuse read failed: HTTP 500" in out


def test_non_404_v2_error_is_not_swallowed_by_the_fallback(server, capsys):
    server.fail["/api/public/v2/observations"] = 500
    code, out = run(capsys)
    assert code == 1 and "Langfuse read failed: HTTP 500" in out
    assert not any(p == "/api/public/observations" for p, _ in server.calls)


def test_network_failure_reaches_the_verdict(server, monkeypatch, capsys):
    def refuse(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(v.urllib.request, "urlopen", refuse)
    code, out = run(capsys)
    assert code == 1 and "FAIL  keys authenticate against https://lf.example" in out


def test_unexpected_error_reaches_the_verdict(server, monkeypatch, capsys):
    monkeypatch.setattr(v, "latest_run", lambda *a: {}["boom"])
    code, out = run(capsys)
    assert code == 1 and "FAIL  verify_demo crashed: KeyError" in out and "NOT READY" in out


def test_broken_helper_fails_the_prompt_check(server, monkeypatch, capsys):
    monkeypatch.setattr(v, "seeded_definitions", lambda: (_ for _ in ()).throw(FileNotFoundError("gone")))
    code, out = run(capsys)
    assert code == 1 and "FAIL  read the seeded definitions" in out


# --- Root discovery and time bounds, through main() -----------------------------------------

def root_requests(server):
    return [q for path, q in server.calls
            if path == "/api/public/v2/observations" and "traceId" not in q]


def test_root_discovery_follows_the_cursor_through_empty_pages(server, capsys):
    server.empty_root_pages = 3  # three empty pages, each with a cursor, before the root
    code, out = run(capsys, "--links")
    assert code == 0, out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert [q.get("cursor") for q in root_requests(server)] == [None, "c1", "c2", "c3"]


def test_repeated_root_cursor_is_not_ready_not_a_loop(server, capsys):
    server.empty_root_pages, server.repeat_cursor = 5, True
    code, out = run(capsys)
    assert code == 1 and "returned the same cursor twice" in out and "NOT READY" in out
    assert len(root_requests(server)) == 2


def test_root_discovery_is_bounded(server, capsys):
    server.empty_root_pages = 1000  # a cursor forever, never a root
    code, out = run(capsys)
    assert code == 1 and f"FAIL  a '{v.TRACE_NAME}' run in the last" in out
    assert len(root_requests(server)) == 20  # Langfuse.cursor's max_pages


@pytest.mark.parametrize("api", ["v2", "v1"])
def test_a_trace_stamped_after_the_upper_bound_is_never_chosen(server, capsys, api):
    server.v2 = api == "v2"
    future = v.iso(datetime.now(timezone.utc) + timedelta(hours=2))  # beyond until = now + 5 min
    server.observations = obs_rows("t-future", start=future) + obs_rows("t1")
    code, out = run(capsys, "--links")
    assert code == 0, out
    assert "/traces/t1" in out and "t-future" not in out
    assert f"read with observations {api}" in out


def test_a_trace_from_another_app_is_never_chosen(server, capsys):
    server.observations = obs_rows("t-other", trace_name="text-to-sql") + obs_rows("t1", start=recent(30))
    code, out = run(capsys, "--links")
    assert code == 0, out
    assert "/traces/t1" in out and "t-other" not in out


def test_the_fake_applies_the_published_time_bound_precedence(server):
    """The fake is only a fair test if it models the contract: a startTime condition in
    `filter` replaces both query bounds, so a lower bound alone lets a future trace through."""
    lf = v.Langfuse("https://lf.example", "pk", "sk")
    now = datetime.now(timezone.utc)
    future = v.iso(now + timedelta(hours=2))
    server.observations = obs_rows("t-future", start=future)
    server.empty_root_pages = 0
    lower_only = json.dumps([{"type": "string", "column": "traceName", "operator": "=", "value": "vector-rag"},
                             {"type": "datetime", "column": "startTime", "operator": ">=",
                              "value": v.iso(now - timedelta(days=1))}])
    window = {"fromStartTime": v.iso(now - timedelta(days=1)), "toStartTime": v.iso(now)}
    got = lf.get("/api/public/v2/observations", fields="core", limit=10, filter=lower_only, **window)
    assert [o["traceId"] for o in got["data"]] == ["t-future", "t-future"]  # the upper bound was dropped
    both = v.trace_name_filter(now - timedelta(days=1), now)
    got = lf.get("/api/public/v2/observations", fields="core", limit=10, filter=both, **window)
    assert got["data"] == []
    got = lf.get("/api/public/v2/observations", fields="core", limit=10, **window)  # query bounds alone
    assert got["data"] == []


# --- Cursor traversal ---------------------------------------------------------------------

class Pages:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, path, **params):
        self.asked.append(params.get("cursor"))
        return self.pages[len(self.asked) - 1]


def test_cursor_follows_through_empty_pages():
    pages = Pages([{"data": [], "meta": {"cursor": "a"}}, {"data": [{"id": 1}], "meta": {"cursor": "b"}},
                   {"data": [], "meta": {"cursor": "c"}}, {"data": [{"id": 2}], "meta": {}}])
    assert v.Langfuse.cursor(pages, "/x", {}) == [{"id": 1}, {"id": 2}]
    assert pages.asked == [None, "a", "b", "c"]


def test_repeated_cursor_is_an_error_not_a_loop():
    pages = Pages([{"data": [], "meta": {"cursor": "a"}}] * 5)
    with pytest.raises(v.ApiError, match="same cursor twice"):
        v.Langfuse.cursor(pages, "/x", {})


def test_cursor_traversal_is_bounded():
    pages = Pages([{"data": [{"id": i}], "meta": {"cursor": f"c{i}"}} for i in range(50)])
    assert len(v.Langfuse.cursor(pages, "/x", {}, max_pages=3)) == 3


# --- Pure helpers -------------------------------------------------------------------------

def test_prompt_drift_compares_the_complete_definition():
    served = dict(SEED, labels=["production"])
    assert v.prompt_drift(served, SEED, "production") == []
    assert v.prompt_drift(dict(served, config=None), dict(SEED, config={}), "production") == []
    assert v.prompt_drift(dict(served, labels=[]), SEED, "production") == ["label production"]


def test_seeded_definition_is_the_helpers_vector_rag_prompt():
    assert SEED["type"] == "text"
    assert "{{context}}" in SEED["prompt"] and "{{question}}" in SEED["prompt"]
    assert set(SEED["config"]) == {"model", "temperature"}


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1"})
    assert [label.split(" ·")[0] for label, _ in links] == ["Act 1", "Act 2", "Act 3", "Act 4"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
    assert links[1][1].endswith("/prompts/vector-rag-generation")
