"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest and
project-name parse, the observation filter, and full runs against a fake Langfuse.
No network, no Langfuse, no model calls."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
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
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://shell.example")  # not in the file: must be dropped
    monkeypatch.setenv("UNRELATED", "kept")
    env = tmp_path / ".env"
    env.write_text('LANGFUSE_PUBLIC_KEY="pk-file"\nLANGFUSE_HOST="https://file.example"\n')
    assert v.load_env(env) is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-file"
    assert os.environ["LANGFUSE_HOST"] == "https://file.example"
    assert "LANGFUSE_BASE_URL" not in os.environ
    assert os.environ["UNRELATED"] == "kept"


def test_missing_env_file_still_drops_shell_langfuse_keys(tmp_path):
    environ = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "OTHER": "kept"}
    assert v.load_env(tmp_path / ".env", environ) is False
    assert environ == {"OTHER": "kept"}


def test_env_file_is_the_demos_own_for_a_standalone_demo():
    assert v.env_file("standalone") == v.DEMO_DIR / ".env"
    assert v.env_file("container") == v.REPO_ROOT / ".env"


def test_manifest_value_reads_nested_and_top_level_keys():
    text = ('name: x  # c\nprofile: standalone\nlangfuse:\n  target: cloud\n'
            '  project: "My Project"   # the real name\nlength:\n  full_min: 15\n')
    assert v.manifest_value(text, "langfuse", "project") == "My Project"
    assert v.manifest_value(text, "", "profile") == "standalone"
    assert v.manifest_value(text, "length", "project") is None


def test_own_manifest_names_the_project_and_profile():
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "langfuse", "project") == "brand-promo-demo"
    assert v.manifest_value(text, "", "profile") == "standalone"


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


# --- demo-specific logic -----------------------------------------------------------------

def test_expected_project_prefers_env_then_overlay_then_manifest(tmp_path, monkeypatch):
    manifest = "langfuse:\n  project: brand-promo-demo\n"
    monkeypatch.setattr(v, "DEMO_DIR", tmp_path)
    assert v.expected_project(manifest, {}) == "brand-promo-demo"
    (tmp_path / "demo.config.yaml").write_text(
        'customer:\n  display_name: "Acme"\ncatalog:\n  brand_families:\n    - name: "X"\n'
        'langfuse:\n  host: "http://localhost:3001"\n  project_name: "acme-demo"   # per customer\n')
    assert v.expected_project(manifest, {}) == "acme-demo"
    assert v.expected_project(manifest, {"DEMO_LANGFUSE_PROJECT": "other"}) == "other"


def test_expected_lists_match_the_seeders():
    """The names verify_demo expects are the names the seeders write."""
    text = (v.DEMO_DIR / "scripts" / "seed_prompts.py").read_text()
    assert sorted(v.EXPECTED_PROMPTS) == sorted(set(re.findall(r'"(promo-planner/[^"]+)"', text)))
    text = (v.DEMO_DIR / "scripts" / "setup_score_configs.py").read_text()
    assert sorted(v.EXPECTED_SCORE_CONFIGS) == sorted(re.findall(r'"name": "([^"]+)"', text))
    text = (v.DEMO_DIR / "scripts" / "seed_dashboards.py").read_text()
    assert all(f'"name": "{d}"' in text for d in v.DASHBOARDS)
    text = (v.DEMO_DIR / "scripts" / "run_experiment.py").read_text()
    assert f'DATASET_NAME = "{v.DATASET_NAME}"' in text and f'REVIEW_QUEUE_NAME = "{v.QUEUE_NAME}"' in text
    text = (v.DEMO_DIR / "demo.config.example.yaml").read_text()
    assert all(f'name: "{a}"' in text for a in v.FLEET_AGENTS)


def test_obs_filter_puts_trace_name_and_root_in_the_filter():
    f = json.loads(v.obs_filter(trace_name="PromoPlanner", root=True))
    assert {"type": "string", "column": "traceName", "operator": "=", "value": "PromoPlanner"} in f
    assert {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True} in f
    assert json.loads(v.obs_filter(trace_name="x")) == [
        {"type": "string", "column": "traceName", "operator": "=", "value": "x"}]


def test_paged_follows_total_pages():
    class Pages:
        def get(self, path, **params):
            return {"data": [{"n": params["page"]}], "meta": {"totalPages": 3}}

    assert [d["n"] for d in v.paged(Pages(), "/x")] == [1, 2, 3]


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows."""

    project = "unused"
    missing_prompt = None
    dataset_items = 75
    history = True
    dashboards = list(v.DASHBOARDS)
    live = [{"traceId": "t1", "startTime": NOW}]
    observation_types = ["CHAIN", "GENERATION", "GENERATION", "GENERATION", "AGENT"]
    queue_items = [{"id": "i1"}]

    def __init__(self, base, pk, sk):
        self.base = base

    def get(self, path, **params):
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            if self.missing_prompt and path.endswith(self.missing_prompt.replace("/", "%2F")):
                raise v.ApiError(404, "not found")
            return {"version": 1}
        if path == "/api/public/score-configs":
            return {"data": [{"name": n} for n in v.EXPECTED_SCORE_CONFIGS], "meta": {"totalPages": 1}}
        if path.startswith("/api/public/v2/datasets/"):
            return {"id": "d1", "name": v.DATASET_NAME}
        if path == "/api/public/dataset-items":
            return {"data": [], "meta": {"totalItems": self.dataset_items}}
        if path == "/api/public/v2/observations":
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            name = next(f["value"] for f in json.loads(params["filter"]) if f["column"] == "traceName")
            if name == v.TRACE_NAME:
                return {"data": self.live}
            return {"data": [{"traceId": f"h-{name}", "startTime": NOW}] if self.history else []}
        if path == "/api/public/unstable/dashboards":
            return {"data": [{"name": n} for n in self.dashboards], "meta": {"totalPages": 1}}
        if path == "/api/public/v2/evaluation-rules":
            return {"data": [{"name": "response-factuality", "enabled": True}]}
        if path == "/api/public/annotation-queues":
            return {"data": [{"id": "q1", "name": v.QUEUE_NAME}], "meta": {"totalPages": 1}}
        if path == "/api/public/annotation-queues/q1/items":
            return {"data": self.queue_items, "meta": {"totalPages": 1}}
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        if path == "/api/public/v2/observations":
            return [{"id": f"o{i}", "type": t} for i, t in enumerate(self.observation_types)]
        if path == "/api/public/experiments":
            return [{"name": "rehearsal"}]
        raise AssertionError(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('LANGFUSE_PUBLIC_KEY="pk"\nLANGFUSE_SECRET_KEY="sk"\nLANGFUSE_HOST="https://lf.example"\n')
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setenv("DEMO_LANGFUSE_PROJECT", "brand-promo-demo")
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(FakeLangfuse, "project", "brand-promo-demo")
    return FakeLangfuse


def test_ready_against_a_fake_langfuse(fake, capsys):
    assert v.main(["--links"]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "NOT READY" not in out and "WARN" not in out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert "https://lf.example/project/p1/traces/h-promo_planner_run" in out


def test_wrong_project_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "project", "someone-elses-project")
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_missing_prompt_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "missing_prompt", "promo-planner/judge/compliance-adherence")
    assert v.main([]) == 1
    assert "11/12 prompts" in capsys.readouterr().out


def test_short_dataset_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "dataset_items", 25)
    assert v.main([]) == 1
    assert "25 items (want 75+)" in capsys.readouterr().out


def test_aged_out_history_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "history", False)
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  seeded 'promo_planner_run' traces" in out and "0/5 fleet agents" in out


def test_missing_dashboard_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "dashboards", v.DASHBOARDS[:2])
    assert v.main([]) == 1
    assert "2/3 persona dashboards" in capsys.readouterr().out


def test_no_live_run_is_not_ready(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "live", [])
    assert v.main([]) == 1
    assert f"FAIL  a '{v.TRACE_NAME}' run" in capsys.readouterr().out


def test_thin_run_and_empty_queue_only_warn(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "observation_types", ["CHAIN", "CHAIN", "GENERATION"])
    monkeypatch.setattr(fake, "queue_items", [])
    assert v.main([]) == 0
    out = capsys.readouterr().out
    assert "READY (" in out and "2 warnings" in out


def test_no_keys_is_not_ready_without_any_request(tmp_path, monkeypatch, capsys):
    empty = tmp_path / ".env"
    empty.write_text("# nothing yet\n")
    monkeypatch.setattr(v, "env_file", lambda profile: empty)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    assert v.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_links_cover_every_act():
    state = {"project_id": "p1", "history_trace_id": "h1", "trace_id": "t1", "dataset_id": "d1", "queue_id": "q1"}
    links = v.act_links("https://lf.example", state)
    assert sorted({label.split(" ·")[0] for label, _ in links}) == [f"Act {n}" for n in range(1, 7)]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)
