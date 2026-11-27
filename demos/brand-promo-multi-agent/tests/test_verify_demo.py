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

EXAMPLE = (v.DEMO_DIR / "demo.config.example.yaml").read_text()
EXAMPLE_FLEET = ["CustomerCareBot", "SupplyChainPlanner", "ShelfImageAnalyzer", "InternalKBSearch", "FinanceCloseBot"]


def test_expected_project_prefers_env_then_overlay_then_manifest(tmp_path, monkeypatch):
    manifest = "langfuse:\n  project: brand-promo-demo\n"
    monkeypatch.setattr(v, "DEMO_DIR", tmp_path)
    assert v.expected_project(manifest, {}) == "brand-promo-demo"
    (tmp_path / "demo.config.yaml").write_text(
        'customer:\n  display_name: "Acme"\ncatalog:\n  brand_families:\n    - name: "X"\n'
        'langfuse:\n  host: "http://localhost:3001"\n  project_name: "acme-demo"   # per customer\n')
    assert v.expected_project(manifest, {}) == "acme-demo"
    assert v.expected_project(manifest, {"DEMO_LANGFUSE_PROJECT": "other"}) == "other"


def test_fleet_parse_matches_the_apps_config_loader(tmp_path):
    """The stdlib parse gives the same fleet as src.config.load_config, for the example and a re-theme."""
    from src.config import load_config

    assert v.fleet_agents(EXAMPLE) == [a.name for a in load_config(str(v.DEMO_DIR / "demo.config.example.yaml"))
                                       .agent_fleet.other_agents] == EXAMPLE_FLEET
    renamed = tmp_path / "demo.config.yaml"
    renamed.write_text(EXAMPLE.replace('name: "CustomerCareBot"', 'name: "CareDesk"  # renamed')
                       .replace("days_back: 30", "days_back: 14"))
    text = renamed.read_text()
    assert v.fleet_agents(text) == [a.name for a in load_config(str(renamed)).agent_fleet.other_agents]
    assert v.history_trace_names(v.fleet_agents(text))[0] == "caredesk_run"
    assert v.manifest_value(text, "synthetic_history", "days_back") == "14"


def test_demo_config_path_prefers_the_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "DEMO_DIR", tmp_path)
    assert v.demo_config_path() == tmp_path / "demo.config.example.yaml"
    (tmp_path / "demo.config.yaml").write_text("x: 1\n")
    assert v.demo_config_path() == tmp_path / "demo.config.yaml"


def test_expected_lists_match_the_seeders():
    """The names verify_demo expects are the names the seeders write."""
    text = (v.DEMO_DIR / "scripts" / "seed_prompts.py").read_text()
    assert sorted(v.EXPECTED_PROMPTS) == sorted(set(re.findall(r'"(promo-planner/[^"]+)"', text)))
    text = (v.DEMO_DIR / "scripts" / "setup_score_configs.py").read_text()
    assert sorted(v.EXPECTED_SCORE_CONFIGS) == sorted(re.findall(r'"name": "([^"]+)"', text))
    text = (v.DEMO_DIR / "scripts" / "seed_dashboards.py").read_text()
    assert all(f'"name": "{d}"' in text for d in v.DASHBOARDS)
    text = (v.DEMO_DIR / "scripts" / "seed_evaluators.py").read_text()
    assert all(f'"name": "{r}"' in text for r in v.JUDGE_RULES)
    text = (v.DEMO_DIR / "scripts" / "run_experiment.py").read_text()
    assert f'DATASET_NAME = "{v.DATASET_NAME}"' in text and f'REVIEW_QUEUE_NAME = "{v.QUEUE_NAME}"' in text
    assert f'label or "{v.BASELINE_LABEL}"' in text
    text = (v.DEMO_DIR / "scripts" / "seed_annotation_queue.py").read_text()
    assert f'QUEUE_NAME = "{v.QUEUE_NAME}"' in text


def test_obs_filter_puts_trace_name_and_root_in_the_filter():
    f = json.loads(v.obs_filter(trace_name="PromoPlanner", root=True))
    assert {"type": "string", "column": "traceName", "operator": "=", "value": "PromoPlanner"} in f
    assert {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True} in f


def test_paged_follows_total_pages_and_is_bounded():
    class Pages:
        def __init__(self, total):
            self.total = total

        def get(self, path, **params):
            return {"data": [{"n": params["page"]}], "meta": {"totalPages": self.total}}

    assert [d["n"] for d in v.paged(Pages(3), "/x")] == [1, 2, 3]
    with pytest.raises(v.ApiError):
        v.paged(Pages(10 ** 6), "/x")


OBS_FIELDS = {"core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"}
OBS_PARAMS = {"limit", "cursor", "fields", "name", "traceId", "type", "isRootObservation", "fromStartTime",
              "toStartTime", "filter"}
SCORE_FIELDS = {"details", "subject", "annotation"}
EXP_FIELDS = {"core", "metadata", "scores"}


class FakeLangfuse(v.Langfuse):
    """The real client with `get` answered from canned rows, so its cursor logic runs.
    Each endpoint rejects parameters and field groups the real API rejects, and returns
    only the field groups that were asked for."""

    project = "brand-promo-demo"
    projects = None                 # override the whole /projects list
    missing_prompt = None
    items = [{"status": "ACTIVE"}] * 75
    history = set(["promo_planner_run"] + [f"{a.lower()}_run" for a in EXAMPLE_FLEET])
    dashboards = list(v.DASHBOARDS)
    live = [{"id": "r1", "traceId": "t1", "startTime": NOW, "name": "PromoPlanner"}]
    observation_types = ["CHAIN", "GENERATION", "GENERATION", "GENERATION", "AGENT"]
    rules = [{"name": n, "enabled": True} for n in v.JUDGE_RULES]
    experiments = [{"id": "e1", "name": "rehearsal", "metadata": {"label": "baseline"}}]
    queues = [{"id": "q1", "name": v.QUEUE_NAME}]
    queue_items = [{"id": "i1", "status": "PENDING"}]
    fail = {}                       # path prefix -> ApiError
    page_size = 2

    def get(self, path, **params):
        for prefix, err in self.fail.items():
            if path.startswith(prefix):
                raise err
        if path == "/api/public/projects":
            return {"data": self.projects if self.projects is not None else [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            if self.missing_prompt and path.endswith(self.missing_prompt.replace("/", "%2F")):
                raise v.ApiError(404, "not found")
            assert params == {"label": "production"}
            return {"version": 1}
        if path == "/api/public/score-configs":
            return self._paged([{"name": n} for n in v.EXPECTED_SCORE_CONFIGS], params)
        if path.startswith("/api/public/v2/datasets/"):
            return {"id": "d1", "name": v.DATASET_NAME}
        if path == "/api/public/dataset-items":
            assert params["datasetName"] == v.DATASET_NAME
            return self._paged(list(self.items), params)
        if path == "/api/public/v2/observations":
            return self._observations(params)
        if path == "/api/public/unstable/dashboards":
            return self._paged([{"name": n} for n in self.dashboards], params)
        if path == "/api/public/v2/evaluation-rules":
            return self._cursor(list(self.rules), params)
        if path == "/api/public/experiments":
            return self._experiments(params)
        if path == "/api/public/annotation-queues":
            return self._paged(list(self.queues), params)
        if path == "/api/public/annotation-queues/q1/items":
            rows = [i for i in self.queue_items if not params.get("status") or i["status"] == params["status"]]
            return self._paged(rows, params)
        raise AssertionError(path)

    def _paged(self, rows, params):
        """Honours limit, capped at 50 rows so 75 dataset items still span two pages."""
        page, size = int(params.get("page", 1)), min(int(params.get("limit", 50)), 50)
        return {"data": rows[(page - 1) * size: page * size],
                "meta": {"page": page, "totalPages": max(1, -(-len(rows) // size))}}

    def _cursor(self, rows, params):
        """Pages of page_size with an empty page (still carrying a cursor) between them."""
        cur = int(params.get("cursor") or 0)
        if cur < 0:
            return {"data": [], "meta": {"cursor": str(-cur)}}
        nxt = cur + self.page_size
        return {"data": rows[cur:nxt], "meta": {"cursor": str(-nxt) if nxt < len(rows) else None}}

    def _observations(self, params):
        if set(params) - OBS_PARAMS:
            raise v.ApiError(400, f"unknown parameters {set(params) - OBS_PARAMS}")
        groups = set(params.get("fields", "core,basic").split(","))
        if groups - OBS_FIELDS:
            raise v.ApiError(400, "unknown field group")
        assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
        if "traceId" in params:
            rows = [{"id": f"o{i}", "traceId": params["traceId"], "type": t, "name": f"n{i}", "startTime": NOW}
                    for i, t in enumerate(self.observation_types)]
        else:
            name = next(f["value"] for f in json.loads(params["filter"]) if f["column"] == "traceName")
            if name == v.TRACE_NAME:
                rows = list(self.live)
            else:
                rows = [{"id": "h", "traceId": f"h-{name}", "startTime": NOW, "name": "x"}] if name in self.history else []
        out = []
        for r in rows:
            o = {k: r[k] for k in ("id", "traceId", "startTime", "type") if k in r}
            if "basic" in groups:
                o["name"] = r["name"]
            out.append(o)
        return self._cursor(out, params)

    def _experiments(self, params):
        if not params.get("fromStartTime"):
            raise v.ApiError(400, "fromStartTime is required")
        groups = set(params.get("fields", "core").split(","))
        if groups - EXP_FIELDS:
            raise v.ApiError(400, "unknown field group")
        rows = [{k: e[k] for k in e if k != "metadata" or "metadata" in groups} for e in self.experiments]
        return self._cursor(rows, params)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('LANGFUSE_PUBLIC_KEY="pk"\nLANGFUSE_SECRET_KEY="sk"\nLANGFUSE_HOST="https://lf.example"\n')
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.delenv("DEMO_LANGFUSE_PROJECT", raising=False)
    monkeypatch.setattr(v, "demo_config_path", lambda: v.DEMO_DIR / "demo.config.example.yaml")
    monkeypatch.setattr(v, "expected_project", lambda manifest: "brand-promo-demo")
    monkeypatch.setattr(v, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(FakeLangfuse, "fail", {})
    return FakeLangfuse


def run(argv, capsys):
    code = v.main(argv)
    return code, capsys.readouterr().out


def test_ready_against_a_fake_langfuse(fake, capsys):
    code, out = run(["--links"], capsys)
    assert code == 0 and "READY (" in out and "NOT READY" not in out and "WARN" not in out
    assert "https://lf.example/project/p1/traces/t1" in out
    assert "https://lf.example/project/p1/traces/h-promo_planner_run" in out
    assert "https://lf.example/project/p1/annotation-queues/q1" in out


@pytest.mark.parametrize("setup, expect", [
    (lambda f, m: m.setattr(f, "project", "someone-elses-project"), "keys map to project 'someone-elses-project'"),
    (lambda f, m: m.setattr(f, "projects", []), "keys map to project 'None'"),
    (lambda f, m: m.setattr(f, "missing_prompt", "promo-planner/judge/compliance-adherence"), "11/12 prompts"),
    (lambda f, m: m.setattr(f, "items", [{"status": "ACTIVE"}] * 25), "25 ACTIVE item(s) of 25"),
    (lambda f, m: m.setattr(f, "items", [{"status": "ARCHIVED"}] * 75), "0 ACTIVE item(s) of 75"),
    (lambda f, m: m.setattr(f, "history", set()), "FAIL  seeded 'promo_planner_run' traces"),
    (lambda f, m: m.setattr(f, "history", {"promo_planner_run", "customercarebot_run"}), "1/5 fleet agents"),
    (lambda f, m: m.setattr(f, "dashboards", v.DASHBOARDS[:2]), "2/3 persona dashboards"),
    (lambda f, m: m.setattr(f, "live", []), "FAIL  a 'PromoPlanner' run"),
    (lambda f, m: m.setattr(f, "rules", [{"name": n, "enabled": n != "response-factuality"} for n in v.JUDGE_RULES]),
     "2/3 online judges enabled"),
    (lambda f, m: m.setattr(f, "experiments", []), "0 'baseline' experiment run(s) of 0"),
    (lambda f, m: m.setattr(f, "experiments", [{"id": "e", "name": "v2", "metadata": {"label": "strategy-v2"}}]),
     "0 'baseline' experiment run(s) of 1"),
    (lambda f, m: m.setattr(f, "queues", []), "FAIL  annotation queue 'PromoPlanner Human Review' exists"),
    (lambda f, m: m.setattr(f, "queue_items", [{"id": "i", "status": "COMPLETED"}]), "0 PENDING item(s)"),
    (lambda f, m: m.setattr(f, "fail", {"/api/public/score-configs": v.ApiError(500, "boom")}), "Langfuse read failed"),
    (lambda f, m: m.setattr(f, "fail", {"/api/public/experiments": v.ApiError(None, "timeout")}),
     "experiment runs readable"),
    (lambda f, m: m.setattr(f, "fail", {"/api/public/unstable/dashboards": v.ApiError(404, "nf")}),
     "dashboards readable"),
])
def test_each_missing_asset_is_not_ready(fake, monkeypatch, capsys, setup, expect):
    setup(fake, monkeypatch)
    code, out = run([], capsys)
    assert code == 1 and "NOT READY" in out
    assert expect in out


def test_short_path_skips_acts_4_to_6_and_says_so(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "experiments", [])
    monkeypatch.setattr(fake, "queues", [])
    monkeypatch.setattr(fake, "rules", [])
    code, out = run(["--short-path"], capsys)
    assert code == 0 and out.rstrip().endswith("for the short path (Acts 1-3)")
    assert "SKIP" in out
    code, out = run([], capsys)
    assert code == 1  # the same project is not ready for the full script


def test_short_path_still_needs_acts_1_to_3(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "live", [])
    code, out = run(["--short-path"], capsys)
    assert code == 1 and "NOT READY" in out


def test_renamed_fleet_is_what_verify_expects(fake, tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "demo.config.yaml"
    cfg.write_text(EXAMPLE.replace('name: "CustomerCareBot"', 'name: "CareDesk"'))
    monkeypatch.setattr(v, "demo_config_path", lambda: cfg)
    code, out = run([], capsys)  # the fake's history still has customercarebot_run, not caredesk_run
    assert code == 1 and "missing caredesk_run" in out
    monkeypatch.setattr(fake, "history", fake.history | {"caredesk_run"})
    code, out = run([], capsys)
    assert code == 0


def test_config_without_a_fleet_fails_before_any_request(fake, tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "demo.config.yaml"
    cfg.write_text("customer:\n  display_name: x\n")
    monkeypatch.setattr(v, "demo_config_path", lambda: cfg)
    monkeypatch.setattr(v, "Langfuse", None)  # any request would raise
    code, out = run([], capsys)
    assert code == 1 and "0 fleet agent(s)" in out


def test_thin_run_only_warns(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "observation_types", ["CHAIN", "CHAIN", "GENERATION"])
    code, out = run([], capsys)
    assert code == 0 and "1 warning" in out


def test_repeated_cursor_is_a_failure_not_a_hang(fake, monkeypatch, capsys):
    def loop(self, rows, params):
        return {"data": [], "meta": {"cursor": "same"}}

    monkeypatch.setattr(fake, "_cursor", loop)
    code, out = run([], capsys)
    assert code == 1 and "repeated a cursor" in out


def test_missing_env_with_hostile_shell_exports_is_not_ready_without_a_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-other")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-other")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://other.example")
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "missing.env")
    monkeypatch.setattr(v, "Langfuse", None)
    code, out = run([], capsys)
    assert code == 1 and "FAIL  env loaded" in out and "NOT READY" in out


def test_partial_env_is_not_ready_without_a_request(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text('LANGFUSE_PUBLIC_KEY="pk"\nLANGFUSE_SECRET_KEY="sk"\n')  # no host
    monkeypatch.setenv("LANGFUSE_HOST", "https://shell.example")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "Langfuse", None)
    code, out = run([], capsys)
    assert code == 1 and "FAIL  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST set" in out


def test_request_builds_query_strings(monkeypatch):
    seen = {}

    class Resp:
        def read(self):
            return b'{"data": []}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(v.urllib.request, "urlopen", lambda req, timeout: seen.update(url=req.full_url) or Resp())
    v.Langfuse("https://lf.example/", "pk", "sk").get("/api/public/v2/observations", isRootObservation=True,
                                                         filter=v.obs_filter(trace_name="PromoPlanner"), cursor=None)
    assert seen["url"].startswith("https://lf.example/api/public/v2/observations?")
    assert "isRootObservation=true" in seen["url"] and "cursor=" not in seen["url"]
    assert "%22traceName%22" in seen["url"]


def test_links_cover_every_act():
    state = {"project_id": "p1", "history_trace_id": "h1", "trace_id": "t1", "dataset_id": "d1", "queue_id": "q1"}
    links = v.act_links("https://lf.example", state)
    assert sorted({label.split(" ·")[0] for label, _ in links}) == [f"Act {n}" for n in range(1, 7)]
    assert all(url.startswith("https://lf.example/project/p1/") for _, url in links)


# --- failure boundary: the real main() path, no stubbed client --------------------------

@pytest.fixture
def demo_copy(tmp_path, monkeypatch):
    """A copy of this demo's manifest and config in tmp_path, with main() reading it."""
    for name in ("demo.yaml", "demo.config.example.yaml"):
        (tmp_path / name).write_bytes((v.DEMO_DIR / name).read_bytes())
    monkeypatch.setattr(v, "DEMO_DIR", tmp_path)
    opened = []

    def urlopen(req, timeout):
        opened.append(req.full_url)
        raise AssertionError("no request expected")

    monkeypatch.setattr(v.urllib.request, "urlopen", urlopen)
    return tmp_path, opened


ENV_OK = 'LANGFUSE_PUBLIC_KEY="pk-lf-x"\nLANGFUSE_SECRET_KEY="sk-lf-x"\n'


def _verdict(out):
    last = out.strip().splitlines()[-1]
    assert last.startswith("NOT READY (") and "required failed" in last, last
    return last


def test_malformed_local_url_is_a_failed_check_and_a_counted_verdict(demo_copy, capsys):
    """132-8: http://[::gg] used to raise ValueError while the request was being built."""
    tmp, opened = demo_copy
    (tmp / ".env").write_text(ENV_OK + "LANGFUSE_BASE_URL=http://[::gg]\n")
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  LANGFUSE_HOST unusable: 'http://[::gg]'" in out
    _verdict(out)
    assert opened == []  # rejected before any request
    assert [s for s, _, w in v.RESULTS if w.startswith("env loaded")] == ["PASS"]  # earlier results kept


@pytest.mark.parametrize("url", ["http://[::gg]", "http://host:99999", "ftp://lf.example", "lf.example:3000"])
def test_base_url_problem_names_each_bad_url(url):
    assert v.base_url_problem(url)
    assert v.base_url_problem("https://us.cloud.langfuse.com") is None
    assert v.base_url_problem("http://[::1]:3001") is None


def test_request_construction_errors_are_api_errors(monkeypatch):
    """Inside the client too: a URL that slips past validation still fails as an ApiError."""
    monkeypatch.setattr(v.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(
        ValueError("unknown url type")))
    with pytest.raises(v.ApiError, match="invalid request URL"):
        v.Langfuse("http://[::gg]", "pk", "sk").get("/api/public/projects")
    with pytest.raises(v.ApiError, match="invalid request URL"):
        v.Langfuse("https://lf.example", "pk", "sk").get("/api/public/projects")


@pytest.mark.parametrize("unreadable", [".env", "demo.config.example.yaml", "demo.yaml"])
def test_unreadable_local_configuration_is_not_ready(demo_copy, capsys, unreadable):
    tmp, opened = demo_copy
    (tmp / ".env").write_text(ENV_OK + "LANGFUSE_BASE_URL=https://lf.example\n")
    (tmp / unreadable).write_bytes(b"\xff\xfe not utf-8 \xff")
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "unreadable" in out and "UnicodeDecodeError" in out
    assert "unexpected error" not in out and opened == []  # failed config: no request at all
    _verdict(out)


def test_an_unexpected_error_mid_run_keeps_results_and_reaches_the_verdict(demo_copy, monkeypatch, capsys):
    tmp, _ = demo_copy
    (tmp / ".env").write_text(ENV_OK + "LANGFUSE_BASE_URL=https://lf.example\n")

    def explode(req, timeout):
        raise RuntimeError("socket layer exploded")

    monkeypatch.setattr(v.urllib.request, "urlopen", explode)
    assert v.main(["--short-path"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  verification stopped by an unexpected error: RuntimeError: socket layer exploded" in out
    assert _verdict(out).endswith("for the short path (Acts 1-3)")
    assert sum(1 for s, _, _ in v.RESULTS if s == "PASS") >= 3  # the config checks before it stand


def test_unexpected_response_shape_is_a_failed_check(demo_copy, monkeypatch, capsys):
    tmp, _ = demo_copy
    (tmp / ".env").write_text(ENV_OK + "LANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v.Langfuse, "request", lambda self, m, p, params=None, body=None: ["not", "an", "object"])
    assert v.main([]) == 1
    out = capsys.readouterr().out
    assert "AttributeError" in out
    _verdict(out)


def test_interrupts_and_usage_are_not_swallowed(demo_copy, monkeypatch, capsys):
    tmp, _ = demo_copy
    (tmp / ".env").write_text(ENV_OK + "LANGFUSE_BASE_URL=https://lf.example\n")

    def interrupt(req, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(v.urllib.request, "urlopen", interrupt)
    with pytest.raises(KeyboardInterrupt):
        v.main([])
    with pytest.raises(SystemExit) as e:
        v.main(["--help"])
    assert e.value.code == 0 and "--short-path" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        v.main(["--no-such-flag"])
    assert e.value.code == 2
