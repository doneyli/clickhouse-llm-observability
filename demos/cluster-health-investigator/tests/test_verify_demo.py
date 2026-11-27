"""verify_demo.py offline: results, summary and exit code, the .env rules, the manifest parse,
pagination, the run-shape logic, the Observations v2 -> v1 fallback, and full and short
runs against a request-level fake Langfuse (tests/fake_langfuse.py) that rejects invalid
query parameters and honours field projection. No network, no Langfuse, no Docker, no
model calls."""
from __future__ import annotations

import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import verify_demo as v  # noqa: E402
from fake_langfuse import FakeLangfuse  # noqa: E402

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
    assert v.manifest_value(text, "", "project") is None


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


# --- Env preparation (shared with the seed scripts) ----------------------------------------

HOSTILE = {"LANGFUSE_PUBLIC_KEY": "pk-other-project", "LANGFUSE_SECRET_KEY": "sk-other",
           "LANGFUSE_HOST": "https://cloud.example", "LANGFUSE_BASE_URL": "https://cloud.example",
           "LANGFUSE_TRACING_ENVIRONMENT": "prod"}


def test_prepare_env_on_host_drops_hostile_exports_and_loads_the_root_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-root\nLANGFUSE_SECRET_KEY=sk-root\nLANGFUSE_BASE_URL=http://localhost:3001\n"
                   "ANTHROPIC_MODEL=m\n")
    environ = dict(HOSTILE, PATH="/bin")
    ok, msg = v.prepare_env(environ, container=False, root_env=env)
    assert ok, msg
    assert environ["LANGFUSE_PUBLIC_KEY"] == "pk-root"
    assert environ["LANGFUSE_HOST"] == environ["LANGFUSE_BASE_URL"] == "http://localhost:3001"
    assert "LANGFUSE_TRACING_ENVIRONMENT" not in environ and environ["PATH"] == "/bin"


def test_prepare_env_fails_on_a_missing_root_env_without_keeping_shell_keys(tmp_path):
    environ = dict(HOSTILE)
    ok, msg = v.prepare_env(environ, container=False, root_env=tmp_path / ".env")
    assert not ok and "not found" in msg
    assert not any(k.startswith("LANGFUSE_") for k in environ)


def test_prepare_env_fails_on_a_partial_root_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-root\n")
    environ = dict(HOSTILE)
    ok, msg = v.prepare_env(environ, container=False, root_env=env)
    assert not ok and "LANGFUSE_SECRET_KEY" in msg and "LANGFUSE_BASE_URL" in msg


def test_prepare_env_in_the_container_uses_the_compose_environment(tmp_path):
    environ = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_HOST": "http://langfuse-web:3000"}
    ok, msg = v.prepare_env(environ, container=True, root_env=tmp_path / "absent")
    assert ok and environ["LANGFUSE_BASE_URL"] == "http://langfuse-web:3000"
    ok, msg = v.prepare_env({"LANGFUSE_PUBLIC_KEY": "pk"}, container=True)
    assert not ok and "container" in msg


def test_in_container_is_identified_positively(tmp_path):
    absent = tmp_path / "no-dockerenv"
    assert v.in_container({}, dockerenv=absent) is False
    assert v.in_container({v.CONTAINER_FLAG: "1"}, dockerenv=absent) is True
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    assert v.in_container({}, dockerenv=marker) is True
    assert "ENV CLUSTER_HEALTH_CONTAINER=1" in (v.DEMO_DIR / "Dockerfile").read_text()


# --- Pagination -----------------------------------------------------------------------------

def _client():
    return v.Langfuse("https://lf.example", "pk", "sk")


def test_cursor_follows_empty_pages_until_the_cursor_is_gone(monkeypatch):
    fake = FakeLangfuse(page_size=2, empty_pages=3).install(monkeypatch)
    for i in range(5):
        fake.add_score("worker_count", float(i), f"t{i}")
    rows = _client().cursor("/api/public/v3/scores", {"name": "worker_count"})
    assert [r["value"] for r in rows] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert sum(1 for c in fake.calls if c[1] == "/api/public/v3/scores") == 3 + 3  # 3 empty + 3 data pages


def test_cursor_rejects_a_repeated_cursor(monkeypatch):
    monkeypatch.setattr(v.Langfuse, "request", lambda self, m, p, params=None, body=None:
                        {"data": [{"i": 1}], "meta": {"cursor": "same"}})
    with pytest.raises(v.ApiError, match="same cursor twice"):
        _client().cursor("/x", {})


def test_cursor_and_pages_are_bounded(monkeypatch):
    n = iter(range(10 ** 6))
    monkeypatch.setattr(v.Langfuse, "request", lambda self, m, p, params=None, body=None:
                        {"data": [], "meta": {"cursor": f"c{next(n)}", "totalPages": 10 ** 6}})
    with pytest.raises(v.ApiError, match="more than 3 pages"):
        _client().cursor("/x", {}, max_pages=3)
    monkeypatch.setattr(v.Langfuse, "request", lambda self, m, p, params=None, body=None:
                        {"data": [{"i": 1}], "meta": {"totalPages": 10 ** 6}})
    with pytest.raises(v.ApiError, match="more than 3 pages"):
        _client().pages("/x", {}, max_pages=3)


def test_request_builds_the_query_string(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": []}'

    def urlopen(req, timeout):
        seen["url"], seen["method"] = req.full_url, req.get_method()
        return Resp()

    monkeypatch.setattr(v.urllib.request, "urlopen", urlopen)
    _client().get("/api/public/v2/observations", name="worker", isRootObservation=True, cursor=None)
    assert seen["url"] == "https://lf.example/api/public/v2/observations?name=worker&isRootObservation=true"
    assert seen["method"] == "GET"


# --- Run-shape logic ----------------------------------------------------------------------

def test_newest_picks_the_latest_start_time_whatever_the_order():
    rows = [{"traceId": "a", "startTime": "2026-11-26T10:00:00Z"},
            {"traceId": "b", "startTime": "2026-11-27T09:00:00.5Z"},
            {"traceId": "c", "startTime": "garbage"},
            {"traceId": "d", "startTime": "2026-11-25T23:00:00Z"}]
    assert v.newest(rows)["traceId"] == "b"
    assert v.newest([]) is None
    assert v.recent_trace_ids(rows + [{"traceId": "b", "startTime": "2026-11-20T00:00:00Z"}], 2) == ["b", "a"]


def test_score_trace_id_reads_the_v3_subject():
    assert v.score_trace_id({"subject": {"kind": "observation", "id": "o1", "traceId": "t1"}}) == "t1"
    assert v.score_trace_id({"subject": {"kind": "trace", "id": "t2"}}) == "t2"
    assert v.score_trace_id({"traceId": "t3"}) is None  # no subject: fields=subject was not requested
    assert v.score_trace_id({}) is None


def test_replan_traces_needs_two_orchestrator_rounds():
    obs = [{"traceId": "t1"}, {"traceId": "t2"}, {"traceId": "t2"}, {"traceId": "t3"}, {}]
    assert v.replan_traces(obs) == ["t2"]


def test_fanout_extremes_finds_the_narrowest_and_broadest_runs():
    scores = [{"subject": {"kind": "trace", "id": "t1"}, "value": 4.0},
              {"subject": {"kind": "trace", "id": "t2"}, "value": 2.0},
              {"subject": {"kind": "trace", "id": "t3"}, "value": 6.0},
              {"subject": {"kind": "trace", "id": "t4"}, "value": None}]
    assert v.fanout_extremes(scores) == (("t2", 2.0), ("t3", 6.0))
    assert v.fanout_extremes([]) == (None, None)


def test_missing_runs_accepts_exact_and_suffixed_run_names():
    names = ["planner-production", "planner-candidate-scoped-decomposition - 2026"]
    assert v.missing_runs(names, v.EXPERIMENT_RUNS) == []
    assert v.missing_runs(["planner-production"], v.EXPERIMENT_RUNS) == ["planner-candidate-scoped-decomposition"]


def test_parse_io_parses_v2_json_strings():
    assert v.parse_io('{"symptom": "x"}') == {"symptom": "x"}
    assert v.parse_io("plain text") == "plain text"
    assert v.parse_io({"a": 1}) == {"a": 1}


# --- Observations v2 -> v1 fallback -------------------------------------------------------

def test_observations_falls_back_to_v1_only_on_404(monkeypatch):
    fake = FakeLangfuse(v3_server=True).install(monkeypatch)
    fake.add_run("t1", ["slow_queries"])
    lf = _client()
    window = {"fromStartTime": "2098-01-01T00:00:00Z", "toStartTime": "2100-01-01T00:00:00Z"}
    rows = lf.observations(dict(window, name="worker"))
    assert [r["name"] for r in rows] == ["worker"] and lf.obs_api == "v1"
    lf.observations(dict(window, name="worker"))  # remembered: no second v2 attempt
    paths = [c[1] for c in fake.calls]
    assert paths == ["/api/public/v2/observations", "/api/public/observations", "/api/public/observations"]
    assert "fields" not in fake.calls[1][2] and fake.calls[1][2]["page"] == 1


def test_observations_does_not_hide_other_errors(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.fail[("GET", "/api/public/v2/observations")] = v.ApiError(401, "bad keys")
    with pytest.raises(v.ApiError) as e:
        _client().observations({"name": "worker", "fromStartTime": "x", "toStartTime": "y"})
    assert e.value.status == 401


def test_observations_v2_asks_for_the_basic_group_so_names_come_back(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.add_run("t1", ["slow_queries"])
    window = {"fromStartTime": "2098-01-01T00:00:00Z", "toStartTime": "2100-01-01T00:00:00Z"}
    rows = _client().observations(dict(window, traceId="t1"))
    assert rows and all("name" in r for r in rows) and not any("input" in r for r in rows)
    assert fake.calls[-1][2]["fields"] == "core,basic"


# --- Full runs against the fake -----------------------------------------------------------

def ready_fake(**kw) -> FakeLangfuse:
    """Everything the full presentation needs."""
    fake = FakeLangfuse(**kw)
    for name, label in sorted(v.EXPECTED_PROMPTS):
        fake.add_prompt(name, f"{name} {label}", [label], {"model": "m"})
    for ds, ids in v.GOLDEN_ITEMS.items():
        fake.datasets[ds] = {"id": "ds-plan" if ds == v.PLAN_DATASET else "ds-worker", "name": ds}
        fake.items[ds] = {i: {"id": i, "status": "ACTIVE", "input": {}} for i in ids}
    fake.items[v.PLAN_DATASET]["chp-src-o1"] = {"id": "chp-src-o1", "status": "ACTIVE", "sourceObservationId": "o1"}
    fake.add_run("t2", ["a", "b", "c", "d", "e", "f"], rounds=2, start="2098-12-31T10:00:00.000Z")
    fake.add_run("t3", [f"w{i}" for i in range(8)], overplan=True, start="2098-12-31T11:00:00.000Z")
    fake.add_run("t1", ["slow_queries", "settings_audit"], start="2099-01-01T00:00:00.000Z")  # the latest run
    for name in v.JUDGE_SCORES:
        fake.add_score(name, 4.0, "t1", "t1-synthesize")
    fake.add_score(v.DELEGATION_SCORE, 0.8, "t2", "t2-worker")
    fake.experiments = [{"name": n, "datasetId": "ds-plan"} for n in v.EXPERIMENT_RUNS]
    return fake


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    monkeypatch.setattr(v, "container_health", lambda name: (True, "running healthy"))
    monkeypatch.delenv("DEMO_LANGFUSE_PROJECT", raising=False)
    monkeypatch.delenv("MAX_WORKERS_TOTAL", raising=False)
    for k, val in HOSTILE.items():
        monkeypatch.setenv(k, val)
    return path


def run(monkeypatch, capsys, fake, *argv):
    fake.install(monkeypatch)
    code = v.main(list(argv))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("v3_server", [False, True])
def test_full_presentation_is_ready(env, monkeypatch, capsys, v3_server):
    code, out = run(monkeypatch, capsys, ready_fake(v3_server=v3_server), "--links")
    assert code == 0, out
    assert "READY (" in out and "NOT READY" not in out and "FAIL" not in out and "WARN" not in out
    assert "full presentation (Acts 1-6)" in out
    assert "https://lf.example/project/p1/traces/t1" in out          # Act 1, the latest run
    assert "Act 2 · narrow fan-out" in out and "Act 2 · broad fan-out" in out and "/traces/t3" in out
    assert "Act 3 · the re-plan run" in out and "/traces/t2" in out  # two orchestrator rounds
    assert "/datasets/ds-plan" in out


def test_hostile_shell_keys_never_reach_a_request(env, monkeypatch, capsys):
    fake = ready_fake()
    code, out = run(monkeypatch, capsys, fake)
    assert code == 0
    assert fake.bases and set(fake.bases) == {"https://lf.example"}


def test_wrong_project_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.projects = [{"id": "p9", "name": "someone-elses-project"}]
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "NOT READY" in out


def test_no_project_for_the_keys_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.projects = []
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "keys map to project 'None'" in out


@pytest.mark.parametrize("what", ["judges", "delegations", "overplan", "experiment", "linked"])
def test_full_presentation_requires_every_acts_evidence(env, monkeypatch, capsys, what):
    fake = ready_fake()
    if what == "judges":
        fake.scores = [s for s in fake.scores if s["name"] != "plan-scaling"]
    elif what == "delegations":
        fake.scores = [s for s in fake.scores if s["name"] != v.DELEGATION_SCORE]
    elif what == "overplan":
        fake.scores = [s for s in fake.scores if not (s["name"] == "worker_count" and s["value"] >= 8)]
    elif what == "experiment":
        fake.experiments = fake.experiments[:1]
    elif what == "linked":
        del fake.items[v.PLAN_DATASET]["chp-src-o1"]
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "NOT READY" in out
    code, out = run(monkeypatch, capsys, fake, "--short")
    assert code == 0 and "READY (" in out and "(not needed for --short)" in out and "WARN" in out


def test_short_path_still_requires_acts_2_and_3(env, monkeypatch, capsys):
    fake = FakeLangfuse()
    src = ready_fake()
    fake.prompts, fake.datasets, fake.items = src.prompts, src.datasets, src.items
    fake.add_run("t1", ["slow_queries"])  # one run: no spread, no re-plan
    code, out = run(monkeypatch, capsys, fake, "--short")
    assert code == 1
    assert "FAIL  runs with different fan-out" in out and "FAIL  0 run(s) with a second planning round" in out


def test_missing_run_score_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.scores = [s for s in fake.scores if not (s["subject"].get("id") == "t1" and s["name"] == "coverage_sufficient")]
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "missing coverage_sufficient" in out


def test_worker_count_that_disagrees_with_the_trace_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    next(s for s in fake.scores if s["subject"].get("id") == "t1" and s["name"] == "worker_count")["value"] = 5.0
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "FAIL  worker_count score 5 matches 2 worker observation(s)" in out


def test_missing_prompt_label_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    for p in fake.prompts["cluster-health-planner"].values():
        p["labels"] = [lab for lab in p["labels"] if lab != "fault-overplan"]
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "FAIL  prompt cluster-health-planner with label fault-overplan" in out


def test_archived_only_dataset_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    for item in fake.items[v.WORKER_DATASET].values():
        item["status"] = "ARCHIVED"
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and f"FAIL  dataset {v.WORKER_DATASET}: 0/10 golden items ACTIVE" in out


def test_missing_dataset_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    del fake.datasets[v.PLAN_DATASET]
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and f"FAIL  dataset {v.PLAN_DATASET}: HTTP 404" in out


def test_no_run_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.observations, fake.scores = [], []
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "FAIL  a 'investigate-cluster-symptom' run" in out


def test_a_failed_read_reaches_the_verdict(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.fail[("GET", "/api/public/v3/scores")] = v.ApiError(500, "boom")
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "FAIL  Langfuse read failed: HTTP 500: boom" in out and "NOT READY" in out


def test_unreachable_langfuse_is_not_ready(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.fail[("GET", "/api/public/projects")] = v.ApiError(None, "URLError: connection refused")
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "FAIL  keys authenticate" in out


def test_target_clickhouse_down_is_not_ready(env, monkeypatch, capsys):
    monkeypatch.setattr(v, "container_health", lambda name: (False, "No such object: langfuse-clickhouse"))
    code, out = run(monkeypatch, capsys, ready_fake())
    assert code == 1 and "FAIL  langfuse-clickhouse (the investigation target)" in out


def test_docker_missing_is_a_failure_not_a_traceback(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(v.subprocess, "run", boom)
    assert v.container_health("langfuse-clickhouse") == (False, "docker not usable: FileNotFoundError")


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1", "narrow_trace": "t1",
                                               "broad_trace": "t3", "replan_trace": "t2", "capped_trace": "t3",
                                               "dataset_id": "d1"})
    acts = [label.split(" ·")[0] for label, _ in links]
    assert sorted(set(acts)) == ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5", "Act 6"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)


# --- Failure boundary: every configuration and runtime failure reaches the verdict ----------
# These drive v.main() itself. The URL and env cases use the real Langfuse.request with
# urlopen guarded, so a request reaching the network fails the test.

def _no_network(monkeypatch):
    def guard(*a, **k):
        pytest.fail("no request may reach the network")

    monkeypatch.setattr(v.urllib.request, "urlopen", guard)


def _verdict(out):
    last = out.strip().splitlines()[-1]
    assert last.startswith("NOT READY (") and "required failed" in last, out
    return last


@pytest.mark.parametrize("url", ["http://[::gg]", "http://[::gg]:3001/", "ftp://lf.example", "http://lf.example:99999",
                                 "localhost:3001"])
def test_malformed_base_url_fails_a_check_before_any_request(env, monkeypatch, capsys, url):
    env.write_text(f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={url}\n")
    _no_network(monkeypatch)
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  invalid LANGFUSE_BASE_URL" in out and "Traceback" not in out
    assert "PASS  env loaded from" in out  # the checks already recorded are kept
    _verdict(out)


def test_malformed_url_inside_request_is_an_api_error_not_a_crash(monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(v.ApiError, match="invalid LANGFUSE_BASE_URL"):
        v.Langfuse("http://[::gg]", "pk", "sk").get("/api/public/projects")


def test_url_rejected_while_opening_is_an_api_error_not_a_crash():
    """http.client rejects a control character in the path (InvalidURL, a ValueError) in
    putrequest, before it connects: real request construction, no network."""
    with pytest.raises(v.ApiError, match="InvalidURL"):
        v.Langfuse("http://lf.example", "pk", "sk").get("/api/public/v2/prompts/a\nb")


def test_validate_base_url_accepts_the_stack_urls():
    assert v.validate_base_url("http://localhost:3001/") == "http://localhost:3001"
    assert v.validate_base_url("http://langfuse-web:3000") == "http://langfuse-web:3000"
    assert v.validate_base_url("https://us.cloud.langfuse.com") == "https://us.cloud.langfuse.com"
    assert v.validate_base_url("http://[::1]:3001") == "http://[::1]:3001"


def test_prepare_env_rejects_a_malformed_base_url_before_any_client(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=http://[::gg]\n")
    ok, msg = v.prepare_env({}, container=False, root_env=env)
    assert not ok and "invalid LANGFUSE_BASE_URL" in msg


def test_undecodable_env_file_fails_a_check_and_drops_shell_keys(env, monkeypatch, capsys):
    env.write_bytes(b"LANGFUSE_PUBLIC_KEY=\xff\xfe\n")
    _no_network(monkeypatch)
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  env file" in out and "unreadable: UnicodeDecodeError" in out
    assert not any(k.startswith("LANGFUSE_") for k in os.environ)  # hostile exports gone, not used
    _verdict(out)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any file")
def test_unreadable_env_file_fails_a_check(env, monkeypatch, capsys):
    env.chmod(0)
    _no_network(monkeypatch)
    try:
        assert v.main([]) == 1
    finally:
        env.chmod(0o600)
    out = capsys.readouterr().out
    assert "FAIL  env file" in out and "unreadable: PermissionError" in out
    _verdict(out)


def test_undecodable_manifest_fails_a_check(env, monkeypatch, capsys, tmp_path):
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "demo.yaml").write_bytes(b"profile: \xff\n")
    monkeypatch.setattr(v, "DEMO_DIR", demo)
    _no_network(monkeypatch)
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  demo.yaml unreadable: UnicodeDecodeError" in out
    _verdict(out)


def test_unexpected_response_shape_reaches_the_verdict(env, monkeypatch, capsys):
    """The latest run's worker_count is not a number: int() raises ValueError deep in the
    Act 1 checks. The earlier rows stay and the run still ends NOT READY."""
    fake = ready_fake()
    wc = [s for s in fake.scores if s["name"] == "worker_count" and v.score_trace_id(s) == "t1"]
    assert wc
    wc[0]["value"] = "n/a"
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1
    assert "PASS  keys map to project 'LLM Observability Demo'" in out and "PASS  prompt " in out
    assert "PASS  latest run has" in out
    assert "FAIL  verify stopped on an unexpected error: ValueError: invalid literal for int()" in out
    _verdict(out)


def test_malformed_projects_payload_reaches_the_verdict(env, monkeypatch, capsys):
    fake = ready_fake()
    fake.projects = "not a list"
    code, out = run(monkeypatch, capsys, fake)
    assert code == 1 and "FAIL  verify stopped on an unexpected error: AttributeError" in out
    _verdict(out)


def test_keyboard_interrupt_is_not_swallowed(env, monkeypatch):
    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(v, "service_checks", interrupt)
    with pytest.raises(KeyboardInterrupt):
        v.main([])


def test_help_and_usage_errors_keep_argparse_behaviour(capsys):
    with pytest.raises(SystemExit) as e:
        v.main(["--help"])
    assert e.value.code == 0 and "--short" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        v.main(["--no-such-flag"])
    assert e.value.code == 2
