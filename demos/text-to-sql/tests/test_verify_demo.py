"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
the Observations v1 fallback, and full runs against a fake Langfuse. No network, no
Langfuse, no model calls."""
from __future__ import annotations

import os
import urllib.error

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


# --- What a ready demo looks like ------------------------------------------------------------

def test_expectations_match_the_seeders():
    """verify expects exactly what seed-app-prompts.py and the dataset seeders write."""
    import seed_all as s

    seeded = {}
    for name, label, _ in s.prompt_entries(s.load_module("seed_app_prompts", s.PROMPT_SEEDER)):
        seeded.setdefault(name, []).append(label)
    expected = dict(v.EXPECTED_PROMPTS, **v.OPTIONAL_PROMPTS)
    assert {k: sorted(x) for k, x in expected.items()} == \
        {k: sorted(x) for k, x in seeded.items() if k.startswith("text-to-sql-")}

    step = s.load_module("seed_step_dataset", s.STEP_SEEDER)
    assert v.EXPECTED_DATASETS == {step.DATASET_NAME: len(step.CURATED_ITEMS)}
    refine = s.load_module("seed_refine_datasets", s.REFINE_SEEDER)
    assert v.OPTIONAL_DATASETS == {refine.CRITIC_DATASET: len(refine.CRITIC_ITEMS),
                                   refine.CONVERGED_DATASET: len(refine.CONVERGED_ITEMS)}


def test_trace_name_matches_the_app():
    import inspect

    import langfuse_config

    assert inspect.signature(langfuse_config.langfuse_trace).parameters["trace_name"].default == v.TRACE_NAME


# --- A fake Langfuse ---------------------------------------------------------------------------

class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows. Class attributes are the knobs."""

    project = "unused"
    scores = list(v.EXPECTED_SCORES)
    roots = [{"traceId": "t1", "startTime": NOW}]
    v1_only = False                 # a 3.221.1 server: no Observations v2
    missing_prompts: set = set()    # (name, label) pairs that 404
    datasets = dict(v.EXPECTED_DATASETS, **v.OPTIONAL_DATASETS)  # name -> item count
    calls: list = []

    def __init__(self, base, pk, sk):
        self.base = base

    def get(self, path, **params):
        type(self).calls.append(path)
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            name = v.urllib.parse.unquote(path.rsplit("/", 1)[1])
            if (name, params.get("label")) in self.missing_prompts:
                raise v.ApiError(404, "not found")
            return {"version": 3}
        if path == "/api/public/v2/datasets":
            return {"data": [{"id": f"d-{n}", "name": n} for n in self.datasets], "meta": {"totalPages": 1}}
        if path == "/api/public/dataset-items":
            return {"data": [], "meta": {"totalItems": self.datasets[params["datasetName"]]}}
        if path == "/api/public/v2/observations":
            if self.v1_only:
                raise v.ApiError(404, "not found")
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            return {"data": self.roots[:1]}
        if path == "/api/public/observations":
            if "traceId" in params:
                return {"data": [{"id": "o1"}], "meta": {"totalItems": 7, "totalPages": 7}}
            assert params["fromStartTime"] and params["toStartTime"]
            older = {"traceId": "t0", "startTime": "2098-12-31T00:00:00Z", "parentObservationId": None}
            child = {"traceId": "t1", "startTime": NOW, "parentObservationId": "x"}
            return {"data": [older, child] + [dict(r, parentObservationId=None) for r in self.roots],
                    "meta": {"totalPages": 1}}
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        if path == "/api/public/v2/observations":
            return [{"id": f"o{i}"} for i in range(v.MIN_OBSERVATIONS)]
        if path == "/api/public/v3/scores":
            return [{"name": n} for n in self.scores]
        raise AssertionError(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setenv("DEMO_LANGFUSE_PROJECT", "demo-project")
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(v, "SERVICES", [])
    monkeypatch.setattr(FakeLangfuse, "project", "demo-project")
    monkeypatch.setattr(FakeLangfuse, "calls", [])
    return FakeLangfuse


def test_ready_against_a_fake_langfuse(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert "https://lf.example/project/p1/datasets/d-text-to-sql-analysis-step" in out
    assert "/api/public/observations" not in fake.calls  # v2 answered: no fallback


def test_v2_404_falls_back_to_v1_observations(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v1_only", True)
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "Observations v1" in out and "latest run has 7 observation(s)" in out
    assert "/api/public/observations" in fake.calls


def test_v1_fallback_picks_the_newest_root():
    class V1(FakeLangfuse):
        v1_only = True

    latest, api = v.latest_root(V1("b", "pk", "sk"), v.parse_time("2099-01-01T00:00:00Z"))
    assert api == "v1" and latest["traceId"] == "t1"


def test_other_observation_errors_are_not_swallowed():
    class Broken(FakeLangfuse):
        def get(self, path, **params):
            raise v.ApiError(500, "boom")

    with pytest.raises(v.ApiError):
        v.latest_root(Broken("b", "pk", "sk"), v.parse_time("2099-01-01T00:00:00Z"))


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", ["gate-pass"])
    assert v.main([]) == 1
    assert "missing sql-risk" in capsys.readouterr().out


def test_missing_required_prompt_label_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "missing_prompts", {("text-to-sql-analysis", "candidate")})
    assert v.main([]) == 1
    assert "FAIL  prompt text-to-sql-analysis with label candidate" in capsys.readouterr().out


def test_act3_gaps_only_warn(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "missing_prompts", {("text-to-sql-critic", "opinion-only")})
    monkeypatch.setattr(fake, "datasets", {"text-to-sql-analysis-step": 8, "text-to-sql/converged-sql": 2})
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "WARN  prompt text-to-sql-critic with label opinion-only (Act 3)" in out
    assert "WARN  dataset text-to-sql/critic-accuracy (Act 3) is missing" in out
    assert "WARN  dataset text-to-sql/converged-sql: 2 item(s)" in out
    assert out.strip().splitlines()[-1].startswith("READY")


def test_short_step_dataset_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "datasets", dict(v.OPTIONAL_DATASETS, **{"text-to-sql-analysis-step": 3}))
    assert v.main([]) == 1
    assert "FAIL  dataset text-to-sql-analysis-step: 3 item(s) (want 8+)" in capsys.readouterr().out


def test_no_run_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "roots", [])
    assert v.main([]) == 1
    assert "docker compose --profile demo run --rm text-to-sql python main.py" in capsys.readouterr().out


def test_service_down_is_not_ready(fake, monkeypatch, capsys):
    def refuse(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr(v, "SERVICES", [("mcp-clickhouse", "localhost", 8001)])
    monkeypatch.setattr(v.socket, "create_connection", refuse)
    assert v.main([]) == 1
    assert "FAIL  mcp-clickhouse not reachable on localhost:8001" in capsys.readouterr().out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    state = {"project_id": "p1", "trace_id": "t1",
             "dataset_ids": {"text-to-sql/converged-sql": "dc", "text-to-sql-analysis-step": "ds"}}
    links = v.act_links("https://lf.example", state)
    assert [label.split(" ·")[0] for label, _ in links] == \
        ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5", "Act 5", "Act 6"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
