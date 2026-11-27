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
    assert "PASS  refine loop stages: generate-sql ×1, gather-evidence ×1, critique-sql ×1" in out
    assert "PASS  refine loop recorded how it ended: stop_reason='accepted', iterations=1" in out
    assert "Act 3 · the prepared refine run" in out and "http://lf.test/project/p1/traces/r1" in out
    assert "http://lf.test/project/p1/datasets/" in out
    assert all(m == "GET" for m, *_ in server.requests)                  # read-only
    assert not any(p.startswith("/api/public/observations") for _, p, *_ in server.requests)  # v2 answered


def test_v2_404_falls_back_to_v1_observations(server, capsys):
    server.v2_observations = False
    code, out = run(capsys)
    assert code == 0, out
    assert "Observations v1" in out and "latest run has 6 observation(s)" in out
    assert "PASS  refine loop stages: generate-sql ×1" in out


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
    old = (v.datetime.now(v.timezone.utc) - v.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    server.add_refine_run(start=old)
    server.add_run(start=old)
    code, out = run(capsys)
    assert code == 0 and "WARN  latest run is" in out and "WARN  refine run is" in out


def test_service_down_is_not_ready(server, monkeypatch, capsys):
    def refuse(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr(v, "SERVICES", [("mcp-clickhouse", "localhost", 8001)])
    monkeypatch.setattr(v.socket, "create_connection", refuse)
    code, out = run(capsys)
    assert code == 1 and "FAIL  mcp-clickhouse not reachable on localhost:8001" in out


def test_links_cover_every_act():
    state = {"project_id": "p1", "trace_id": "t1", "refine_trace_id": "r1",
             "dataset_ids": {"text-to-sql/converged-sql": "dc", "text-to-sql-analysis-step": "ds"}}
    links = v.act_links("https://lf.example", state)
    assert [label.split(" ·")[0] for label, _ in links] == \
        ["Act 1", "Act 2", "Act 3", "Act 3", "Act 4", "Act 5", "Act 5", "Act 6"]
    assert ("Act 3 · the prepared refine run", "https://lf.example/project/p1/traces/r1") in links
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)


# --- Act 3: a prepared refine run, not just any run (131-4) -------------------------------------

def drop_refine_run(server):
    server.observations = [o for o in server.observations if o["traceId"] != "r1"]
    server.scores = [sc for sc in server.scores if sc["traceId"] != "r1"]


def full_only_failure(out):
    assert "Short path (Opening, Acts 1–2, Close): READY" in out
    assert "Full presentation: NOT READY" in out


def test_a_normal_chain_alone_is_not_full_ready(server, capsys):
    """Six observations plus gate-pass and sql-risk used to pass the full verdict."""
    drop_refine_run(server)
    code, out = run(capsys)
    assert code == 1
    assert "PASS  latest run has 6 observation(s)" in out and "PASS  latest run has a 'sql-risk' score" in out
    assert "FAIL  a 'sql-refine-loop' span in the last 7 days" in out and "--refine" in out
    full_only_failure(out)


def test_the_refine_reads_ask_for_the_field_groups_they_use(server, capsys):
    code, _ = run(capsys)
    assert code == 0
    v2 = [q for m, p, q, _ in server.requests if p == "/api/public/v2/observations"]
    loop = [q for q in v2 if q.get("name") == "sql-refine-loop"]
    stages = [q for q in v2 if q.get("traceId") == "r1"]
    assert loop and all({"basic", "io"} <= set(q["fields"].split(",")) for q in loop)
    assert stages and all("basic" in q["fields"].split(",") for q in stages)


@pytest.mark.parametrize("stages,missing", [
    (("generate-sql", "gather-evidence"), "critique-sql"),
    (("generate-sql",), "gather-evidence, critique-sql"),
    ((), "generate-sql, gather-evidence, critique-sql"),
])
def test_missing_refine_stages_fail_the_full_verdict(server, capsys, stages, missing):
    drop_refine_run(server)
    server.add_refine_run(stages=stages)
    code, out = run(capsys)
    assert code == 1 and f"missing {missing}" in out and "FAIL  refine loop stages" in out
    full_only_failure(out)


def test_stages_outside_the_loop_span_do_not_count(server, capsys):
    drop_refine_run(server)
    server.add_refine_run()
    for o in server.observations:
        if o["traceId"] == "r1" and o["name"] in v.REFINE_STAGES:
            o["parentObservationId"] = "r1-root"
    code, out = run(capsys)
    assert code == 1 and "FAIL  refine loop stages: generate-sql ×0" in out


def test_a_loop_span_without_a_text_to_sql_root_fails(server, capsys):
    drop_refine_run(server)
    server.add_refine_run(root=False)
    code, out = run(capsys)
    assert code == 1 and "FAIL  refine run r1 has a 'text-to-sql' root" in out
    full_only_failure(out)


@pytest.mark.parametrize("output", [
    {},                                                        # the loop never wrote its output
    {"iterations": 2, "critic_scores": [0.4, 0.95]},           # no stop reason
    {"stop_reason": "running", "iterations": 1},               # not a reason the loop ends with
    {"stop_reason": "accepted", "iterations": 0},
    "not json",
])
def test_a_loop_that_did_not_record_how_it_ended_fails(server, capsys, output):
    drop_refine_run(server)
    server.add_refine_run(output=output)
    code, out = run(capsys)
    assert code == 1 and "FAIL  refine loop recorded how it ended" in out
    full_only_failure(out)


@pytest.mark.parametrize("score", v.REFINE_SCORES)
def test_a_missing_convergence_score_fails(server, capsys, score):
    server.scores = [sc for sc in server.scores if not (sc["traceId"] == "r1" and sc["name"] == score)]
    code, out = run(capsys)
    assert code == 1 and f"FAIL  refine run has a '{score}' score" in out
    full_only_failure(out)


@pytest.mark.parametrize("stop,iterations", [("oscillation", 2), ("max_iterations", 3)])
def test_a_loop_that_stopped_without_converging_is_still_a_prepared_run(server, capsys, stop, iterations):
    """Act 3 can show a run that hit its budget; what it needs is a run that ended."""
    drop_refine_run(server)
    server.add_refine_run(iterations=iterations,
                          output={"converged": False, "iterations": iterations, "stop_reason": stop})
    code, out = run(capsys)
    assert code == 0, out
    assert f"generate-sql ×{iterations}" in out


def test_a_refine_read_error_fails_only_the_full_verdict(server, capsys):
    server.fail[("GET", "/api/public/v3/scores")] = [None, 500]   # the latest run's read, then the refine run's
    code, out = run(capsys)
    assert code == 1 and "FAIL  Langfuse read failed (Act 3 refine run): ApiError: HTTP 500" in out
    full_only_failure(out)


# --- Every failure reaches the verdict (131-5) -----------------------------------------------------

def env_with(server, tmp_path, monkeypatch, content):
    path = tmp_path / "bad.env"
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    return path


def verdict_counts(out):
    last = out.strip().splitlines()[-1]
    assert last.startswith("Full presentation: NOT READY (") and "required failed" in last
    return last


@pytest.mark.parametrize("url", ["http://[::gg]", "http://[::gg]:3001", "lf.test", "ftp://lf.test",
                                 "http://lf.test:99999"])
def test_a_malformed_base_url_is_a_failed_check_with_a_verdict(server, tmp_path, monkeypatch, capsys, url):
    env_with(server, tmp_path, monkeypatch,
             f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={url}\n")
    code, out = run(capsys)
    assert code == 1
    assert "PASS  env loaded from" in out
    assert "FAIL  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and a valid LANGFUSE_BASE_URL set" in out
    assert repr(url) in out
    verdict_counts(out)
    assert server.requests == []                                   # failed before any API use


def test_request_construction_errors_are_api_errors(monkeypatch):
    """The client's own boundary, for a URL that slipped past config (a caller's)."""
    monkeypatch.setattr(v.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no request expected"))
    with pytest.raises(v.ApiError, match="cannot build the request"):
        v.Langfuse("lf.test", "pk", "sk").get("/api/public/projects")


@pytest.mark.parametrize("kind", ["bad-utf8", "no-permission"])
def test_an_unreadable_env_file_is_a_failed_check_with_a_verdict(server, tmp_path, monkeypatch, capsys, kind):
    if kind == "bad-utf8":
        env_with(server, tmp_path, monkeypatch, b"LANGFUSE_PUBLIC_KEY=\xff\xfe\n")
    else:
        if os.geteuid() == 0:
            pytest.skip("root reads any file")
        path = env_with(server, tmp_path, monkeypatch, "LANGFUSE_PUBLIC_KEY=pk\n")
        path.chmod(0)
    code, out = run(capsys)
    assert code == 1
    assert "FAIL  env loaded from" in out and "cannot read" in out
    verdict_counts(out)
    assert server.requests == []


def test_an_unexpected_response_shape_is_a_failed_check_with_a_verdict(server, monkeypatch, capsys):
    monkeypatch.setattr(server, "_projects", lambda *a: (200, [{"id": "p1"}]))   # a list, not {"data": [...]}
    code, out = run(capsys)
    assert code == 1
    assert "PASS  env loaded from" in out                          # earlier rows are kept
    assert "FAIL  verifier stopped: AttributeError" in out
    verdict_counts(out)


def test_an_unexpected_error_mid_run_keeps_the_rows_before_it(server, monkeypatch, capsys):
    real = server._scores_v3
    monkeypatch.setattr(server, "_scores_v3", lambda *a: (200, {"data": ["not-a-score"], "meta": {}})
                        if a[1].get("traceId") == "t1" else real(*a))
    code, out = run(capsys)
    assert code == 1
    assert "PASS  prompt text-to-sql-analysis: production" in out and "PASS  latest run has 6 observation(s)" in out
    assert "FAIL  verifier stopped: AttributeError" in out
    verdict_counts(out)


def test_help_and_usage_errors_still_exit_as_argparse_does(capsys):
    with pytest.raises(SystemExit) as ok:
        v.main(["--help"])
    assert ok.value.code == 0
    with pytest.raises(SystemExit) as bad:
        v.main(["--bogus"])
    assert bad.value.code == 2


def test_keyboard_interrupt_is_not_swallowed(monkeypatch):
    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(v, "runtime_config", interrupt)
    with pytest.raises(KeyboardInterrupt):
        v.main([])
