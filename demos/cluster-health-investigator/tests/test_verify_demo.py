"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
the run-shape logic, the Observations v2 -> v1 fallback, and full runs against a fake
Langfuse. No network, no Langfuse, no Docker, no model calls."""
from __future__ import annotations

import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import verify_demo as v  # noqa: E402

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


# --- Run-shape logic ----------------------------------------------------------------------

def test_newest_picks_the_latest_start_time_whatever_the_order():
    rows = [{"traceId": "a", "startTime": "2026-11-26T10:00:00Z"},
            {"traceId": "b", "startTime": "2026-11-27T09:00:00.5Z"},
            {"traceId": "c", "startTime": "garbage"},
            {"traceId": "d", "startTime": "2026-11-25T23:00:00Z"}]
    assert v.newest(rows)["traceId"] == "b"
    assert v.newest([]) is None


def test_score_trace_id_reads_the_v3_subject():
    assert v.score_trace_id({"subject": {"kind": "observation", "id": "o1", "traceId": "t1"}}) == "t1"
    assert v.score_trace_id({"subject": {"kind": "trace", "id": "t2"}}) == "t2"
    assert v.score_trace_id({"traceId": "t3"}) == "t3"
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
    names = v.run_names([{"name": "planner-production"}, {"name": "planner-candidate-scoped-decomposition - 2026"}])
    assert v.missing_runs(names, v.EXPERIMENT_RUNS) == []
    assert v.missing_runs(["planner-production"], v.EXPERIMENT_RUNS) == ["planner-candidate-scoped-decomposition"]


# --- Observations v2 -> v1 fallback -------------------------------------------------------

def _scripted(responses):
    """A Langfuse whose request() answers from `responses`: path -> payload or ApiError."""
    calls = []

    def request(self, method, path, params=None, body=None):
        calls.append((path, dict(params or {})))
        r = responses[path]
        if isinstance(r, Exception):
            raise r
        return r

    return calls, request


def test_observations_falls_back_to_v1_only_on_404(monkeypatch):
    calls, request = _scripted({
        "/api/public/v2/observations": v.ApiError(404, "only available in a Langfuse v4 write mode"),
        "/api/public/observations": {"data": [{"id": "o1"}], "meta": {"page": 1, "totalPages": 1}},
    })
    monkeypatch.setattr(v.Langfuse, "request", request)
    lf = v.Langfuse("https://lf.example", "pk", "sk")
    assert lf.observations({"name": "worker"}) == [{"id": "o1"}]
    assert lf.obs_api == "v1"
    lf.observations({"name": "worker"})  # remembered: no second v2 attempt
    assert [p for p, _ in calls] == ["/api/public/v2/observations", "/api/public/observations",
                                     "/api/public/observations"]
    assert calls[1][1]["page"] == 1 and "fields" not in calls[1][1]


def test_observations_does_not_hide_other_errors(monkeypatch):
    _, request = _scripted({"/api/public/v2/observations": v.ApiError(401, "bad keys")})
    monkeypatch.setattr(v.Langfuse, "request", request)
    with pytest.raises(v.ApiError) as e:
        v.Langfuse("https://lf.example", "pk", "sk").observations({"name": "worker"})
    assert e.value.status == 401


def test_observations_uses_v2_when_served(monkeypatch):
    _, request = _scripted({"/api/public/v2/observations": {"data": [{"id": "o1"}], "meta": {}}})
    monkeypatch.setattr(v.Langfuse, "request", request)
    lf = v.Langfuse("https://lf.example", "pk", "sk")
    assert lf.observations({"traceId": "t1"}) == [{"id": "o1"}]
    assert lf.obs_api == "v2"


def test_pages_follows_total_pages(monkeypatch):
    pages = {1: {"data": [{"i": 1}], "meta": {"totalPages": 2}}, 2: {"data": [{"i": 2}], "meta": {"totalPages": 2}}}
    monkeypatch.setattr(v.Langfuse, "request", lambda self, m, p, params=None, body=None: pages[params["page"]])
    assert v.Langfuse("https://lf.example", "pk", "sk").pages("/x", {}) == [{"i": 1}, {"i": 2}]


# --- Full runs against a fake Langfuse ----------------------------------------------------

def _trace_obs(trace_id, workers, rounds=1):
    obs = [{"traceId": trace_id, "name": "LangGraph", "startTime": NOW}]
    for _ in range(rounds):
        obs += [{"traceId": trace_id, "name": "orchestrator", "startTime": NOW},
                {"traceId": trace_id, "name": "plan-analyses", "startTime": NOW},
                {"traceId": trace_id, "name": "replan-gate", "startTime": NOW}]
    for _ in range(workers):
        obs += [{"traceId": trace_id, "name": n, "startTime": NOW}
                for n in ("worker", "run-system-query", "interpret-findings")]
    obs.append({"traceId": trace_id, "name": "synthesize-diagnosis", "startTime": NOW})
    return obs


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows."""

    project = "unused"
    traces = {"t1": _trace_obs("t1", 2), "t2": _trace_obs("t2", 6, rounds=2), "t3": _trace_obs("t3", 8)}
    latest = "t1"
    run_scores = list(v.EXPECTED_SCORES) + list(v.JUDGE_SCORES)
    worker_count = 2
    dataset_items = dict(v.EXPECTED_DATASETS)
    missing_prompt = None
    experiments_404 = True

    def __init__(self, base, pk, sk):
        self.base = base

    def get(self, path, **params):
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            if (path.rsplit("/", 1)[1], params.get("label")) == self.missing_prompt:
                raise v.ApiError(404, "prompt not found")
            return {"version": 2}
        if path.startswith("/api/public/v2/datasets/"):
            return {"id": "ds-" + path.rsplit("%2F", 1)[-1]}
        raise AssertionError(path)

    def observations(self, params, cap=1000):
        assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
        if "traceId" in params:
            return list(self.traces.get(params["traceId"], []))
        rows = [o for t in self.traces.values() for o in t if o["name"] == params["name"]]
        if params["name"] == "synthesize-diagnosis":  # only the latest run's final observation is newest
            rows = [dict(o, startTime=NOW if o["traceId"] == self.latest else "2098-12-31T00:00:00Z") for o in rows]
        return rows

    def pages(self, path, params, cap=1000):
        if path == "/api/public/dataset-items":
            return [{"id": i} for i in range(self.dataset_items[params["datasetName"]])]
        if path.endswith("/runs"):
            return [{"name": n} for n in v.EXPERIMENT_RUNS]
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        if path == "/api/public/experiments":
            if self.experiments_404:
                raise v.ApiError(404, "not found")
            return [{"name": n} for n in v.EXPERIMENT_RUNS]
        if path != "/api/public/v3/scores":
            raise AssertionError(path)
        if "traceId" in params:
            return [{"name": n, "value": float(self.worker_count) if n == "worker_count" else 1.0}
                    for n in self.run_scores]
        if params["name"] == "worker_count":
            return [{"name": "worker_count", "value": float(sum(o["name"] == "worker" for o in obs)),
                     "subject": {"kind": "trace", "id": t}} for t, obs in self.traces.items()]
        if params["name"] == v.DELEGATION_SCORE:
            return [{"name": v.DELEGATION_SCORE, "value": 0.8}]
        raise AssertionError(params)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "container_health", lambda name: (True, "running healthy"))
    monkeypatch.setenv("DEMO_LANGFUSE_PROJECT", "demo-project")
    monkeypatch.delenv("MAX_WORKERS_TOTAL", raising=False)
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(FakeLangfuse, "project", "demo-project")
    return FakeLangfuse


def test_ready_against_a_fake_langfuse(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out and "WARN" not in out
    assert "https://lf.example/project/p1/traces/t1" in out          # Act 1, the latest run
    assert "Act 2 · broad fan-out" in out and "/traces/t3" in out    # broadest worker_count
    assert "Act 3 · the re-plan run" in out and "/traces/t2" in out  # two orchestrator rounds
    assert "/datasets/ds-plan-quality" in out


def test_experiments_api_is_used_when_served(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "experiments_404", False)
    assert v.main([]) == 0
    assert "Act 5: planner experiment runs" in capsys.readouterr().out


def test_optional_acts_only_warn(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "traces", {"t1": _trace_obs("t1", 2)})
    monkeypatch.setattr(fake, "run_scores", list(v.EXPECTED_SCORES))
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out
    assert out.count("WARN") == 4  # judges, Act 2 spread, Act 3 re-plan, Act 4 cap


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_run_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "run_scores", ["worker_count"])
    assert v.main([]) == 1
    assert "missing plan_execution_complete, coverage_sufficient" in capsys.readouterr().out


def test_worker_count_that_disagrees_with_the_trace_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "worker_count", 5)
    assert v.main([]) == 1
    assert "FAIL  worker_count score 5 matches 2 worker observation(s)" in capsys.readouterr().out


def test_missing_prompt_label_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "missing_prompt", ("cluster-health-planner", "fault-overplan"))
    assert v.main([]) == 1
    assert "FAIL  prompt cluster-health-planner with label fault-overplan" in capsys.readouterr().out


def test_short_dataset_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "dataset_items", {"cluster-health/plan-quality": 8, "cluster-health/worker-quality": 3})
    assert v.main([]) == 1
    assert "FAIL  dataset cluster-health/worker-quality: 3 item(s) (want 10+)" in capsys.readouterr().out


def test_no_run_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "traces", {})
    assert v.main([]) == 1
    assert "FAIL  a 'investigate-cluster-symptom' run" in capsys.readouterr().out


def test_target_clickhouse_down_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(v, "container_health", lambda name: (False, "No such object: langfuse-clickhouse"))
    assert v.main([]) == 1
    assert "FAIL  langfuse-clickhouse (the investigation target)" in capsys.readouterr().out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    links = v.act_links("https://lf.example", {"project_id": "p1", "trace_id": "t1", "narrow_trace": "t1",
                                               "broad_trace": "t3", "replan_trace": "t2", "dataset_id": "d1"})
    acts = [label.split(" ·")[0] for label, _ in links]
    assert sorted(set(acts)) == ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5", "Act 6"]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
