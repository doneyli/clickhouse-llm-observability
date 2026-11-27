"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
finding the latest run on Observations v2 and on the v1 fallback, and full runs against a
fake Langfuse. No network, no Langfuse, no model calls."""
from __future__ import annotations

import json
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


def test_trace_name_filter_carries_the_trace_name_and_the_time_bound():
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)
    f = json.loads(v.trace_name_filter(since, {"column": "type", "value": "GENERATION"}))
    assert f[0] == {"type": "string", "column": "traceName", "operator": "=", "value": "vector-rag"}
    assert f[1]["column"] == "startTime" and f[1]["value"] == "2026-11-01T00:00:00Z"
    assert f[2]["value"] == "GENERATION"


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


GEN = {"id": "g1", "traceId": "t1", "type": "GENERATION", "startTime": NOW,
       "promptName": "vector-rag-generation", "promptVersion": 3}


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows. `v2` False plays the root
    stack's self-hosted v3 server, which answers Observations v2 with 404."""

    project = "unused"
    scores = list(v.EXPECTED_SCORES)
    roots = [{"traceId": "t1", "startTime": NOW}]
    trace_obs = [{"id": "root", "type": "SPAN", "startTime": NOW}, GEN]
    v1_generations = [GEN]
    v2 = True
    calls: list = []

    def __init__(self, base, pk, sk):
        self.base = base
        type(self).calls = []

    def get(self, path, **params):
        self.calls.append(path)
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            return {"version": 3}
        if path == "/api/public/v2/observations":
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            if not self.v2:
                raise v.ApiError(404, "Not Found")
            assert json.loads(params["filter"])[0]["value"] == "vector-rag"
            return {"data": self.roots[:1]}
        if path == "/api/public/observations":
            assert not self.v2
            if "traceId" in params:
                return {"data": self.trace_obs, "meta": {"totalPages": 1}}
            assert params["type"] == "GENERATION" and params["fromStartTime"]
            return {"data": self.v1_generations, "meta": {"totalPages": 1}}
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        self.calls.append(path)
        if path == "/api/public/v2/observations":
            assert params["traceId"] == "t1" and "prompt" in params["fields"]
            return self.trace_obs
        if path == "/api/public/v3/scores":
            return [{"name": n} for n in self.scores]
        raise AssertionError(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n"
                   "VECTOR_RAG_PORT=1\n")  # nothing listens on port 1: the optional API check warns
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setenv("DEMO_LANGFUSE_PROJECT", "demo-project")
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(FakeLangfuse, "project", "demo-project")
    return FakeLangfuse


def test_ready_against_a_fake_langfuse(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out
    assert "linked to vector-rag-generation v3" in out
    assert "read with observations v2" in out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert "WARN  vector-rag API" in out  # optional: the script does not need the server


def test_ready_on_a_v3_server_through_the_v1_fallback(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v2", False)
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "read with observations v1" in out
    assert "/api/public/observations" in fake.calls
    assert not any("/traces" in c for c in fake.calls)  # no legacy trace endpoints


def test_v1_fallback_finds_no_run_when_nothing_is_linked_to_the_prompt(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v2", False)
    monkeypatch.setattr(fake, "v1_generations", [dict(GEN, promptName=None)])
    assert v.main([]) == 1
    assert "FAIL  a 'vector-rag' run" in capsys.readouterr().out


def test_run_on_the_inline_fallback_prompt_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "trace_obs", [{"id": "root", "type": "SPAN"}, dict(GEN, promptName=None)])
    assert v.main([]) == 1
    assert "FAIL  latest run's generation is linked" in capsys.readouterr().out


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", ["output-present"])
    assert v.main([]) == 1
    assert "missing structure-clean, response-length, credential-leak" in capsys.readouterr().out


def test_other_api_errors_are_not_swallowed_by_the_fallback(fake, monkeypatch, capsys):
    real_get = fake.get

    def boom(self, path, **params):
        if path == "/api/public/v2/observations":
            raise v.ApiError(500, "boom")
        return real_get(self, path, **params)

    monkeypatch.setattr(fake, "get", boom)
    assert v.main([]) == 1
    assert "Langfuse read failed: HTTP 500" in capsys.readouterr().out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1"})
    assert [label.split(" ·")[0] for label, _ in links] == ["Act 1", "Act 2", "Act 3", "Act 4"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
    assert links[1][1].endswith("/prompts/vector-rag-generation")
