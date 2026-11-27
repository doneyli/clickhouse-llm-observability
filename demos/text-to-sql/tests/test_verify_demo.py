"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
and full runs against an HTTP-level fake Langfuse (fake_langfuse.py), so the real client
builds every request. No network, no Langfuse, no model calls."""
from __future__ import annotations

import os
import urllib.error

import pytest

import verify_demo as v
from fake_langfuse import FakeServer, ready_server

@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    del v.SHORT_PATH_FAILS[:]
    yield
    del v.RESULTS[:]
    del v.SHORT_PATH_FAILS[:]
    os.environ.clear()
    os.environ.update(saved)


# --- Helpers kept from the template --------------------------------------------------------

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
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    assert v.env_file("standalone") == v.DEMO_DIR / ".env"
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "", "profile") == "container"
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


# --- Configuration: the root .env is the only source -----------------------------------------

HOSTILE = {"LANGFUSE_PUBLIC_KEY": "pk-other-project", "LANGFUSE_SECRET_KEY": "sk-other",
           "LANGFUSE_HOST": "https://other.example", "LANGFUSE_BASE_URL": "https://other.example"}


def test_missing_env_file_with_hostile_shell_fails_before_any_request(tmp_path, monkeypatch, capsys):
    for k, val in HOSTILE.items():
        monkeypatch.setenv(k, val)
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / ".env")
    monkeypatch.setattr(v, "in_container", lambda: False)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  env loaded from" in out and "NOT READY" in out


def test_partial_env_file_names_what_is_missing(tmp_path, monkeypatch):
    for k, val in HOSTILE.items():
        monkeypatch.setenv(k, val)
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-file\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    cfg = v.runtime_config(container=False)
    assert cfg.public_key == "pk-file"
    assert "LANGFUSE_SECRET_KEY" in cfg.error and "LANGFUSE_BASE_URL" in cfg.error  # shell values dropped


def test_inside_the_container_compose_env_is_used(monkeypatch):
    env = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_BASE_URL": "http://langfuse-web:3000"}
    cfg = v.runtime_config(env, container=True)
    assert (cfg.base, cfg.error) == ("http://langfuse-web:3000", "")


# --- What a ready demo looks like ------------------------------------------------------------

def test_expectations_match_the_seeders():
    """verify expects exactly the prompts seed-app-prompts.py writes and the datasets the seeders own."""
    import seed_all as s

    seeded = {}
    for spec in s.prompt_specs(s.load_module("seed_app_prompts", s.PROMPT_SEEDER)):
        seeded.setdefault(spec["name"], set()).add(spec["label"])
    expected = {}
    for group in (v.SHORT_PATH_PROMPTS, v.FULL_PROMPTS):
        for name, labels in group.items():
            expected.setdefault(name, set()).update(labels)
    assert expected == {k: x for k, x in seeded.items() if k.startswith("text-to-sql-")}
    assert sorted(v.dataset_definitions()) == sorted(v.FULL_DATASETS)
    assert [len(x) for x in v.dataset_definitions().values()] == [8, 10, 6]


def test_trace_name_matches_the_app():
    import inspect

    import langfuse_config

    assert inspect.signature(langfuse_config.langfuse_trace).parameters["trace_name"].default == v.TRACE_NAME


# --- Full runs against the fake server ---------------------------------------------------------

@pytest.fixture
def server(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=http://lf.test\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "in_container", lambda: False)
    monkeypatch.delenv("DEMO_LANGFUSE_PROJECT", raising=False)
    return ready_server(monkeypatch)


def run(capsys, *argv):
    code = v.main(list(argv))
    return code, capsys.readouterr().out


def test_ready_against_a_seeded_server(server, capsys):
    code, out = run(capsys, "--links")
    assert code == 0
    assert out.strip().splitlines()[-1].startswith("Full presentation: READY (")
    assert "Short path (Opening, Acts 1–2, Close): READY" in out
    assert "http://lf.test/project/p1/traces/t1" in out
    assert "http://lf.test/project/p1/datasets/" in out
    assert all(m == "GET" for m, *_ in server.requests)                  # read-only
    assert not any(p.startswith("/api/public/observations") for _, p, *_ in server.requests)  # v2 answered


def test_v2_404_falls_back_to_v1_observations(server, capsys):
    server.v2_observations = False
    code, out = run(capsys)
    assert code == 0, out
    assert "Observations v1" in out and "latest run has 6 observation(s)" in out


def test_empty_cursor_pages_are_followed(server, capsys):
    server.empty_pages = True
    code, out = run(capsys)
    assert code == 0, out
    assert "latest run has 6 observation(s)" in out and "has a 'gate-pass' score" in out


def test_a_repeated_cursor_is_a_failed_read_not_a_hang(server, capsys):
    server.repeat_cursor = True
    server.observations += [dict(o, id=o["id"] + f"-{i}") for i in range(200) for o in server.observations[:1]]
    code, out = run(capsys)
    assert code == 1 and "repeated a cursor" in out


def test_archived_only_dataset_is_not_ready(server, capsys):
    for row in server.items["text-to-sql-analysis-step"]:
        row["status"] = "ARCHIVED"
    code, out = run(capsys)
    assert code == 1
    assert "FAIL  dataset text-to-sql-analysis-step: 0/8 seeded items ACTIVE and unchanged" in out
    assert "Short path (Opening, Acts 1–2, Close): READY" in out   # Act 5 only


def test_an_edited_item_does_not_count(server, capsys):
    server.items["text-to-sql/critic-accuracy"][0]["expectedOutput"] = {"verdict": "accept"}
    code, out = run(capsys)
    assert code == 1 and "9/10 seeded items" in out and "crit-01" in out


def test_missing_dataset_is_not_ready(server, capsys):
    del server.datasets["text-to-sql/converged-sql"]
    code, out = run(capsys)
    assert code == 1 and "FAIL  dataset text-to-sql/converged-sql is missing" in out


@pytest.mark.parametrize("project", ["someone-elses-project", None])
def test_wrong_or_empty_project_is_not_ready(server, capsys, project):
    server.projects = [{"id": "p9", "name": project}] if project else []
    code, out = run(capsys)
    assert code == 1 and "keys map to project" in out and "NOT READY" in out


def test_missing_short_path_label_fails_both_verdicts(server, capsys):
    for p in server.prompts["text-to-sql-gate-grounding"]:
        p["labels"] = []
    code, out = run(capsys)
    assert code == 1
    assert "FAIL  prompt text-to-sql-gate-grounding with label production" in out
    assert "Short path (Opening, Acts 1–2, Close): NOT READY" in out


def test_missing_act3_label_fails_only_the_full_verdict(server, capsys):
    for p in server.prompts["text-to-sql-critic"]:
        p["labels"] = [x for x in p["labels"] if x != "opinion-only"]
    code, out = run(capsys)
    assert code == 1
    assert "FAIL  prompt text-to-sql-critic with label opinion-only" in out
    assert "Short path (Opening, Acts 1–2, Close): READY" in out
    assert "Full presentation: NOT READY" in out


def test_a_prompt_read_error_is_a_failure(server, capsys):
    server.fail[("GET", "/api/public/v2/prompts/text-to-sql-generator")] = 500
    code, out = run(capsys)
    assert code == 1 and "FAIL  prompt text-to-sql-generator with label production: HTTP 500" in out


def test_a_dataset_read_error_reaches_the_verdict(server, capsys):
    server.fail[("GET", "/api/public/dataset-items")] = 503
    code, out = run(capsys)
    assert code == 1 and "FAIL  Langfuse read failed" in out and "NOT READY" in out


def test_missing_score_is_not_ready(server, capsys):
    server.scores = [sc for sc in server.scores if sc["name"] != "sql-risk"]
    code, out = run(capsys)
    assert code == 1 and "FAIL  latest run has a 'sql-risk' score" in out
    assert "Short path (Opening, Acts 1–2, Close): READY" in out


def test_no_run_is_not_ready(server, capsys):
    server.observations = []
    code, out = run(capsys)
    assert code == 1 and "docker compose --profile demo run --rm text-to-sql python main.py" in out


def test_an_old_run_only_warns(server, capsys):
    server.observations = []
    server.scores = []
    server.add_run(start=(v.datetime.now(v.timezone.utc) - v.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    code, out = run(capsys)
    assert code == 0 and "WARN  latest run is" in out


def test_service_down_is_not_ready(server, monkeypatch, capsys):
    def refuse(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr(v, "SERVICES", [("mcp-clickhouse", "localhost", 8001)])
    monkeypatch.setattr(v.socket, "create_connection", refuse)
    code, out = run(capsys)
    assert code == 1 and "FAIL  mcp-clickhouse not reachable on localhost:8001" in out


def test_links_cover_every_act():
    state = {"project_id": "p1", "trace_id": "t1",
             "dataset_ids": {"text-to-sql/converged-sql": "dc", "text-to-sql-analysis-step": "ds"}}
    links = v.act_links("https://lf.example", state)
    assert [label.split(" ·")[0] for label, _ in links] == \
        ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5", "Act 5", "Act 6"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
