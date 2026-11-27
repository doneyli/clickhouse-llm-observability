"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
the v2 -> v1 observations fallback, and full runs against a fake Langfuse. No network, no
Langfuse, no model calls."""
from __future__ import annotations

import os
import urllib.error

import pytest

import seed_prompts
import verify_demo as v

NOW = "2099-01-01T00:00:00.000Z"      # always newer than the lookback window start
EARLIER = "2098-12-31T00:00:00.000Z"


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


# --- helpers kept from the template ------------------------------------------------------

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


def test_container_profile_reads_the_root_env():
    assert v.env_file("container") == v.REPO_ROOT / ".env"
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


# --- demo-specific logic -------------------------------------------------------------------

def test_expected_prompts_match_what_seed_prompts_seeds():
    texts = v.expected_prompt_texts()
    assert sorted(texts) == sorted(v.SEED_PROMPTS)
    assert len(v.SEED_PROMPTS) == 7
    assert texts[v.VERSIONED_PROMPT] == seed_prompts.SQL_VOTER_V2


def test_expected_datasets_match_what_seed_datasets_seeds():
    import seed_datasets
    assert v.EXPECTED_DATASETS == {seed_datasets.CATEGORY_DATASET: len(seed_datasets.CATEGORY_ITEMS),
                                   seed_datasets.SQL_VOTING_DATASET: len(seed_datasets.SQL_VOTING_ITEMS)}
    assert v.EXPERIMENT_DATASET == seed_datasets.SQL_VOTING_DATASET


def test_latest_roots_sorts_newest_first_and_skips_children():
    rows = [{"name": v.TRACE_NAME, "root": True, "traceId": "old", "startTime": EARLIER},
            {"name": v.TRACE_NAME, "root": True, "traceId": "new", "startTime": NOW},
            {"name": v.TRACE_NAME, "root": False, "traceId": "child", "startTime": NOW},
            {"name": "experiment", "root": True, "traceId": "x", "startTime": NOW}]
    assert [r["traceId"] for r in v.latest_roots(rows, v.TRACE_NAME)] == ["new", "old"]


def test_stage_gaps_lists_missing_stages_in_pipeline_order():
    full = [{"name": n} for n in v.STAGE_OBSERVATIONS]
    assert v.stage_gaps(full) == []
    partial = [o for o in full if o["name"] not in ("tally-votes", "branch-policy-guard")]
    assert v.stage_gaps(partial) == ["branch-policy-guard", "tally-votes"]


def test_run_names_reads_either_experiments_shape():
    rows = [{"name": "aggregator-result-signature"}, {"runName": "aggregator-majority-exact"}, {"id": 3}]
    assert v.run_names(rows) == ["aggregator-majority-exact", "aggregator-result-signature"]


class StubApi:
    """Records reads; answers v2 with `v2` (an ApiError instance raises) and v1 with `v1`."""

    def __init__(self, v2, v1=None):
        self.v2, self.v1, self.calls = v2, v1 or [], []

    def cursor(self, path, params, cap=1000):
        self.calls.append(path)
        if isinstance(self.v2, v.ApiError):
            raise self.v2
        return self.v2

    def pages(self, path, params, cap=1000):
        self.calls.append(path)
        return self.v1


def window():
    t = v.parse_time(NOW)
    return t, t


def test_observations_use_v2_and_its_root_flag():
    api = StubApi([{"name": "a", "isRootObservation": True}, {"name": "b"}])
    obs = v.Observations(api)
    rows = obs.list(*window(), name="a")
    assert obs.version == "v2" and [r["root"] for r in rows] == [True, False]
    assert api.calls == ["/api/public/v2/observations"]


def test_observations_fall_back_to_v1_on_404_only():
    api = StubApi(v.ApiError(404, "only available in a Langfuse v4 write mode"),
                  [{"name": "a", "parentObservationId": None}, {"name": "b", "parentObservationId": "p"}])
    obs = v.Observations(api)
    rows = obs.list(*window(), name="a")
    assert obs.version == "v1" and [r["root"] for r in rows] == [True, False]
    obs.list(*window(), trace_id="t")  # later reads go straight to v1
    assert api.calls == ["/api/public/v2/observations", "/api/public/observations", "/api/public/observations"]


def test_observations_do_not_mask_other_errors():
    with pytest.raises(v.ApiError):
        v.Observations(StubApi(v.ApiError(401, "unauthorized"))).list(*window(), name="a")


# --- full runs against a fake Langfuse -----------------------------------------------------

def stage_rows(trace_id):
    rows = [{"name": n, "traceId": trace_id, "startTime": NOW, "parentObservationId": "p"}
            for n in v.STAGE_OBSERVATIONS]
    rows[0]["parentObservationId"] = None
    return rows


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows, as a v3 server would
    (Observations v2 and experiments 404)."""

    project = "unused"
    scores = list(v.EXPECTED_SCORES) + list(v.EVALUATOR_SCORES)
    stages = list(v.STAGE_OBSERVATIONS)
    prompt_overrides = {}
    dataset_items = dict(v.EXPECTED_DATASETS)
    tie_trace = "t1"
    runs = [{"name": "aggregator-result-signature"}]

    def __init__(self, base, pk, sk):
        self.base = base

    def get(self, path, **params):
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            name = v.urllib.parse.unquote(path.rsplit("/", 1)[1])
            text = self.prompt_overrides.get(name, v.expected_prompt_texts()[name])
            return {"version": 2, "prompt": text}
        if path == "/api/public/dataset-items":
            return {"data": [{}], "meta": {"totalItems": self.dataset_items[params["datasetName"]]}}
        if path == "/api/public/experiments":
            raise v.ApiError(404, "not on a v3 server")
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        if path == "/api/public/v2/observations":
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            raise v.ApiError(404, "The observations v2 API is only available in a Langfuse v4 write mode")
        if path == "/api/public/v3/scores":
            return [{"name": n} for n in self.scores]
        raise AssertionError(path)

    def pages(self, path, params, cap=1000):
        if path == "/api/public/v2/datasets":
            return [{"name": n, "id": f"ds-{i}"} for i, n in enumerate(v.EXPECTED_DATASETS)]
        if path == "/api/public/observations":
            assert params["fromStartTime"] and params["toStartTime"]
            older = {"name": v.TRACE_NAME, "traceId": "t0", "startTime": EARLIER}
            if params.get("name") == v.TRACE_NAME:
                return [older, {"name": v.TRACE_NAME, "traceId": "t1", "startTime": NOW}]
            if params.get("name") == v.TIE_OBSERVATION:
                return ([{"name": v.TIE_OBSERVATION, "traceId": self.tie_trace, "startTime": NOW,
                          "parentObservationId": "p"}] if self.tie_trace else [])
            if params.get("traceId") == "t1":
                return [r for r in stage_rows("t1") if r["name"] in self.stages]
        if path.startswith("/api/public/datasets/") and path.endswith("/runs"):
            return self.runs
        raise AssertionError(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n"
                   "ANTHROPIC_API_KEY=sk-ant-test\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setenv("DEMO_LANGFUSE_PROJECT", "demo-project")
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(v, "playground_check", lambda host: v.check(True, f"playground {host}"))
    monkeypatch.setattr(FakeLangfuse, "project", "demo-project")
    return FakeLangfuse


def test_ready_against_a_fake_v3_server(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out and "WARN" not in out
    assert "read through v1" in out
    assert "https://lf.example/project/p1/traces/t1" in out          # latest run, and the tie trace
    assert "https://lf.example/project/p1/datasets/ds-1" in out      # the sql-voting dataset


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_stage_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "stages", [n for n in v.STAGE_OBSERVATIONS if n != "vote-sql"])
    assert v.main([]) == 1
    assert "missing vote-sql" in capsys.readouterr().out


def test_missing_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", ["consensus_confidence"])
    assert v.main([]) == 1
    assert "missing sql_validity_rate, policy_flagged" in capsys.readouterr().out


def test_short_dataset_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "dataset_items", dict(v.EXPECTED_DATASETS, **{"support-triage/sql-voting": 3}))
    assert v.main([]) == 1
    assert "support-triage/sql-voting: 3 item(s) (want 10)" in capsys.readouterr().out


def test_optional_acts_only_warn(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "tie_trace", None)
    monkeypatch.setattr(fake, "runs", [])
    monkeypatch.setattr(fake, "scores", list(v.EXPECTED_SCORES))
    monkeypatch.setattr(fake, "prompt_overrides", {"support-triage-summary": "edited in a rehearsal"})
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "4 warnings" in out


def test_tie_from_an_experiment_trace_does_not_count(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "tie_trace", "experiment-trace")
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "WARN  a tie trace for Act 3" in out and "the tie trace" not in out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1",
                                               "tie_trace_id": "t2", "experiment_dataset_id": "d1"})
    acts = [label.split(" ·")[0] for label, _ in links]
    assert acts == ["Act 1-2", "Act 3-4", "Act 5", "Act 6", "Act 7", "Show me the code"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
