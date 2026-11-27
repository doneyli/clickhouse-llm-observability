"""verify_demo.py offline, end to end through its real HTTP code against tests/fake_services.py:
env loading, parameter construction, pagination, the v3 fallbacks, and the verdicts.
No network, no Langfuse, no ClickHouse, no Docker, no model calls."""
from __future__ import annotations

import os

import pytest

import verify_demo as v
from fake_services import PESSIMAL_DDL, PROJECTED_DDL, FakeServer

GOOD_ENV = ("LANGFUSE_PUBLIC_KEY=pk-lf-file\nLANGFUSE_SECRET_KEY=sk-lf-file\n"
            "LANGFUSE_BASE_URL=http://localhost:3001\nANTHROPIC_API_KEY=sk-ant-test\n")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Fresh results, os.environ restored (load_env writes to it), hostile shell exports."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    del v.ACTS[:]
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-shell-other-project")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-shell-other-project")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://other.example")
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example")
    for k in ("DEMO_LANGFUSE_PROJECT", "CLICKHOUSE_TUNING_HTTP_PORT", "TUNING_CH_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield
    os.environ.clear()
    os.environ.update(saved)


def setup(monkeypatch, tmp_path, server, env=GOOD_ENV, checkpoints=True):
    path = tmp_path / ".env"
    if env is not None:
        path.write_text(env)
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    cp = tmp_path / "checkpoints"
    if checkpoints:
        cp.mkdir()
    monkeypatch.setattr(v, "CHECKPOINTS", cp)
    monkeypatch.setattr(v, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(v.urllib.request, "urlopen", server)
    return server


def run(capsys, *args):
    code = v.main(list(args))
    return code, capsys.readouterr().out


@pytest.fixture
def v4(monkeypatch, tmp_path):
    return setup(monkeypatch, tmp_path, FakeServer("4.30.0").seed_ready())


@pytest.fixture
def v3(monkeypatch, tmp_path):
    return setup(monkeypatch, tmp_path, FakeServer("3.221.1").seed_ready())


# --- helpers -------------------------------------------------------------------------------

def test_summary_counts_and_exit_code():
    v.check(True, "a")
    v.check(False, "b", required=False)
    assert v.summary() == ("READY (1/2 checks passed, 1 warning)", 0)
    v.check(False, "c")
    assert v.summary() == ("NOT READY (1/3 checks passed, 1 required failed, 1 warning)", 1)
    assert v.summary([]) == ("NOT READY (0/0 checks passed)", 1)


def test_short_path_ignores_acts_outside_it():
    v.check(True, "every path")
    v.check(False, "Act 5 only", act=5)
    assert v.short_path_summary().endswith("READY (1/1 checks passed)")
    assert v.summary()[1] == 1


def test_parse_env_handles_comments_quotes_and_export():
    text = '# c\nexport A=1\nB="two words"\nC=three # trailing\nD=\'x # y\'\nE=\n'
    assert v.parse_env(text) == {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""}


def test_container_demo_reads_the_root_env_and_names_the_project():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "", "profile") == "container"
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    assert v.manifest_value(text, "langfuse", "project") == "LLM Observability Demo"


def test_lab_shape():
    assert v.lab_shape(PESSIMAL_DDL) == (True, "ORDER BY tuple(), no projection or index")
    assert v.lab_shape(PROJECTED_DDL)[0] is False
    indexed = PESSIMAL_DDL.replace("`user_id` UInt64\\n", "`user_id` UInt64,\\n    INDEX i user_id TYPE minmax GRANULARITY 1\\n")
    assert v.lab_shape(indexed) == (False, "ORDER BY tuple(), has index")
    assert v.lab_shape(PESSIMAL_DDL.replace("ORDER BY tuple()", "ORDER BY user_id"))[0] is False


# --- ready paths ---------------------------------------------------------------------------

def test_ready_on_a_current_server(v4, capsys):
    code, out = run(capsys, "--links")
    assert code == 0 and out.rstrip().splitlines()[-1].startswith("READY (")
    assert "(Observations v2)" in out and "(experiments API)" in out
    assert "http://localhost:3001/project/p1/traces/t1" in out          # newest root, not the first row
    assert "WARN  Act 4: no documented API reads Monitors" in out
    assert not any("/traces" in r or "/api/public/observations?" in r for r in v4.requests)
    assert not any("/runs" in r for r in v4.requests)


def test_ready_on_the_pinned_v3_server_through_its_fallbacks(v3, capsys):
    code, out = run(capsys)
    assert code == 0, out
    assert "Observations v1 (v2 is 404 on this v3 server)" in out
    assert "dataset runs (no experiments API on this v3 server)" in out
    assert "PASS  Act 4: Langfuse 3.221.1 has no Monitors" in out
    assert "latest run has 3 observation(s)" in out


def test_shell_exports_never_reach_a_request(v4, capsys):
    run(capsys)
    assert not any("other.example" in r for r in v4.requests)


def test_empty_cursor_pages_are_followed(v4, capsys):
    v4.empty_pages_first = 3
    code, out = run(capsys)
    assert code == 0, out
    assert "latest run has 3 observation(s)" in out


def test_repeated_cursor_is_not_ready(v4, capsys):
    v4.repeat_cursor = True
    code, out = run(capsys)
    assert code == 1 and "returned the same cursor twice" in out


# --- config failures stop before any network use -------------------------------------------

@pytest.mark.parametrize("env", [None, "", "LANGFUSE_PUBLIC_KEY=pk-lf-file\nANTHROPIC_API_KEY=x\n",
                                 GOOD_ENV.replace("ANTHROPIC_API_KEY=sk-ant-test\n", "")])
def test_missing_or_partial_env_is_not_ready_without_any_request(monkeypatch, tmp_path, capsys, env):
    server = setup(monkeypatch, tmp_path, FakeServer().seed_ready(), env=env)
    code, out = run(capsys)
    assert code == 1 and "NOT READY" in out
    assert server.requests == []
    assert "skipped until the config is complete" in out


# --- Langfuse state ------------------------------------------------------------------------

@pytest.mark.parametrize("projects", [[{"id": "p9", "name": "someone-elses-project"}], []])
def test_wrong_or_empty_project_is_not_ready(v4, capsys, projects):
    v4.projects = projects
    code, out = run(capsys)
    assert code == 1 and "keys map to project" in out


def test_langfuse_down_is_not_ready(v4, capsys):
    v4.down.add("langfuse")
    code, out = run(capsys)
    assert code == 1 and "FAIL  Langfuse at http://localhost:3001: URLError" in out


def test_missing_secondary_label_is_not_ready(v4, capsys):
    del v4.prompts[("query-tuner-system", "v2-disciplined")]
    code, out = run(capsys)
    assert code == 1 and "v2-disciplined: missing" in out


def test_labels_split_across_versions_are_not_ready(v4, capsys):
    v4.prompts[("query-tuner-system", "v2-disciplined")] = dict(v4.prompts[("query-tuner-system", "production")], version=9)
    code, out = run(capsys)
    assert code == 1 and "labels split across versions" in out


def test_config_only_drift_is_not_ready(v4, capsys):
    v4.prompts[("query-tuner-goal", "production")] = dict(v4.prompts[("query-tuner-goal", "production")],
                                                          config={"temperature": 1})
    code, out = run(capsys)
    assert code == 1 and "config differs" in out


def test_text_drift_is_not_ready(v4, capsys):
    v4.prompts[("query-tuner-system", "v1-naive")] = dict(v4.prompts[("query-tuner-system", "v1-naive")],
                                                          prompt="edited in the UI")
    code, out = run(capsys)
    assert code == 1 and "text differs from prompts.py" in out


def test_archived_only_dataset_is_not_ready(v4, capsys):
    for item in v4.items:
        item["status"] = "ARCHIVED"
    code, out = run(capsys)
    assert code == 1 and "0 ACTIVE item(s)" in out and "qt-q1 ARCHIVED" in out
    assert "Short path (Opening, Acts 1, 2, 4, Close): READY" in out    # Act 6 only


def test_changed_dataset_item_is_not_ready(v4, capsys):
    v4.items[1]["input"] = {"sql": "SELECT 1", "target_ms": 800}
    code, out = run(capsys)
    assert code == 1 and "qt-q2 differs from seed_dataset.py" in out


def test_missing_dataset_is_not_ready(v4, capsys):
    v4.datasets.clear()
    code, out = run(capsys)
    assert code == 1 and "dataset query-tuner/goals: missing" in out


def test_no_experiment_only_warns_but_a_failed_read_fails(v4, capsys):
    v4.experiments = []
    code, out = run(capsys)
    assert code == 0 and "WARN  Act 6 (bonus): 0" in out
    del v.RESULTS[:]
    del v.ACTS[:]
    v4.fail["experiments"] = 500
    code, out = run(capsys)
    assert code == 1 and "FAIL  Act 6: experiment read failed: HTTP 500" in out


def test_a_404_on_a_current_server_does_not_fall_back(v4, capsys):
    v4.fail["v2_observations"] = 404
    code, out = run(capsys)
    assert code == 1 and "Langfuse read failed: HTTP 404" in out


def test_no_run_is_not_ready(v4, capsys):
    v4.observations = []
    code, out = run(capsys)
    assert code == 1 and "FAIL  a 'tune-clickhouse-query' run" in out


@pytest.mark.parametrize("missing,needle", [("run_cost_usd", "missing run_cost_usd"),
                                            ("cap_terminated", "runaway-loop-guard scores on the latest run; missing cap_terminated")])
def test_missing_required_scores_are_not_ready(v4, capsys, missing, needle):
    v4.scores = [s for s in v4.scores if s["name"] != missing]
    code, out = run(capsys)
    assert code == 1 and needle in out


def test_missing_judge_score_only_warns(v4, capsys):
    v4.scores = [s for s in v4.scores if s["name"] != "goal_drift"]
    code, out = run(capsys)
    assert code == 0 and "WARN  Act 6 (bonus): goal_drift" in out


def test_v3_act4_fallback_needs_its_scores(v3, capsys):
    v3.scores = [s for s in v3.scores if s["name"] != "cap_terminated"]
    code, out = run(capsys)
    assert code == 1 and "FAIL  Act 4: Langfuse 3.221.1 has no Monitors" in out


# --- lab, admin, checkpoints ---------------------------------------------------------------

def test_lab_down_is_not_ready_but_langfuse_is_still_checked(v4, capsys):
    v4.down.add("clickhouse")
    code, out = run(capsys)
    assert code == 1 and "docker compose --profile demo up -d clickhouse-tuning" in out
    assert "keys map to project 'LLM Observability Demo'" in out


def test_leftover_ddl_and_half_seeded_lab_are_not_ready(v4, capsys):
    v4.ch_ddl, v4.ch_rows = PROJECTED_DDL, 1_200_000
    code, out = run(capsys)
    assert code == 1 and "FAIL  lab table still pessimal" in out and "has 1200000 rows" in out


def test_admin_auth_failure_fails_act5_only(v4, capsys):
    v4.ch_users["tuner_admin"] = "rotated"
    code, out = run(capsys)
    assert code == 1 and "FAIL  Act 5: tuner_admin" in out
    assert "Short path (Opening, Acts 1, 2, 4, Close): READY" in out


def test_missing_checkpoints_folder_fails_act3(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path, FakeServer().seed_ready(), checkpoints=False)
    code, out = run(capsys)
    assert code == 1 and "FAIL  Act 3:" in out


def test_links_follow_the_act_order():
    links = v.act_links("http://lf", {"project_id": "p1", "trace_id": "t1", "dataset_id": "d1"})
    acts = [int(label.split(" ·")[0].split()[1]) for label, _ in links]
    assert acts == sorted(acts) and acts[0] == 1 and acts[-1] == 6


# --- failure boundary: every failure is a counted FAIL and a NOT READY verdict ---------------

def last_line(out):
    return out.rstrip().splitlines()[-1]


@pytest.mark.parametrize("line,needle", [
    ("LANGFUSE_BASE_URL=http://[::gg]", "LANGFUSE_BASE_URL http://[::gg] is a usable http(s) URL: "),
    ("LANGFUSE_BASE_URL=localhost:3001", "scheme must be http or https"),
    ("TUNING_CH_URL=http://localhost:99999", "tuning lab URL"),
])
def test_malformed_url_in_the_env_is_a_failed_check_without_any_request(monkeypatch, tmp_path, capsys, line, needle):
    env = GOOD_ENV.replace("LANGFUSE_BASE_URL=http://localhost:3001", line) if line.startswith("LANGFUSE") \
        else GOOD_ENV + line + "\n"
    server = setup(monkeypatch, tmp_path, FakeServer().seed_ready(), env=env)
    code, out = run(capsys)
    assert code == 1 and needle in out
    assert last_line(out).startswith("NOT READY (") and "required failed" in last_line(out)
    assert server.requests == []


@pytest.mark.parametrize("url", ["http://[::gg]", ""])
def test_request_construction_errors_become_api_errors(monkeypatch, url):
    """Real Request construction, no stubbed client: a bad base URL is an ApiError, not a ValueError."""
    monkeypatch.setattr(v.urllib.request, "urlopen", FakeServer().seed_ready())
    with pytest.raises(v.ApiError):
        v.Langfuse(url, "pk", "sk").get("/api/public/health")
    with pytest.raises(v.ApiError):
        v.clickhouse_query(url, "tuner_agent", "x", "SELECT 1")


def test_unreadable_env_is_not_ready_without_any_request(monkeypatch, tmp_path, capsys):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    server = setup(monkeypatch, tmp_path, FakeServer().seed_ready())
    (tmp_path / ".env").chmod(0)
    try:
        code, out = run(capsys)
    finally:
        (tmp_path / ".env").chmod(0o600)
    assert code == 1 and "FAIL  env loaded from .env; shell-exported LANGFUSE_* ignored: PermissionError" in out
    assert last_line(out).startswith("NOT READY (")
    assert server.requests == []


def test_non_utf8_env_is_not_ready_without_any_request(monkeypatch, tmp_path, capsys):
    server = setup(monkeypatch, tmp_path, FakeServer().seed_ready())
    (tmp_path / ".env").write_bytes(b"LANGFUSE_PUBLIC_KEY=\xff\xfe\n")
    code, out = run(capsys)
    assert code == 1 and "UnicodeDecodeError" in out and last_line(out).startswith("NOT READY (")
    assert server.requests == []


def test_unparseable_response_is_a_failed_check(v4, capsys):
    for o in v4.observations:
        o["startTime"] = "yesterday"
    code, out = run(capsys)
    assert code == 1 and "FAIL  Langfuse returned data the check cannot parse: ValueError" in out
    assert last_line(out).startswith("NOT READY (")


def test_unexpected_error_keeps_earlier_results_and_reaches_the_verdict(v4, capsys):
    v4.projects = ["not-a-project-object"]          # a response shape no check expects
    code, out = run(capsys)
    assert code == 1 and "FAIL  verify stopped by an unexpected error: AttributeError" in out
    assert "PASS  env loaded from .env" in out and "PASS  Act 5: tuner_admin authenticates" in out
    line = last_line(out)
    assert line.startswith("NOT READY (") and "1 required failed" in line
    passed = int(line.split("(")[1].split("/")[0])
    assert passed == sum(1 for s, _, _ in v.RESULTS if s == "PASS") > 0


def test_keyboard_interrupt_and_help_are_not_swallowed(monkeypatch, tmp_path, capsys):
    def interrupt(req, timeout=None):
        raise KeyboardInterrupt
    setup(monkeypatch, tmp_path, interrupt)
    with pytest.raises(KeyboardInterrupt):
        v.main([])
    with pytest.raises(SystemExit) as e:
        v.main(["--help"])
    assert e.value.code == 0 and "--links" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        v.main(["--no-such-flag"])
    assert e.value.code == 2
