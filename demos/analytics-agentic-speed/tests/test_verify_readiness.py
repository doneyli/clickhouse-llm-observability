"""verify_demo.py end to end against an in-memory Langfuse: request shapes, paging and verdicts.

No network: Langfuse is an httpx.MockTransport that rejects what the public API rejects,
ClickHouse and the Anthropic lookup are stubbed, and the .env is a temp file.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

DEMO = Path(__file__).resolve().parent.parent
for p in (DEMO, DEMO / "scripts", DEMO / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import verify_demo as v  # noqa: E402
from explorer import config  # noqa: E402
from fakes_langfuse import FakeLangfuseServer  # noqa: E402

GOOD_ENV = """\
CH_HOST=svc.example.clickhouse.cloud
CH_USER=scenario_agent
CH_PASSWORD=pw
LANGFUSE_PUBLIC_KEY=pk-lf-demo
LANGFUSE_SECRET_KEY=sk-lf-demo
LANGFUSE_BASE_URL=https://fake.langfuse.test
"""
HOSTILE = {"LANGFUSE_PUBLIC_KEY": "pk-lf-OTHER", "LANGFUSE_SECRET_KEY": "sk-lf-OTHER",
           "LANGFUSE_HOST": "https://other.example", "LANGFUSE_BASE_URL": "https://other.example"}


def healthy() -> FakeLangfuseServer:
    s = FakeLangfuseServer()
    for name in (v.PLANNER, v.ANALYST):
        s.prompts[(name, "production")] = {"name": name, "version": 1, "prompt": "text", "labels": ["production"]}
        s.prompts[(name, v.CANDIDATE_LABEL)] = {"name": name, "version": 3, "prompt": "coarse",
                                                "labels": [v.CANDIDATE_LABEL]}
    s.items = [{"id": f"q{i}", "status": "ACTIVE", "expectedOutput": {"x": i}} for i in range(15)]
    s.configs = [{**c, "id": c["name"], "isArchived": False} for c in v.seed.SCORE_CONFIGS]
    s.dashboards = [{"id": "d1", "name": v.seed.DASHBOARD["name"]}]
    s.dashboard_widgets = {"d1": [{"id": f"pl{i}", "widgetId": f"w{i}"} for i in range(len(v.seed.WIDGETS))]}
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s.observation_pages = [[{"id": "o1", "traceId": "t1", "startTime": start, "type": "AGENT",
                             "name": v.seed.ROOT, "sessionId": None, "metadata": {"question_id": v.HERO}}]]
    s.scores = [{"id": f"s-{n}", "traceId": "t1", "name": n, "value": 1.0, "dataType": "NUMERIC",
                 "subject": {"kind": "trace", "id": "t1"}}
                for n in ("db_time_share", "scenarios_evaluated", "db_p95_ms", "recommendation_grounded")]
    s.scores.append({"id": "s-q", "traceId": "t1", "name": "db_server_ms", "value": 12.0, "dataType": "NUMERIC",
                     "subject": {"kind": "observation", "id": "o9", "traceId": "t1"}})
    s.experiments = [{"id": "e1", "name": "breadth-10-20261126T232112Z", "datasetId": "ds1"},
                     {"id": "e2", "name": "breadth-300-20261126T232112Z", "datasetId": "ds1"}]
    return s


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Run verify_demo.main against a fake server; returns (exit code, stdout, server)."""
    async def ch_ok(cfg):
        v.check(True, "ClickHouse stubbed")

    monkeypatch.setattr(v, "clickhouse_checks", ch_ok)
    monkeypatch.setattr(v, "anthropic_check", lambda cfg: v.check(True, "Anthropic stubbed"))
    monkeypatch.setattr(v.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("no live view")))
    for k, val in HOSTILE.items():
        monkeypatch.setenv(k, val)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("EXPLORER_TARGET", raising=False)
    for k in ("CH_HOST", "CH_USER", "CH_PASSWORD"):  # load_env sets these: restore them afterwards
        monkeypatch.setenv(k, "unset")
        monkeypatch.delenv(k)

    def go(server: FakeLangfuseServer, env_text: str | None = GOOD_ENV, argv=None):
        env_file = tmp_path / ".env"
        if env_text is not None:
            env_file.write_text(env_text)
        monkeypatch.setattr(config, "ENV_FILE", env_file)
        # The real Langfuse.from_env and httpx.Client run; only the transport is in memory.
        monkeypatch.setattr(v, "TRANSPORT", server.transport())
        made = []
        real_init = v.Langfuse.__init__

        def spy(self, *a, **k):
            made.append(a[0] if a else k.get("base"))  # recorded before construction can fail
            real_init(self, *a, **k)
        monkeypatch.setattr(v.Langfuse, "__init__", spy)
        code = v.main(argv or [])
        return code, made

    return go


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


# ---------------------------------------------------------------- verdicts

def test_healthy_project_is_ready_with_the_files_keys(run, capsys):
    server = healthy()
    code, made = run(server)
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.strip().splitlines()[-1].startswith("READY: ")
    assert " 0 failed" in out.strip().splitlines()[-1]
    assert "WARN  live view not running" in out  # optional, so still READY
    # the .env's keys and base URL were used, never the hostile shell exports
    auth = server.requests[0].headers["authorization"]
    assert server.requests[0].url.host == "fake.langfuse.test"
    import base64
    assert base64.b64decode(auth.split()[1]).decode() == "pk-lf-demo:sk-lf-demo"
    assert made == ["https://fake.langfuse.test"]


def test_scores_request_subject_only_and_drop_observation_scores(run, capsys):
    server = healthy()
    run(server)
    reqs = [r for r in server.requests if r.url.path == "/api/public/v3/scores"]
    assert reqs and all(dict(r.url.params)["fields"] == "subject" for r in reqs)
    assert "db_server_ms" not in capsys.readouterr().out.split("latest run")[1].split("\n")[0]


def test_fixture_rejects_core_as_a_score_field_group():
    server = healthy()
    lf = v.Langfuse("https://fake.langfuse.test", "pk", "sk", transport=server.transport())
    with pytest.raises(httpx.HTTPStatusError) as e:
        lf.get("/api/public/v3/scores", traceId="t1", fields="core,subject")
    assert e.value.response.status_code == 400


def test_missing_env_with_hostile_shell_fails_before_any_request(run, capsys):
    server = healthy()
    code, made = run(server, env_text=None)
    out = capsys.readouterr().out
    assert code == 1 and not made and not server.requests
    assert "FAIL  no .env at" in out
    assert out.strip().splitlines()[-1].startswith("NOT READY: 0 passed, 1 failed")


@pytest.mark.parametrize("partial", [False, True])
def test_github_actions_never_lends_the_verifier_its_credentials(run, capsys, monkeypatch, partial):
    """On a runner, with the step env's keys exported, a missing or partial .env still fails first."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    server = healthy()
    code, made = run(server, env_text=GOOD_ENV.replace("LANGFUSE_PUBLIC_KEY=pk-lf-demo\n", "") if partial else None)
    out = capsys.readouterr().out
    assert code == 1 and not made and not server.requests
    assert ("FAIL  missing from" if partial else "FAIL  no .env at") in out
    assert out.strip().splitlines()[-1].startswith("NOT READY: 0 passed, 1 failed")


def test_malformed_base_url_is_a_counted_fail_before_any_client(run, capsys):
    server = healthy()
    code, made = run(server, env_text=GOOD_ENV.replace("https://fake.langfuse.test", "http://localhost:invalid"))
    out = capsys.readouterr().out
    assert code == 1 and not made and not server.requests
    assert "FAIL  LANGFUSE_BASE_URL in" in out and "http://localhost:invalid" in out
    assert out.strip().splitlines()[-1].startswith("NOT READY: 0 passed, 1 failed")


def test_client_construction_errors_reach_the_verdict(run, capsys, monkeypatch):
    """With the URL check out of the way, real httpx.Client construction raises InvalidURL."""
    monkeypatch.setattr(config, "base_url_problem", lambda url: None)
    server = healthy()
    code, made = run(server, env_text=GOOD_ENV.replace("https://fake.langfuse.test", "http://localhost:invalid"))
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1 and made == ["http://localhost:invalid"] and not server.requests
    assert any("FAIL  verification stopped: InvalidURL" in ln for ln in lines)
    assert any("PASS  settings from" in ln for ln in lines)  # the result recorded before it is kept
    assert lines[-1].startswith("NOT READY: 1 passed, 1 failed")


def test_unreadable_env_is_a_counted_fail(run, capsys, monkeypatch, tmp_path):
    real_open = Path.open

    def deny(self, mode="r", *a, **k):
        if self == tmp_path / ".env" and "r" in mode:  # the fixture may still write it
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, mode, *a, **k)
    monkeypatch.setattr(Path, "open", deny)
    server = healthy()
    code, made = run(server)
    out = capsys.readouterr().out
    assert code == 1 and not made and not server.requests
    assert "FAIL  cannot read" in out and "PermissionError" in out
    assert out.strip().splitlines()[-1].startswith("NOT READY: 0 passed, 1 failed")


def test_an_unexpected_error_keeps_earlier_results_and_reaches_the_verdict(run, capsys, monkeypatch):
    def boom(cfg):
        raise RuntimeError("model lookup exploded")
    monkeypatch.setattr(v, "anthropic_check", boom)
    server = healthy()
    code, _ = run(server)
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert "  PASS  ClickHouse stubbed" in lines  # recorded before the error, still counted
    assert any("FAIL  verification stopped: RuntimeError: model lookup exploded" in ln for ln in lines)
    assert lines[-1].startswith("NOT READY: 2 passed, 1 failed")


def test_help_and_usage_errors_behave_as_usual(capsys):
    with pytest.raises(SystemExit) as e:
        v.main(["--help"])
    assert e.value.code == 0 and "--links" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        v.main(["--no-such-flag"])
    assert e.value.code == 2


def test_keyboard_interrupt_is_not_swallowed(run, monkeypatch):
    def interrupt(cfg):
        raise KeyboardInterrupt
    monkeypatch.setattr(v, "anthropic_check", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(healthy())


def test_partial_env_does_not_borrow_the_shell_secret(run, capsys):
    server = healthy()
    code, made = run(server, env_text=GOOD_ENV.replace("LANGFUSE_SECRET_KEY=sk-lf-demo\n", ""))
    out = capsys.readouterr().out
    assert code == 1 and not made and not server.requests
    assert "LANGFUSE_SECRET_KEY" in out and "NOT READY" in out.strip().splitlines()[-1]


def test_401_on_projects_is_a_fail_not_a_traceback(run, capsys):
    server = healthy()
    server.override = lambda r: httpx.Response(401, json={"message": "Invalid credentials"})
    code, _ = run(server)
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  read /api/public/projects: HTTP 401" in out
    assert out.strip().splitlines()[-1].startswith("NOT READY")


def test_connection_error_reaches_the_verdict(run, capsys):
    server = healthy()

    def down(req):
        raise httpx.ConnectError("connection refused", request=req)
    server.override = down
    code, _ = run(server)
    out = capsys.readouterr().out
    assert code == 1 and "ConnectError" in out and out.strip().splitlines()[-1].startswith("NOT READY")


def test_a_later_read_failure_still_fails_and_the_rest_still_run(run, capsys):
    server = healthy()
    server.override = lambda r: (httpx.Response(500, text="boom")
                                 if r.url.path == "/api/public/score-configs" else None)
    code, _ = run(server)
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  score configs: HTTP 500" in out
    assert "Experiment A run 'breadth-300-*'" in out  # later sections still ran


@pytest.mark.parametrize("project", ["some-other-project", ""])
def test_wrong_or_empty_project_is_not_ready(run, capsys, project):
    server = healthy()
    server.project = project
    code, _ = run(server)
    assert code == 1 and "FAIL  keys map to project" in capsys.readouterr().out


def test_archived_only_dataset_is_not_ready(run, capsys):
    server = healthy()
    for i in server.items:
        i["status"] = "ARCHIVED"
    code, _ = run(server)
    assert code == 1 and "0/0 active items with ground truth (15 in all" in capsys.readouterr().out


def test_missing_production_label_is_not_ready(run, capsys):
    server = healthy()
    del server.prompts[(v.ANALYST, "production")]
    code, _ = run(server)
    assert code == 1 and f"FAIL  prompt {v.ANALYST} with label production" in capsys.readouterr().out


def test_score_config_of_the_wrong_type_is_not_ready(run, capsys):
    server = healthy()
    next(c for c in server.configs if c["name"] == "stop_reason")["dataType"] = "NUMERIC"
    code, _ = run(server)
    assert code == 1 and "stop_reason: NUMERIC, want CATEGORICAL" in capsys.readouterr().out


def test_missing_experiment_a_run_is_required_for_act_3(run, capsys):
    server = healthy()
    server.experiments = server.experiments[:1]
    code, _ = run(server)
    assert code == 1 and "FAIL  Experiment A run 'breadth-300-*'" in capsys.readouterr().out


def test_missing_core_dashboard_is_not_ready(run, capsys):
    server = healthy()
    server.dashboards = []
    code, _ = run(server)
    assert code == 1 and f"FAIL  dashboard '{v.seed.DASHBOARD['name']}': 0/{len(v.seed.WIDGETS)} widgets placed" \
        in capsys.readouterr().out


def test_missing_candidate_label_only_warns(run, capsys):
    server = healthy()
    del server.prompts[(v.PLANNER, v.CANDIDATE_LABEL)]
    code, _ = run(server)
    out = capsys.readouterr().out
    assert code == 0 and f"WARN  Act 5: prompt {v.PLANNER} with label {v.CANDIDATE_LABEL}" in out


def test_no_recent_run_is_not_ready(run, capsys):
    server = healthy()
    server.observation_pages = [[]]
    code, _ = run(server)
    assert code == 1 and "FAIL  0 run(s) in the last 7 days" in capsys.readouterr().out


def test_links_print_the_act_labels(run, capsys):
    run(healthy(), argv=["--links"])
    out = capsys.readouterr().out
    assert "latest run (Act 1)" in out and "dataset (Act 3)" in out and "dashboard (Close)" in out


# ---------------------------------------------------------------- cursors

def test_empty_first_page_with_a_cursor_keeps_reading(run, capsys):
    server = healthy()
    server.observation_pages = [[], server.observation_pages[0]]
    code, _ = run(server)
    out = capsys.readouterr().out
    assert code == 0, out
    obs = [r for r in server.requests if r.url.path == "/api/public/v2/observations"]
    assert any("cursor" in dict(r.url.params) for r in obs)
    assert all(dict(r.url.params)["fields"] == "core,basic" for r in obs)


def lf_with(handler) -> v.Langfuse:
    return v.Langfuse("https://fake.langfuse.test", "pk", "sk", transport=httpx.MockTransport(handler))


def test_cursor_reads_to_the_end_across_empty_pages():
    pages = {None: ([], "a"), "a": ([], "b"), "b": ([{"id": 1}], "c"), "c": ([{"id": 2}], None)}

    def handler(req):
        rows, nxt = pages[dict(req.url.params).get("cursor")]
        return httpx.Response(200, json={"data": rows, "meta": {"cursor": nxt} if nxt else {}})
    assert lf_with(handler).cursor("/api/public/v2/observations", {}) == [{"id": 1}, {"id": 2}]


def test_repeated_cursor_raises_instead_of_looping():
    def handler(req):
        return httpx.Response(200, json={"data": [], "meta": {"cursor": "same"}})
    with pytest.raises(v.CursorError, match="repeated a cursor"):
        lf_with(handler).cursor("/api/public/v2/observations", {})


def test_page_budget_is_bounded():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(200, json={"data": [], "meta": {"cursor": f"c{n['i']}"}})
    with pytest.raises(v.CursorError, match="still paging after 5 pages"):
        lf_with(handler).cursor("/api/public/v2/observations", {}, max_pages=5)
    assert n["i"] == 5


def test_cursor_stops_at_the_cap():
    def handler(req):
        return httpx.Response(200, json={"data": [{"id": i} for i in range(100)], "meta": {"cursor": "next"}})
    assert len(lf_with(handler).cursor("/x", {}, cap=100)) == 100
