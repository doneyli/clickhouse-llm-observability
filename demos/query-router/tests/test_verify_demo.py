"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
the Observations v2 -> v1 fallback, and full runs against a fake Langfuse.
No network, no Langfuse, no model calls."""
from __future__ import annotations

import os
import urllib.error

import pytest

import verify_demo as v

NOW = "2099-01-01T00:00:00.000Z"  # newer than any window start; the fake ignores windows


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


# --- helpers ------------------------------------------------------------------------------

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


def test_container_demo_reads_the_root_env():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "", "profile") == "container"
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    assert v.manifest_value(text, "langfuse", "project") == "LLM Observability Demo"


def test_manifest_value_reads_nested_and_top_level_keys():
    text = ('name: x  # c\nprofile: standalone\nlangfuse:\n  target: cloud\n'
            '  project: "My Project"   # the real name\nlength:\n  full_min: 15\n')
    assert v.manifest_value(text, "langfuse", "project") == "My Project"
    assert v.manifest_value(text, "", "profile") == "standalone"
    assert v.manifest_value(text, "length", "project") is None


def test_services_follow_the_compose_port_variables():
    assert [u for _, u in v.services({})] == [
        "http://localhost:8008/health", "http://localhost:8002/health",
        "http://localhost:8003/health", "http://localhost:8006/health"]
    assert v.services({"QUERY_ROUTER_PORT": "9008"})[0][1] == "http://localhost:9008/health"


def test_constants_match_the_router_code():
    import router
    assert v.ROUTES == router.ROUTES
    assert v.PROMPT_NAME == router.ROUTER_PROMPT_NAME


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


# --- Observations v2 -> v1 fallback ---------------------------------------------------------

def _client(handler):
    lf = v.Langfuse("https://lf.example", "pk", "sk")
    lf.calls = []

    def get(path, **params):
        lf.calls.append((path, params))
        return handler(path, params)

    lf.get = get
    return lf


def test_observations_use_v2_when_the_server_has_it():
    lf = _client(lambda path, params: {"data": [{"id": "o1"}], "meta": {}})
    assert lf.observations(name="route-query") == [{"id": "o1"}]
    assert lf.observations_api == "v2"
    assert lf.calls[0][0] == "/api/public/v2/observations" and lf.calls[0][1]["fields"] == "core,basic"


def test_observations_fall_back_to_v1_on_a_v3_server():
    def handler(path, params):
        if path == "/api/public/v2/observations":
            raise v.ApiError(404, "only available in a Langfuse v4 write mode")
        return {"data": [{"id": "o1"}], "meta": {"totalPages": 1}}

    lf = _client(handler)
    assert lf.observations(name="route-query") == [{"id": "o1"}]
    assert lf.observations(name="route-query") == [{"id": "o1"}]
    assert lf.observations_api == "v1"
    paths = [p for p, _ in lf.calls]
    assert paths == ["/api/public/v2/observations", "/api/public/observations", "/api/public/observations"]
    assert "fields" not in lf.calls[1][1]


def test_observations_other_errors_propagate():
    def handler(path, params):
        raise v.ApiError(401, "unauthorized")

    with pytest.raises(v.ApiError):
        _client(handler).observations(name="x")


def test_pages_stop_at_the_last_page():
    lf = _client(lambda path, params: {"data": [{"page": params["page"]}], "meta": {"totalPages": 2}})
    assert lf.pages("/api/public/dataset-items", {}) == [{"page": 1}, {"page": 2}]


# --- full runs against a fake Langfuse ------------------------------------------------------

LIVE_DISPATCH = [  # a docs_complex run: router spans plus the agentic-rag subtree
    {"name": "route-and-dispatch", "parentObservationId": None},
    {"name": "route-query", "parentObservationId": "r"},
    {"name": "dispatch-docs_complex", "parentObservationId": "r"},
    {"name": "agentic-rag-handler", "parentObservationId": "d"},
    {"name": "retrieve", "parentObservationId": "a"},
    {"name": "generate", "parentObservationId": "a"},
]
LIVE_FALLBACK = [
    {"name": "route-and-dispatch", "parentObservationId": None},
    {"name": "route-query", "parentObservationId": "r"},
    {"name": "fallback-handler", "parentObservationId": "r"},
    {"name": "escalate-to-human", "parentObservationId": "r"},
]


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows."""

    project = "LLM Observability Demo"
    scores = ["router_confidence"]
    history = v.HISTORY_MIN_PER_WEEK
    dataset_items = v.MIN_DATASET_ITEMS
    traces = {"t-fallback": LIVE_FALLBACK, "t-dispatch": LIVE_DISPATCH}
    roots = [{"traceId": "t-fallback", "startTime": NOW},
             {"traceId": "t-dispatch", "startTime": "2098-12-31T23:00:00.000Z"}]

    def __init__(self, base, pk, sk):
        self.base = base
        self.observations_api = "v2"

    def get(self, path, **params):
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            return {"version": 2}
        if path.startswith("/api/public/v2/datasets/"):
            return {"id": "ds1"}
        raise AssertionError(path)

    def pages(self, path, params, cap=1000):
        assert path == "/api/public/dataset-items"
        return [{"id": f"i{n}", "status": "ACTIVE"} for n in range(self.dataset_items)]

    def cursor(self, path, params, cap=1000, max_pages=20):
        assert path == "/api/public/v3/scores"
        if "traceId" in params:
            return [{"name": n} for n in self.scores]
        return [{"name": params["name"]}]

    def observations(self, cap=100, **filters):
        assert filters["fromStartTime"] and filters["toStartTime"]  # every read is time-bounded
        if "traceId" in filters:
            return self.traces[filters["traceId"]]
        if filters["name"] == "route-query":
            return [{"id": f"h{n}"} for n in range(min(cap, self.history))]
        if filters["name"] == "route-and-dispatch":
            return self.roots
        raise AssertionError(filters)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(v, "_health", lambda url: (200, {
        "status": "ok", "langfuse": True, "handlers": {r: "ok" for r in v.ROUTES}}))
    return FakeLangfuse


def test_ready_against_a_fake_langfuse(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out
    assert "https://lf.example/project/p1/traces/t-dispatch" in out  # Act 1 opens the dispatched run
    assert "https://lf.example/project/p1/datasets/ds1" in out


def test_missing_routes_only_warn(fake, capsys):
    v.main([])
    warned = [w for s, _, w in v.RESULTS if s == "WARN"]
    assert any("analytics_sql" in w for w in warned) and any("docs_simple" in w for w in warned)


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_runtime_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", [])
    assert v.main([]) == 1
    assert "missing router_confidence" in capsys.readouterr().out


def test_aged_out_history_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "history", 40)
    assert v.main([]) == 1
    assert "router history: 40" in capsys.readouterr().out


def test_short_dataset_is_not_ready(fake, monkeypatch):
    monkeypatch.setattr(fake, "dataset_items", 12)
    assert v.main([]) == 1


def test_only_fallbacks_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "roots", [{"traceId": "t-fallback", "startTime": NOW}])
    assert v.main([]) == 1
    assert "carries the handler subtree: 0" in capsys.readouterr().out


def test_router_that_cannot_reach_a_handler_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(v, "_health", lambda url: (200, {
        "status": "ok", "langfuse": True, "handlers": {"analytics_sql": "ok", "docs_simple": "unreachable: ConnectError",
                                                       "docs_complex": "ok"}}))
    assert v.main([]) == 1
    assert "not ok: docs_simple=unreachable" in capsys.readouterr().out


def test_service_down_is_not_ready(fake, monkeypatch):
    monkeypatch.setattr(v, "_health", lambda url: (None, "ConnectionRefusedError"))
    assert v.main([]) == 1


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1",
                                               "dispatched_trace_id": "t2", "dataset_id": "d1"})
    assert [label.split(" ·")[0] for label, _ in links] == ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
