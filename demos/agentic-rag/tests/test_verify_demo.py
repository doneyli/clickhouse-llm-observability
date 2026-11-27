"""verify_demo.py offline: the result helpers, the .env rule, the manifest parse, the
latest-run search (v2 and the self-hosted v1 fallback), the score logic, and full runs
against a fake Langfuse and ClickHouse. No network, no Langfuse, no Docker, no model calls."""
from __future__ import annotations

import os
import urllib.error
from datetime import datetime, timezone

import pytest

import verify_demo as v

NOW = "2099-01-01T00:00:00.000Z"  # always newer than the lookback window start


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


# --- Template helpers ---------------------------------------------------------------------

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
    with pytest.raises(v.ApiError):
        v.ch_query("http://ch.invalid", "u", "p", "SELECT 1")


# --- Demo-specific logic --------------------------------------------------------------------

def test_services_follow_the_env_ports_and_only_clickhouse_is_required():
    svc = v.services({"CLICKHOUSE_VECTORS_HTTP_PORT": "9125", "AGENTIC_RAG_PORT": "9006"})
    assert svc[0] == ("clickhouse-vectors", "http://localhost:9125/ping", True)
    assert svc[1][1] == "http://localhost:9006/health"
    assert [required for _, _, required in svc] == [True, False, False, False]


def test_newest_traces_dedupes_and_sorts_newest_first():
    rows = [{"traceId": "a", "startTime": "2026-11-01T10:00:00Z"},
            {"traceId": "b", "startTime": "2026-11-02T10:00:00.5Z"},
            {"traceId": "a", "startTime": "2026-11-03T10:00:00Z"},  # a's second grade, newest
            {"traceId": None, "startTime": "2026-11-04T10:00:00Z"}]
    assert [t for t, _ in v.newest_traces(rows)] == ["a", "b"]


def test_score_trace_reads_observation_and_trace_subjects():
    assert v.score_trace({"subject": {"kind": "observation", "id": "o1", "traceId": "t1"}}) == "t1"
    assert v.score_trace({"subject": {"kind": "trace", "id": "t2"}}) == "t2"
    assert v.score_trace({}) is None


def test_self_correcting_traces_need_a_zero_and_a_one():
    def s(trace, value, ts):
        return {"value": value, "timestamp": ts, "subject": {"kind": "observation", "traceId": trace}}

    scores = [s("loop-old", 0, "2026-11-01"), s("loop-old", 1, "2026-11-01"),
              s("single", 1, "2026-11-05"),
              s("gave-up", 0, "2026-11-06"), s("gave-up", 0, "2026-11-06"),
              s("loop-new", 0.0, "2026-11-07"), s("loop-new", 1.0, "2026-11-07")]
    assert v.self_correcting_traces(scores) == ["loop-new", "loop-old"]


def test_parse_rule_rows():
    assert v.parse_rule_rows("arag-faithfulness|ACTIVE\narag-context-relevance|BLOCKED\n\n") == {
        "arag-faithfulness": "ACTIVE", "arag-context-relevance": "BLOCKED"}


def test_act_links_cover_the_acts_in_order():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1",
                                              "self_correcting_trace_id": "t0"})
    assert [label.split(" ·")[0] for label, _ in links] == ["Act 2", "Act 3", "Act 3", "Act 3", "Act 3b", "Act 4"]
    assert links[0][1] == "https://lf.example/project/p1/traces/t0"
    assert links[4][1].endswith("/prompts/agentic-rag-generation")


# --- Fake Langfuse ------------------------------------------------------------------------

GOOD_TRACE = [{"name": n} for n in ["LangGraph", *v.EXPECTED_OBSERVATIONS, "ChatAnthropic"]]


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows. `v2` False plays a 3.x server."""

    project = "LLM Observability Demo"
    v2 = True
    anchors = [{"traceId": "t1", "startTime": NOW}]
    traces = {"t1": GOOD_TRACE}
    scores = v.EXPECTED_SCORES + v.JUDGE_SCORES
    rr_scores = [{"value": 0, "timestamp": NOW, "subject": {"traceId": "t1"}},
                 {"value": 1, "timestamp": NOW, "subject": {"traceId": "t1"}}]
    prompt = {"version": 2, "prompt": "CONTEXT:\n{{context}}\n\nQUESTION: {{question}}"}
    calls: list = []

    def __init__(self, base, pk, sk):
        self.base = base

    def get(self, path, **params):
        type(self).calls.append(path)
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path == "/api/public/v2/prompts/agentic-rag-generation":
            assert params["label"] == "production"
            return self.prompt
        if path == "/api/public/v2/prompts":
            return {"data": [{"name": params["name"], "versions": [1, 2]}]}
        if path == "/api/public/v2/observations":
            if not self.v2:
                raise v.ApiError(404, "The observations v2 API is only available in a Langfuse v4 write mode")
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            assert '"traceName"' in params["filter"] and params["name"] == v.ANCHOR_OBSERVATION
            return {"data": self.anchors}
        if path == "/api/public/observations":  # v1 fallback, page-based
            assert not self.v2
            rows = self.traces[params["traceId"]] if "traceId" in params else self.anchors
            return {"data": rows, "meta": {"page": params["page"], "totalPages": 1}}
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        if path == "/api/public/v2/observations":
            return self.traces[params["traceId"]]
        if path == "/api/public/v3/scores":
            if params.get("name") == "retrieval_relevance":
                assert params["fields"] == "subject"
                return self.rr_scores
            return [{"name": n} for n in self.scores]
        raise AssertionError(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(v, "services", lambda env: [])
    monkeypatch.setattr(v, "ch_query", lambda base, u, p, sql: "28" if "count()" in sql
                        else "vector_similarity('hnsw', 'cosineDistance', 384)")
    monkeypatch.setattr(v, "judge_rule_status", lambda pid, c: {r: "ACTIVE" for r in v.JUDGE_RULES})
    monkeypatch.setattr(FakeLangfuse, "calls", [])
    return FakeLangfuse


def test_ready_on_a_v4_server(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out and "WARN" not in out
    assert "(Observations v2)" in out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert not any(c.startswith("/api/public/traces") for c in fake.calls)  # no legacy trace reads


def test_ready_on_a_self_hosted_3x_server_via_the_v1_fallback(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v2", False)
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "(Observations v1)" in out and "NOT READY" not in out


def test_a_query_router_trace_is_not_this_demos_run(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v2", False)  # v1 cannot filter on the trace name
    monkeypatch.setattr(fake, "anchors", [{"traceId": "router", "startTime": "2098-12-31T23:00:00Z"},
                                          {"traceId": "t1", "startTime": "2098-12-31T22:00:00Z"}])
    monkeypatch.setattr(fake, "traces", {"router": [{"name": v.ROUTER_HANDLER}, *GOOD_TRACE], "t1": GOOD_TRACE})
    run = v.find_latest_run(v.Observations(fake("b", "pk", "sk")), datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert run["trace_id"] == "t1"


def test_only_direct_route_runs_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "anchors", [])  # main.py's last question routes direct: no grade step
    assert v.main([]) == 1
    assert "FAIL  a scored 'agentic-rag' run" in capsys.readouterr().out


def test_missing_in_graph_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", ["retrieval_relevance"])
    assert v.main([]) == 1
    assert "missing groundedness" in capsys.readouterr().out


def test_missing_graph_node_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "traces", {"t1": [{"name": "route"}, {"name": "grade-relevance"}]})
    assert v.main([]) == 1
    assert "missing retrieve, generate, reflect-groundedness" in capsys.readouterr().out


def test_missing_judge_scores_and_loop_only_warn(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", list(v.EXPECTED_SCORES))
    monkeypatch.setattr(fake, "rr_scores", [{"value": 1, "timestamp": NOW, "subject": {"traceId": "t1"}}])
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "2 warnings" in out


def test_blocked_judge_rule_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(v, "judge_rule_status", lambda pid, c: {"arag-faithfulness": "BLOCKED"})
    assert v.main([]) == 1
    assert "arag-faithfulness BLOCKED" in capsys.readouterr().out


def test_postgres_out_of_reach_only_warns(fake, monkeypatch, capsys):
    def no_docker(pid, c):
        raise RuntimeError("docker not usable")

    monkeypatch.setattr(v, "judge_rule_status", no_docker)
    assert v.main([]) == 0
    assert "WARN  judge rules not readable" in capsys.readouterr().out


def test_empty_vector_table_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(v, "ch_query", lambda base, u, p, sql: "0" if "count()" in sql else "")
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "holds 0 chunk(s)" in out and "vec_idx is a vector_similarity index (missing)" in out


def test_prompt_without_its_variables_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "prompt", {"version": 3, "prompt": "Answer the question."})
    assert v.main([]) == 1


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out
