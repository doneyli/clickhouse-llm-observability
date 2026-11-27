"""verify_demo.py offline: helpers, the .env rule, the manifest parse, and full runs against
an in-memory Langfuse served through a fake urlopen (tests/fake_langfuse.py), so the real
query strings, paging and parsing are exercised. No network, Langfuse, Docker or model calls."""
from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

import fake_langfuse as fl
import seed_all as s
import verify_demo as v


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A root .env with keys, and hostile shell exports for another project."""
    path = tmp_path / ".env"
    path.write_text(f"LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\nLANGFUSE_BASE_URL={fl.FakeLangfuse.base}\n")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://shell.invalid")
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    return path


@pytest.fixture
def fake(env, monkeypatch):
    server = fl.ready_project(fl.FakeLangfuse(), v, s)
    monkeypatch.setattr(urllib.request, "urlopen", server.urlopen)
    return server


def statuses(prefix: str):
    return [st for st, _, what in v.RESULTS if what.startswith(prefix)]


def failed():
    return [what for st, _, what in v.RESULTS if st == "FAIL"]


# --- helpers ------------------------------------------------------------------------------

def test_check_records_pass_fail_and_warn(capsys):
    assert v.check(True, "a") is True
    assert v.check(False, "b") is False
    assert v.check(False, "c", required=False, hint="do x") is False
    assert [st for st, _, _ in v.RESULTS] == ["PASS", "FAIL", "WARN"]
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("  WARN  c") and out[2].endswith("->  do x")


def test_summary_counts_and_exit_codes():
    assert v.summary([]) == ("NOT READY (0/0 checks passed)", 1)  # nothing checked is not ready
    v.check(True, "a")
    v.check(False, "b", required=False)
    assert v.summary() == ("READY (1/2 checks passed, 1 warning)", 0)
    v.check(False, "c")
    assert v.summary() == ("NOT READY (1/3 checks passed, 1 required failed, 1 warning)", 1)


def test_parse_env_handles_comments_quotes_and_export():
    text = '# comment\n\nexport A=1\nB="two words"  \nC=three # trailing\nD=\'x # y\'\nE=\nnot a line\n'
    assert v.parse_env(text) == {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""}


def test_env_file_overrides_shell_langfuse_keys(env):
    assert v.load_env(env) is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-file"
    assert "LANGFUSE_HOST" not in os.environ  # not in the file: the shell value is dropped


def test_missing_env_file_still_drops_shell_langfuse_keys(tmp_path):
    environ = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "OTHER": "kept"}
    assert v.load_env(tmp_path / ".env", environ) is False
    assert environ == {"OTHER": "kept"}


def test_container_demo_reads_the_root_env():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "", "profile") == "container"
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    assert v.env_file("standalone") == v.DEMO_DIR / ".env"
    assert v.manifest_value(text, "langfuse", "project") == "LLM Observability Demo"


def test_services_follow_the_compose_port_variables():
    assert [u for _, u in v.services({})] == [
        "http://localhost:8008/health", "http://localhost:8002/health",
        "http://localhost:8003/health", "http://localhost:8006/health"]
    assert v.services({"QUERY_ROUTER_PORT": "9008"})[0][1] == "http://localhost:9008/health"


def test_constants_match_the_router_code():
    import router
    assert v.ROUTES == router.ROUTES and v.PROMPT_NAME == router.ROUTER_PROMPT_NAME


def test_experiment_run_names_come_from_the_experiment_script():
    assert v.experiment_run_names() == ["router-prompt=baseline-model=claude-haiku-4-5",
                                        "router-prompt=production-model=claude-haiku-4-5",
                                        "router-prompt=production-model=claude-sonnet-4-6"]
    with pytest.raises(ValueError):
        v.experiment_run_names("X = 1\n")


def test_run_matches_exact_or_suffixed_names_only():
    run = "router-prompt=production-model=claude-haiku-4-5"
    assert v.run_matches(run, run) and v.run_matches(run + " - 2026-11-27T10:00:00Z", run)
    assert not v.run_matches(run + "-20251001", run) and not v.run_matches("router-prompt=production", run)


def test_parse_time_accepts_api_timestamps():
    assert v.parse_time("2026-11-26T10:00:00.123Z").microsecond == 123000
    assert v.parse_time("2026-11-26T10:00:00.123456789+00:00").microsecond == 123456


def test_network_failure_is_an_api_error_not_a_traceback(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(v.ApiError) as e:
        v.Langfuse("http://langfuse.invalid", "pk", "sk").get("/api/public/projects")
    assert e.value.status is None


# --- paging and the v3-server fallbacks, through real query strings ---------------------------

def client(server, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", server.urlopen)
    return v.Langfuse(server.base, "pk", "sk")


def test_observations_v2_ask_for_basic_so_names_come_back(monkeypatch):
    server = fl.FakeLangfuse()
    server.add_obs("route-query", "t1", None, fl.ts())
    rows = client(server, monkeypatch).observations(name="route-query", fromStartTime="2000-01-01T00:00:00Z")
    assert rows[0]["name"] == "route-query"
    method, path, query = server.requests[0]
    assert path == "/api/public/v2/observations" and query["fields"] == "core,basic"


def test_cursor_follows_empty_pages(monkeypatch):
    server = fl.FakeLangfuse()
    server.empty_cursor_pages, server.page_size = 3, 2
    for n in range(5):
        server.add_obs("route-query", f"t{n}", None, fl.ts())
    assert len(client(server, monkeypatch).observations(name="route-query")) == 5


def test_a_repeated_cursor_raises_instead_of_looping(monkeypatch):
    server = fl.FakeLangfuse()
    server.page_size, server.repeat_cursor = 1, True
    for n in range(3):
        server.add_obs("route-query", f"t{n}", None, fl.ts())
    with pytest.raises(v.ApiError, match="same cursor twice"):
        client(server, monkeypatch).observations(name="route-query")


def test_unfinished_pagination_raises(monkeypatch):
    server = fl.FakeLangfuse()
    server.page_size = 1
    for n in range(30):
        server.add_obs("route-query", f"t{n}", None, fl.ts())
    with pytest.raises(v.ApiError, match="more pages"):
        client(server, monkeypatch).cursor("/api/public/v2/observations", {"name": "route-query"}, max_pages=5)


def test_observations_fall_back_to_v1_only_on_a_v3_server(monkeypatch):
    server = fl.FakeLangfuse()
    server.v3_server = True
    server.add_obs("route-query", "t1", None, fl.ts())
    lf = client(server, monkeypatch)
    assert lf.observations(name="route-query")[0]["name"] == "route-query"
    assert lf.observations_api == "v1"
    assert [p for _, p, _ in server.requests] == ["/api/public/v2/observations", "/api/public/observations"]
    assert "fields" not in server.requests[1][2]


def test_other_observation_errors_propagate(monkeypatch):
    server = fl.FakeLangfuse()
    server.fail = lambda method, path, body: 401
    with pytest.raises(v.ApiError) as e:
        client(server, monkeypatch).observations(name="x")
    assert e.value.status == 401


def test_the_fake_rejects_parameters_an_endpoint_does_not_take(monkeypatch):
    with pytest.raises(v.ApiError) as e:
        client(fl.FakeLangfuse(), monkeypatch).get("/api/public/v3/scores", bogus=1)
    assert e.value.status == 400


# --- full runs ---------------------------------------------------------------------------------

def test_ready_for_the_full_script(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "\nREADY (" in out and not failed()
    assert f"{fake.base}/project/p1/traces/live-docs_complex" in out
    assert f"{fake.base}/project/p1/dashboards/dash1" in out
    for _, path, query in fake.requests:  # the hostile shell host was never used
        assert "shell.invalid" not in path


def test_ready_on_a_v3_server_through_the_fallbacks(fake, capsys):
    fake.v3_server = True
    assert v.main([]) == 0, failed()
    out = capsys.readouterr().out
    assert "no Observations v2" in out and "no experiments API" in out


def test_missing_env_file_is_not_ready_before_any_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://shell.invalid")
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "missing.env")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("request made"))
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_partial_env_file_is_not_ready_before_any_request(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LANGFUSE_PUBLIC_KEY=pk-file\n")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")  # must not fill the gap
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://shell.invalid")
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("request made"))
    assert v.main([]) == 1


@pytest.mark.parametrize("project", [{"id": "p2", "name": "someone-elses-project"}, None])
def test_wrong_or_empty_project_is_not_ready(fake, project):
    fake.project = project
    assert v.main([]) == 1
    assert any("keys map to project" in f for f in failed())


def test_wrong_keys_are_not_ready(fake):
    fake.fail = lambda method, path, body: 401 if path == "/api/public/projects" else None
    assert v.main([]) == 1


def test_missing_production_label_fails_even_on_the_short_path(fake):
    fake.prompts = [p for p in fake.prompts if "production" not in p["labels"]]
    assert v.main(["--short"]) == 1


def test_missing_baseline_label_fails_the_full_script_only(fake, capsys):
    fake.prompts = [p for p in fake.prompts if "baseline" not in p["labels"]]
    assert v.main([]) == 1
    assert v.main(["--short"]) == 0
    assert "READY for the short path only" in capsys.readouterr().out


def test_archived_only_dataset_is_not_ready(fake):
    for item in fake.items[v.DATASET_NAME]:
        item["status"] = "ARCHIVED"
    assert v.main([]) == 1
    assert any("0 usable ACTIVE item(s) of 30" in f for f in failed())


def test_items_without_a_usable_route_do_not_count(fake):
    for item in fake.items[v.DATASET_NAME][:5]:
        item["expectedOutput"] = {"route": "kb"}
    assert v.main([]) == 1


def test_aged_out_history_is_not_ready(fake):
    fake.observations = [o for o in fake.observations if not o["traceId"].startswith("h")
                         or int(o["traceId"][1:].split("-")[0]) < 7]
    assert v.main([]) == 1
    assert any(f.startswith("router history: ") for f in failed())


def test_missing_dashboard_is_not_ready(fake):
    fake.dashboards = []
    assert v.main([]) == 1
    assert "dashboard 'Router Ops' exists" in failed()


def test_server_without_a_dashboards_api_is_not_ready(fake, capsys):
    fake.dashboards_api = False
    assert v.main([]) == 1
    assert "no dashboards API" in capsys.readouterr().out


def test_unplaced_or_drifted_widget_is_not_ready(fake):
    fake.dashboards[0]["definition"]["widgets"].pop()
    fake.widgets[0]["filters"] = []
    assert v.main([]) == 1
    msgs = failed()
    assert any("not placed" in m for m in msgs) and any("differs in filters" in m for m in msgs)


def test_missing_widget_is_not_ready(fake):
    fake.widgets.pop(1)
    assert v.main([]) == 1


def test_missing_experiment_run_is_not_ready(fake):
    fake.experiments.pop(0)
    assert v.main([]) == 1
    assert any("experiment run 'router-prompt=baseline" in f for f in failed())


def test_experiment_without_scores_is_not_ready(fake):
    exp = fake.experiments[1]
    exp["scores"] = []
    for item in fake.experiment_items[exp["id"]]:
        item["scores"] = []
    assert v.main([]) == 1
    assert any("0 with route-match" in f and "avg_route_accuracy missing" in f for f in failed())


def test_missing_experiment_scores_on_a_v3_server_is_not_ready(fake):
    fake.v3_server = True
    fake.scores = [x for x in fake.scores if x["name"] != "route-match"]
    assert v.main([]) == 1


def test_short_path_turns_acts_3_to_5_into_warnings(fake, capsys):
    fake.dashboards, fake.experiments, fake.items[v.DATASET_NAME] = [], [], []
    assert v.main([]) == 1
    del v.RESULTS[:]
    assert v.main(["--short"]) == 0
    out = capsys.readouterr().out
    assert "SHORT PATH ONLY" in out and "READY for the short path only" in out


def test_no_live_run_is_not_ready(fake):
    fake.observations = [o for o in fake.observations if not o["traceId"].startswith("live")]
    assert v.main(["--short"]) == 1


def test_only_fallbacks_is_not_ready(fake):
    fake.observations = [o for o in fake.observations if not o["traceId"].startswith("live-")
                         or o["traceId"] == "live-fallback"]
    assert v.main(["--short"]) == 1
    assert any("dispatched to docs_complex" in f for f in failed())


def test_dispatch_without_nested_handler_spans_is_not_ready(fake):
    fake.observations = [o for o in fake.observations if not o["name"].startswith("handler-step")]
    assert v.main(["--short"]) == 1


def test_docs_simple_route_only_warns(fake):
    fake.observations = [o for o in fake.observations if o["traceId"] != "live-docs_simple"]
    assert v.main([]) == 0
    assert statuses("a recent live run dispatched to docs_simple") == ["WARN"]


def test_missing_runtime_score_is_not_ready(fake):
    fake.scores = [x for x in fake.scores if x["name"] != "router_confidence"]
    assert v.main(["--short"]) == 1


def test_router_that_cannot_reach_a_handler_is_not_ready(fake, capsys):
    url = v.services({})[0][1]
    fake.health[url] = (200, {"status": "ok", "langfuse": True,
                              "handlers": {"analytics_sql": "ok", "docs_simple": "unreachable: ConnectError",
                                           "docs_complex": "ok"}})
    assert v.main(["--short"]) == 1
    assert "not ok: docs_simple=unreachable" in capsys.readouterr().out


def test_service_down_is_not_ready(fake):
    fake.health.pop(v.services({})[2][1])
    assert v.main(["--short"]) == 1


def test_a_failed_read_fails_the_verdict(fake):
    fake.fail = lambda method, path, body: 500 if path == "/api/public/unstable/dashboard-widgets" else None
    assert v.main([]) == 1


def test_judge_scores_only_warn(fake):
    fake.scores = [x for x in fake.scores if x["name"] != "route-plausibility"]
    assert v.main([]) == 0
    assert statuses("'route-plausibility'") == ["WARN"]


def test_verify_makes_no_writes(fake):
    v.main(["--links"])
    assert fake.writes == []


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1", "dispatched_trace_id": "t2",
                                               "dataset_id": "d1", "dashboard_id": "x"})
    assert [label.split(" ·")[0] for label, _ in links] == ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5", "Act 5"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
