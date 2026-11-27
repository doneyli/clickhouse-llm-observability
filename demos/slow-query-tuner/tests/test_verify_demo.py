"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
the lab-shape parse, the v2 -> v1 observations fallback, and full runs against a fake Langfuse
and a fake ClickHouse. No network, no Langfuse, no ClickHouse, no model calls."""
from __future__ import annotations

import os
import urllib.error

import pytest

import verify_demo as v

NOW = "2099-01-01T00:00:00.000Z"  # always newer than the lookback window start
PESSIMAL_DDL = ("CREATE TABLE tuning_lab.web_events\\n(\\n    `event_id` String,\\n    `user_id` UInt64\\n)\\n"
                "ENGINE = MergeTree\\nORDER BY tuple()\\nSETTINGS index_granularity = 8192\n")
PROJECTED_DDL = ("CREATE TABLE tuning_lab.web_events\\n(\\n    `user_id` UInt64,\\n    PROJECTION p_user\\n    (\\n"
                 "        SELECT *\\n        ORDER BY user_id\\n    )\\n)\\nENGINE = MergeTree\\nORDER BY tuple()\n")


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


# --- helpers kept from the template -------------------------------------------------------

def test_check_records_pass_fail_and_warn(capsys):
    assert v.check(True, "a") is True
    assert v.check(False, "b") is False
    assert v.check(False, "c", required=False, hint="do x") is False
    assert [s for s, _, _ in v.RESULTS] == ["PASS", "FAIL", "WARN"]
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("  WARN  c") and out[2].endswith("->  do x")


def test_summary_ready_when_only_optional_checks_fail():
    v.check(True, "a")
    v.check(False, "b", required=False)
    assert v.summary() == ("READY (1/2 checks passed, 1 warning)", 0)


def test_summary_not_ready_when_a_required_check_fails():
    v.check(True, "a")
    v.check(False, "b")
    v.check(False, "c", required=False)
    assert v.summary() == ("NOT READY (1/3 checks passed, 1 required failed, 1 warning)", 1)


def test_parse_env_handles_comments_quotes_and_export():
    text = '# comment\n\nexport A=1\nB="two words"  \nC=three # trailing\nD=\'x # y\'\nE=\n'
    assert v.parse_env(text) == {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""}


def test_env_file_overrides_shell_langfuse_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://shell.example")  # not in the file: must be dropped
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=http://localhost:3001\n")
    assert v.load_env(env) is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-file"
    assert "LANGFUSE_HOST" not in os.environ


def test_container_demo_reads_the_root_env():
    profile = v.manifest_value((v.DEMO_DIR / "demo.yaml").read_text(), "", "profile")
    assert profile == "container"
    assert v.env_file(profile) == v.REPO_ROOT / ".env"


def test_manifest_names_the_project_verify_expects():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "langfuse", "project") == "LLM Observability Demo"


def test_parse_time_accepts_api_timestamps():
    assert v.parse_time("2026-11-26T10:00:00.123Z").microsecond == 123000
    assert v.parse_time("2026-11-26T10:00:00.123456789+00:00").microsecond == 123456


def test_network_failure_is_an_api_error_not_a_traceback(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(v.urllib.request, "urlopen", refuse)
    with pytest.raises(v.ApiError) as e:
        v.Langfuse("http://langfuse.invalid", "pk", "sk").get("/api/public/projects")
    assert e.value.status is None
    with pytest.raises(v.ApiError):
        v.clickhouse_query("http://ch.invalid", "u", "p", "SELECT 1")


# --- demo-specific logic ------------------------------------------------------------------

def test_lab_shape_accepts_the_seeded_table():
    ok, detail = v.lab_shape(PESSIMAL_DDL)
    assert ok and detail == "ORDER BY tuple(), no projection or index"


def test_lab_shape_flags_a_leftover_projection():
    ok, detail = v.lab_shape(PROJECTED_DDL)
    assert not ok and "has projection" in detail


def test_lab_shape_flags_a_skip_index_and_a_sort_key():
    with_index = PESSIMAL_DDL.replace("`user_id` UInt64\\n", "`user_id` UInt64,\\n    INDEX idx_u user_id TYPE bloom_filter GRANULARITY 1\\n")
    assert v.lab_shape(with_index) == (False, "ORDER BY tuple(), has index")
    sorted_ = PESSIMAL_DDL.replace("ORDER BY tuple()", "ORDER BY (event_date, user_id)")
    assert v.lab_shape(sorted_)[0] is False


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows."""

    project = "unused"
    scores = list(v.EXPECTED_SCORES) + list(v.OPTIONAL_SCORES)
    roots = [{"traceId": "old", "startTime": "2098-12-31T00:00:00Z"}, {"traceId": "t1", "startTime": NOW}]
    v2_status = None            # set to 404 to act like the self-hosted v3 server
    production_labels = ["production", "v2-disciplined", "latest"]
    items = [{"id": i, "status": "ACTIVE"} for i in v.DATASET_ITEMS]
    calls: list = []

    def __init__(self, base, pk, sk):
        self.base = base

    def get(self, path, **params):
        type(self).calls.append(path)
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            labels = self.production_labels if params["label"] == "production" else [params["label"]]
            return {"version": 3, "labels": labels}
        if path.startswith("/api/public/v2/datasets/"):
            return {"id": "d1", "name": v.DATASET_NAME}
        if path == "/api/public/dataset-items":
            assert params["datasetName"] == v.DATASET_NAME
            return {"data": self.items}
        if path.endswith("/runs"):
            return {"data": [{"name": v.EXPERIMENT_RUN_PREFIX + "v2-disciplined"}]}
        if path == "/api/public/monitors":
            raise v.ApiError(404, "not found")
        if path == "/api/public/v2/observations":
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            if self.v2_status:
                raise v.ApiError(self.v2_status, "only available in a Langfuse v4 write mode")
            return {"data": self.roots}
        if path == "/api/public/observations":
            assert params["fromStartTime"] and params["toStartTime"]
            if "traceId" in params:
                return {"data": [{"id": "o1"}], "meta": {"totalItems": 9}}
            return {"data": [dict(r, parentObservationId=None) for r in self.roots]
                    + [{"traceId": "t1", "startTime": NOW, "parentObservationId": "x"}]}
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        if path == "/api/public/v2/observations":
            return [{"id": "o1"}, {"id": "o2"}, {"id": "o3"}]
        if path == "/api/public/v3/scores":
            assert params["traceId"] == "t1"
            return [{"name": n} for n in self.scores]
        raise AssertionError(path)


def fake_clickhouse(rows=str(v.LAB_ROWS), ddl=PESSIMAL_DDL):
    def query(url, user, password, sql, timeout=v.TIMEOUT_S):
        assert user == "tuner_agent" and url == "http://localhost:8126"
        if sql.startswith("SELECT count()"):
            return rows + "\n"
        if sql.startswith("SHOW CREATE TABLE"):
            return ddl
        raise AssertionError(sql)
    return query


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=http://localhost:3001\n"
                   "ANTHROPIC_API_KEY=sk-ant-test\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.delenv("DEMO_LANGFUSE_PROJECT", raising=False)
    monkeypatch.delenv("CLICKHOUSE_TUNING_HTTP_PORT", raising=False)
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(FakeLangfuse, "project", "LLM Observability Demo")
    monkeypatch.setattr(FakeLangfuse, "calls", [])
    monkeypatch.setattr(v, "clickhouse_query", fake_clickhouse())
    return FakeLangfuse


def test_ready_against_fakes(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out
    assert "(Observations v2)" in out
    assert "http://localhost:3001/project/p1/traces/t1" in out      # newest run, not the first row
    assert "http://localhost:3001/project/p1/datasets/d1" in out
    assert "WARN  Act 4: Monitors not readable" in out              # v3 server: optional only
    assert not any("/traces" in c for c in fake.calls)              # no legacy trace reads


def test_v3_server_falls_back_to_v1_observations(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v2_status", 404)
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "Observations v1 (v2 is 404 on this v3 server)" in out
    assert "latest run has 9 observation(s)" in out


def test_other_v2_errors_do_not_fall_back(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "v2_status", 500)
    assert v.main([]) == 1
    assert "Langfuse read failed: HTTP 500" in capsys.readouterr().out


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_trace_score_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "scores", ["turns_used"])
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "missing run_cost_usd" in out
    assert "WARN  evaluator score goal_drift" in out


def test_production_label_off_v2_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "production_labels", ["production"])
    assert v.main([]) == 1
    assert "that version lacks v2-disciplined" in capsys.readouterr().out


def test_missing_dataset_item_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "items", [{"id": "qt-q1", "status": "ACTIVE"}, {"id": "qt-q2", "status": "ARCHIVED"}])
    assert v.main([]) == 1
    assert "missing qt-q2, qt-q3" in capsys.readouterr().out


def test_leftover_ddl_in_the_lab_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(v, "clickhouse_query", fake_clickhouse(ddl=PROJECTED_DDL))
    assert v.main([]) == 1
    assert "FAIL  lab table still pessimal" in capsys.readouterr().out


def test_half_seeded_lab_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(v, "clickhouse_query", fake_clickhouse(rows="1200000"))
    assert v.main([]) == 1
    assert "has 1200000 rows" in capsys.readouterr().out


def test_lab_down_is_not_ready_but_langfuse_is_still_checked(fake, monkeypatch, capsys):
    def down(*a, **k):
        raise v.ApiError(None, "URLError: connection refused")

    monkeypatch.setattr(v, "clickhouse_query", down)
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "docker compose --profile demo up -d clickhouse-tuning" in out
    assert "keys map to project 'LLM Observability Demo'" in out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    monkeypatch.setattr(v, "clickhouse_query", fake_clickhouse())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # conftest sets a dummy one
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  ANTHROPIC_API_KEY set" in out and "NOT READY" in out


def test_links_follow_the_act_order():
    links = v.act_links("http://lf", {"project_id": "p1", "trace_id": "t1", "dataset_id": "d1"})
    acts = [label.split(" ·")[0] for label, _ in links]
    assert acts == sorted(acts, key=lambda a: int(a.split()[1]))
    assert acts[0] == "Act 1" and acts[-1] == "Act 6"
    assert all(url.startswith("http://lf/project/p1/") for _, url in links)
