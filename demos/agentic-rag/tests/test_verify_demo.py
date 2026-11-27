"""verify_demo.py offline: the result helpers, the .env rule, the manifest parse, the score
logic, and full runs against fake_langfuse.FakeServer, which stands in for Langfuse (v4 and
the self-hosted 3.x server), clickhouse-vectors, the services and the Postgres read at the
urlopen / subprocess layer. No network, no Langfuse, no Docker, no model calls."""
from __future__ import annotations

import http.client
import os
import urllib.error

import pytest

import verify_demo as v

from fake_langfuse import LF_BASE, NOW, FakeServer, good_trace, obs, score


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

def test_services_follow_the_env_ports_and_only_clickhouse_is_on_the_short_path():
    svc = v.services({"CLICKHOUSE_VECTORS_HTTP_PORT": "9125", "AGENTIC_RAG_PORT": "9006"})
    assert svc[0] == ("clickhouse-vectors", "http://localhost:9125/ping", True)
    assert svc[1][1] == "http://localhost:9006/health"
    assert [short for _, _, short in svc] == [True, False, False, False]


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


# --- Full runs against the fake server ----------------------------------------------------

import seed_all  # noqa: E402  (the prompt text verify must accept is the one seed writes)

PROMPT = seed_all.prompt_definitions()
HOSTILE_SHELL = {"LANGFUSE_PUBLIC_KEY": "pk-other-project", "LANGFUSE_SECRET_KEY": "sk-other-project",
                 "LANGFUSE_HOST": "https://hostile.example", "LANGFUSE_BASE_URL": "https://hostile.example"}
ENV_TEXT = f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={LF_BASE}\nDEPLOY_MODE=self-hosted\n"


@pytest.fixture
def server(tmp_path, monkeypatch):
    srv = FakeServer()
    srv.seed_prompt(PROMPT.V2, PROMPT.CONFIG)
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT)
    for k, val in HOSTILE_SHELL.items():  # a shell exporting another project's keys
        monkeypatch.setenv(k, val)
    for k in ("DEPLOY_MODE", "CLICKHOUSE_VECTORS_HTTP_PORT", "AGENTIC_RAG_PORT", "MCP_RAG_RETRIEVER_PORT",
              "CH_VECTORS_USER", "CH_VECTORS_PASSWORD", "DEMO_LANGFUSE_PROJECT", "LANGFUSE_POSTGRES_CONTAINER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v.urllib.request, "urlopen", srv.urlopen)
    monkeypatch.setattr(v.subprocess, "run", srv.run)
    srv.env = env
    return srv


def verdict(capsys, argv=()):
    code = v.main(list(argv))
    out = capsys.readouterr().out
    assert out.rstrip().splitlines()[-1].startswith("READY (" if code == 0 else "NOT READY (")
    return code, out


def test_ready_on_a_v4_server(server, capsys):
    code, out = verdict(capsys, ["--links"])
    assert code == 0 and "WARN" not in out, out
    assert "(Observations v2)" in out and "incl. route, retrieve" in out
    assert f"{LF_BASE}/project/p1/traces/t1" in out
    assert not any(p.startswith("/api/public/traces") for p in server.paths())  # no legacy trace reads
    v2 = [q for m, p, q in server.requests if p == "/api/public/v2/observations"]
    assert v2 and all("basic" in q["fields"].split(",") for q in v2)  # 136-1: names need `basic`


def test_names_are_missing_when_only_core_is_requested(server, monkeypatch, capsys):
    """The fixture honours projection: a core-only read has no names, so the run is unusable."""
    monkeypatch.setattr(v.Observations, "FIELDS", "core")
    code, out = verdict(capsys)
    assert code == 1
    assert "missing route, retrieve, grade-relevance, generate, reflect-groundedness" in out


def test_ready_on_the_self_hosted_3x_server_via_the_v1_fallback(server, capsys):
    server.v2 = False
    code, out = verdict(capsys)
    assert code == 0 and "(Observations v1)" in out, out
    assert "/api/public/observations" in server.paths()


def test_hostile_shell_keys_are_not_used(server, capsys):
    verdict(capsys)
    assert server.requests  # every request authenticated with the file's keys (the fake rejects others)


@pytest.mark.parametrize("text", ["", "LANGFUSE_PUBLIC_KEY=pk\n", f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_BASE_URL={LF_BASE}\n"])
def test_missing_or_partial_env_fails_before_any_request(server, capsys, text):
    server.env.write_text(text)
    code, out = verdict(capsys)
    assert code == 1 and server.requests == [] and server.commands == []


def test_missing_env_file_fails_before_any_request(server, capsys):
    server.env.unlink()
    code, out = verdict(capsys)
    assert code == 1 and "FAIL  env loaded" in out and server.requests == []


# --- 136-4: every failure reaches a counted verdict, through main() -----------------------

@pytest.mark.parametrize("line, reason", [
    ("LANGFUSE_BASE_URL=http://[::gg]", "LANGFUSE_BASE_URL 'http://[::gg]'"),  # message differs by Python
    ("LANGFUSE_BASE_URL=http://localhost:30o1", "Port could not be cast"),
    ("LANGFUSE_BASE_URL=localhost:3001", "needs an http:// or https:// scheme"),
    ("CLICKHOUSE_VECTORS_HTTP_PORT=81x5", "CLICKHOUSE_VECTORS_HTTP_PORT='81x5' is not a port"),
])
def test_a_malformed_env_value_fails_before_any_request(server, capsys, line, reason):
    """The .env as written, through main(): a FAIL row, NOT READY with counts, exit 1, no request."""
    key = line.split("=", 1)[0]
    kept = [ln for ln in ENV_TEXT.splitlines() if not ln.startswith(key + "=")]
    server.env.write_text("\n".join(kept + [line]) + "\n")
    code, out = verdict(capsys)
    assert code == 1 and server.urls == [] and server.commands == []
    assert "FAIL  LANGFUSE_BASE_URL and the service ports are well-formed" in out and reason in out
    assert "PASS  env loaded" in out  # the checks before it are kept and counted
    assert out.rstrip().splitlines()[-1] == "NOT READY (2/3 checks passed, 1 required failed)"


@pytest.mark.parametrize("damage", ["unreadable", "not-utf8"])
def test_an_unreadable_env_file_fails_with_a_verdict(server, capsys, damage):
    if damage == "unreadable":
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root reads a mode-000 file")
        server.env.chmod(0)
    else:
        server.env.write_bytes(b"LANGFUSE_PUBLIC_KEY=\xff\xfe\n")
    try:
        code, out = verdict(capsys)
    finally:
        server.env.chmod(0o600)
    assert code == 1 and server.urls == [] and "not readable" in out
    assert not any(k.startswith("LANGFUSE_") for k in os.environ)  # the hostile shell keys are still dropped


def test_a_malformed_url_raises_apierror_from_the_real_client_and_ch_reader():
    with pytest.raises(v.ApiError, match="LANGFUSE_BASE_URL"):
        v.Langfuse("http://[::gg]", "pk", "sk")
    with pytest.raises(v.ApiError):  # real urllib.request.Request construction, no fake
        v.ch_query("http://[::gg]", "u", "p", "SELECT 1")
    assert v.base_url_error("https://cloud.langfuse.com") is None
    assert v.base_url_error("http://[::1]:3001") is None


def test_a_transport_fault_mid_read_is_a_failed_check(server, capsys):
    server.raise_on["/api/public/projects"] = http.client.IncompleteRead(b"{")
    code, out = verdict(capsys)
    assert code == 1 and "FAIL  keys authenticate" in out and "IncompleteRead" in out


def test_an_unexpected_error_keeps_earlier_results_and_still_gives_a_verdict(server, capsys):
    server.raise_on["/api/public/v2/prompts/agentic-rag-generation"] = LookupError("boom")
    code, out = verdict(capsys)
    assert code == 1 and "FAIL  verification stopped on an unexpected error: LookupError: boom" in out
    passed = sum(1 for s, _, _ in v.RESULTS if s == "PASS")
    assert passed >= 8 and f"{passed}/{len(v.RESULTS)} checks passed" in out  # earlier rows still count


def test_ctrl_c_is_not_swallowed(server):
    server.raise_on["/api/public/projects"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        v.main([])


def test_help_and_usage_errors_behave_as_argparse(capsys):
    with pytest.raises(SystemExit) as e:
        v.main(["--help"])
    assert e.value.code == 0 and "--short" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        v.main(["--nope"])
    assert e.value.code == 2 and v.RESULTS == []


@pytest.mark.parametrize("projects", [[], [{"id": "p9", "name": "someone-elses-project"}]])
def test_wrong_or_empty_project_is_not_ready(server, capsys, projects):
    server.projects = projects
    code, out = verdict(capsys)
    assert code == 1 and "keys map to project" in out


def test_bad_keys_are_not_ready(server, capsys):
    server.auth = "Basic nope"
    code, out = verdict(capsys)
    assert code == 1 and "HTTP 401" in out


# 136-3: anchor discovery follows the cursor

def test_an_empty_first_page_with_a_cursor_is_followed(server, capsys):
    server.anchor_pages = [[], [obs("t1-o3", "t1", "grade-relevance")]]
    code, out = verdict(capsys)
    assert code == 0, out
    cursors = [q.get("cursor") for m, p, q in server.requests
               if p == "/api/public/v2/observations" and "traceId" not in q]
    assert cursors == [None, "c1"]


def test_a_first_page_of_router_traces_does_not_hide_a_later_run(server, capsys):
    server.anchor_pages = [[obs("r-o1", "router", "grade-relevance", "2098-12-31T23:00:00Z")],
                           [obs("t1-o3", "t1", "grade-relevance", "2098-12-31T22:00:00Z")]]
    server.traces["router"] = [obs("r-o0", "router", v.ROUTER_HANDLER), *good_trace("router")]
    code, out = verdict(capsys, ["--links"])
    assert code == 0 and "/traces/t1" in out and "/traces/router" not in out, out


def test_the_v1_fallback_skips_query_router_traces(server, capsys):
    server.v2 = False  # v1 cannot filter on the trace name
    server.anchor_pages = [[obs("r-o1", "router", "grade-relevance", "2098-12-31T23:00:00Z"),
                            obs("t1-o3", "t1", "grade-relevance", "2098-12-31T22:00:00Z")]]
    server.traces["router"] = [obs("r-o0", "router", v.ROUTER_HANDLER), *good_trace("router")]
    code, out = verdict(capsys, ["--links"])
    assert code == 0 and "/traces/t1" in out, out


def test_a_repeated_cursor_ends_in_a_verdict_not_a_loop(server, capsys):
    server.anchor_pages = [[], []]
    server.repeat_cursor = True
    code, out = verdict(capsys)
    assert code == 1 and "repeated cursor" in out


def test_only_direct_route_runs_is_not_ready(server, capsys):
    server.anchor_pages = [[]]  # main.py's last question routes direct: no grade step, no scores
    code, out = verdict(capsys)
    assert code == 1 and "FAIL  a scored 'agentic-rag' run" in out


# 136-2: required judge state

@pytest.mark.parametrize("docker, message", [("missing", "FileNotFoundError"), ("timeout", "TimeoutExpired"),
                                             ("denied", "permission denied")])
def test_unreadable_judge_rules_fail(server, capsys, docker, message):
    server.docker = docker
    code, out = verdict(capsys)
    assert code == 1
    line = next(ln for ln in out.splitlines() if "judge rules not readable" in ln)
    assert line.lstrip().startswith("FAIL") and message in line and "->" in line


@pytest.mark.parametrize("rules, shown", [({"arag-faithfulness": "BLOCKED"}, "arag-faithfulness BLOCKED"),
                                          ({}, "arag-faithfulness missing")])
def test_blocked_or_missing_judge_rules_fail(server, capsys, rules, shown):
    server.rules = rules
    code, out = verdict(capsys)
    assert code == 1 and shown in out


def test_judge_rules_are_read_for_this_project_only(server, capsys):
    verdict(capsys)
    sql = server.commands[0][-1]
    assert "project_id = 'p1'" in sql and "blocked_at" in sql


def test_cloud_mode_needs_judge_scores_instead_of_rules(server, capsys):
    server.env.write_text(ENV_TEXT.replace("self-hosted", "cloud"))
    code, out = verdict(capsys)
    assert code == 0 and server.commands == [] and "stands in for the rule check" in out
    server.scores = [s for s in server.scores if s["name"] != "faithfulness"]
    code, out = verdict(capsys)
    assert code == 1 and "missing faithfulness" in out


def test_missing_judge_scores_fail(server, capsys):
    server.scores = [s for s in server.scores if s["name"] in v.EXPECTED_SCORES]
    code, out = verdict(capsys)
    assert code == 1 and "missing faithfulness, context-relevance, answer-relevance" in out


def test_missing_in_graph_score_is_not_ready(server, capsys):
    server.scores = [s for s in server.scores if s["name"] != "groundedness"]
    code, out = verdict(capsys)
    assert code == 1 and "missing groundedness" in out


def test_no_self_correcting_run_is_not_ready(server, capsys):
    server.scores = [s for s in server.scores if not (s["name"] == "retrieval_relevance" and s["value"] == 0)]
    code, out = verdict(capsys)
    assert code == 1 and "0 self-correcting run(s)" in out


def test_missing_graph_node_is_not_ready(server, capsys):
    server.traces["t1"] = [obs("t1-o1", "t1", "route"), obs("t1-o3", "t1", "grade-relevance")]
    code, out = verdict(capsys)
    assert code == 1 and "missing retrieve, generate, reflect-groundedness" in out


# Seeded assets and services

def test_missing_production_label_is_not_ready(server, capsys):
    for p in server.prompt_versions:
        p["labels"] = []
    code, out = verdict(capsys)
    assert code == 1 and "prompt agentic-rag-generation with label production: HTTP 404" in out


def test_prompt_without_its_variables_is_not_ready(server, capsys):
    server.seed_prompt("Answer the question.", PROMPT.CONFIG)
    code, out = verdict(capsys)
    assert code == 1


def test_act_3b_needs_two_versions_unless_short(server, capsys):
    server.seed_prompt(PROMPT.V2, PROMPT.CONFIG, versions=1)
    assert verdict(capsys)[0] == 1
    code, out = verdict(capsys, ["--short"])
    assert code == 0 and "WARN  prompt agentic-rag-generation has 1 version(s)" in out


def test_act_4_services_are_required_unless_short(server, capsys):
    server.down_ports = {3080, 8007}
    code, out = verdict(capsys)
    assert code == 1 and "FAIL  LibreChat (Act 4) not reachable" in out
    code, out = verdict(capsys, ["--short"])
    assert code == 0 and "WARN  LibreChat (Act 4)" in out and "short path" in out


def test_clickhouse_down_is_not_ready(server, capsys):
    server.ch_down = True
    code, out = verdict(capsys, ["--short"])
    assert code == 1 and "FAIL  clickhouse-vectors not reachable" in out


@pytest.mark.parametrize("count, index, shown", [(0, "", "holds 0 chunk(s)"), (28, "", "index (missing)"),
                                                 (None, "", "readable: HTTP 404")])
def test_missing_vector_assets_are_not_ready(server, capsys, count, index, shown):
    server.ch_count, server.ch_index = count, index
    code, out = verdict(capsys)
    assert code == 1 and shown in out


@pytest.mark.parametrize("path", ["/api/public/v3/scores", "/api/public/v2/observations", "/api/public/v2/prompts"])
def test_langfuse_errors_mid_run_reach_the_verdict(server, capsys, path):
    server.fail[path] = 500
    code, out = verdict(capsys)
    assert code == 1 and "HTTP 500" in out


def test_a_malformed_response_reaches_the_verdict(server, capsys):
    server.anchor_pages = [[{"id": "x", "traceId": "t1", "startTime": "yesterday", "name": "grade-relevance"}]]
    code, out = verdict(capsys)
    assert code == 1 and "unexpected Langfuse response" in out


def test_stale_run_only_warns(server, capsys):
    server.anchor_pages = [[obs("t1-o3", "t1", "grade-relevance", "2026-01-01T00:00:00.000Z")]]
    code, out = verdict(capsys)
    assert code == 0 and "WARN  latest run is" in out
