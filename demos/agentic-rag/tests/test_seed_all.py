"""seed_all.py offline: the prompt reconcile decision against a fake Langfuse, the step
wrapper, and that verify expects what seed creates. No network, no Docker, no model calls."""
from __future__ import annotations

import pytest

import seed_all as s
import verify_demo as v

DEFS = s.prompt_definitions()
CONFIG = {"model": "claude-sonnet-4-6", "temperature": 0.3}


class FakeLangfuse:
    def __init__(self, served=None, exists=None, status=404):
        self.served, self.status, self.posts = served, status, []
        self.exists = served is not None if exists is None else exists

    def get(self, path, **params):
        if path == "/api/public/v2/prompts":  # list by name
            return {"data": [{"name": params["name"]}] if self.exists else []}
        if self.served is None:
            raise v.ApiError(self.status, "not found")
        return self.served

    def post(self, path, body):
        self.posts.append(body)


def served(**changes):
    row = {"type": "text", "prompt": DEFS.V2, "config": dict(CONFIG), "version": 2}
    row.update(changes)
    return row


def reconcile(lf, **kw):
    return s.reconcile_prompt(lf, DEFS.NAME, DEFS.V1, DEFS.V2, CONFIG, **kw)


def test_missing_prompt_gets_baseline_then_production():
    lf = FakeLangfuse()
    assert reconcile(lf) == "created"
    assert [p["labels"] for p in lf.posts] == [[], ["production"]]
    assert [p["prompt"] for p in lf.posts] == [DEFS.V1, DEFS.V2]


def test_identical_prompt_is_left_unchanged():
    lf = FakeLangfuse(served())
    assert reconcile(lf) == "unchanged"
    assert lf.posts == []


@pytest.mark.parametrize("change", [{"prompt": "edited in the UI"}, {"config": {"model": "other"}}])
def test_drift_adds_one_production_version(change):
    lf = FakeLangfuse(served(**change))
    assert reconcile(lf) == "updated"
    assert [p["labels"] for p in lf.posts] == [["production"]]


def test_existing_prompt_without_the_label_gets_no_second_baseline():
    lf = FakeLangfuse(served=None, exists=True)
    assert reconcile(lf) == "updated"
    assert [p["prompt"] for p in lf.posts] == [DEFS.V2]


def test_dry_run_writes_nothing():
    lf = FakeLangfuse(served(prompt="drifted"))
    assert reconcile(lf, dry_run=True) == "updated"
    assert lf.posts == []


def test_other_errors_propagate():
    with pytest.raises(v.ApiError):
        reconcile(FakeLangfuse(status=500))


def test_dry_run_step_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(s.subprocess, "run", None)  # any call would raise
    assert s.run_step(["./scripts/seed-agentic-rag-evaluators.sh"], dry_run=True) == "skipped"
    assert "would run" in capsys.readouterr().out


def test_step_result_follows_the_exit_code(monkeypatch):
    class Done:
        def __init__(self, code):
            self.returncode = code

    monkeypatch.setattr(s.subprocess, "run", lambda cmd, cwd: Done(0))
    assert s.run_step(["true"], dry_run=False) == "reconciled"
    monkeypatch.setattr(s.subprocess, "run", lambda cmd, cwd: Done(1))
    assert s.run_step(["false"], dry_run=False) == "failed"


def test_wrapped_seeders_exist():
    for cmd in (s.JUDGES_CMD, s.LIBRECHAT_CMD):
        assert (v.REPO_ROOT / cmd[0]).is_file()
    assert (v.DEMO_DIR / s.INGEST_CMD[-1]).is_file()
    judges = (v.REPO_ROOT / s.JUDGES_CMD[0]).read_text()
    for rule in v.JUDGE_RULES:  # verify reads back the ids the judge seeder upserts
        assert f'"{rule}"' in judges


def test_verify_expects_what_seed_creates(monkeypatch):
    monkeypatch.delenv("GEN_PROMPT_NAME", raising=False)
    assert {s.prompt_definitions().NAME: s.PRODUCTION} == v.EXPECTED_PROMPTS
    graph_src = (v.DEMO_DIR / "graph.py").read_text()
    assert '"agentic-rag-generation"' in graph_src and 'lf.trace_context("agentic-rag"' in graph_src
    for name in v.EXPECTED_OBSERVATIONS:
        assert f'"{name}"' in graph_src


# --- Full runs of main() against the fake server ------------------------------------------

from fake_langfuse import LF_BASE, FakeServer  # noqa: E402

ENV_TEXT = f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={LF_BASE}\nDEPLOY_MODE=self-hosted\n"


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A fake stack where the prompt is already seeded and every wrapped seeder succeeds."""
    srv = FakeServer()
    srv.seed_prompt(DEFS.V2, DEFS.CONFIG)
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-other-project")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://hostile.example")
    for k in ("DEPLOY_MODE", "GEN_PROMPT_NAME", "CLICKHOUSE_VECTORS_HTTP_PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v.urllib.request, "urlopen", srv.urlopen)
    srv.exit_codes = {}
    srv.on_ingest = None

    def run(cmd, cwd):
        srv.commands.append(cmd)
        assert cwd == v.REPO_ROOT
        if cmd == s.INGEST_CMD and srv.on_ingest:
            srv.on_ingest()
        return type("Done", (), {"returncode": srv.exit_codes.get(cmd[0], 0)})()

    monkeypatch.setattr(s.subprocess, "run", run)
    srv.env = env
    return srv


def seed(capsys, *argv):
    code = s.main(list(argv))
    return code, capsys.readouterr().out


def test_unchanged_reseed_writes_nothing(seeded, capsys):
    versions = len(seeded.prompt_versions)
    code, out = seed(capsys)
    assert code == 0 and seeded.posts == [] and len(seeded.prompt_versions) == versions
    assert "unchanged  agentic_rag.kb_chunks: 28 -> 28" in out
    assert "unchanged  prompt agentic-rag-generation" in out
    assert [c[0] for c in seeded.commands] == ["docker", *s.JUDGES_CMD, *s.LIBRECHAT_CMD]


def test_fresh_project_gets_baseline_and_production(seeded, capsys):
    seeded.prompt_versions = []
    seeded.ch_count = None  # no table yet

    def ingest():
        seeded.ch_count = 28

    seeded.on_ingest = ingest
    code, out = seed(capsys)
    assert code == 0, out
    assert "created    agentic_rag.kb_chunks: 0 -> 28" in out and "created    prompt" in out
    assert [p["labels"] for p in seeded.prompt_versions] == [[], ["production"]]


def test_config_only_drift_adds_one_production_version(seeded, capsys):
    seeded.prompt_versions[-1]["config"] = {"model": "claude-old", "temperature": 0.3}
    code, out = seed(capsys)
    assert code == 0 and "updated    prompt" in out
    assert len(seeded.posts) == 1 and seeded.posts[0]["config"] == DEFS.CONFIG
    assert seeded.prompt_versions[-1]["labels"] == ["production"]
    assert all("production" not in p["labels"] for p in seeded.prompt_versions[:-1])


def test_changed_corpus_reports_updated(seeded, capsys):
    def ingest():
        seeded.ch_fingerprint = "999"

    seeded.on_ingest = ingest
    code, out = seed(capsys)
    assert code == 0 and "updated    agentic_rag.kb_chunks" in out


def test_partial_prompt_write_failure_fails(seeded, capsys):
    seeded.prompt_versions = []
    seeded.post_status = [200, 500]  # the baseline lands, the production version does not
    code, out = seed(capsys, "--skip-ingest")
    assert code == 1 and "failed     prompt" in out and "HTTP 500" in out


def test_total_prompt_write_failure_fails(seeded, capsys):
    seeded.prompt_versions[-1]["prompt"] = "drifted"
    seeded.post_status = [503]
    code, out = seed(capsys, "--skip-ingest")
    assert code == 1 and "failed     prompt" in out


def test_prompt_read_failure_fails(seeded, capsys):
    seeded.fail["/api/public/v2/prompts/agentic-rag-generation"] = 500
    code, out = seed(capsys, "--skip-ingest")
    assert code == 1 and "failed     prompt" in out


@pytest.mark.parametrize("tool", [s.JUDGES_CMD[0], s.LIBRECHAT_CMD[0], "docker"])
def test_a_failed_wrapped_seeder_fails_the_run(seeded, capsys, tool):
    seeded.exit_codes[tool] = 1
    code, out = seed(capsys)
    assert code == 1 and "failed" in out


def test_ingest_that_leaves_an_empty_table_fails(seeded, capsys):
    def ingest():
        seeded.ch_count = 0

    seeded.on_ingest = ingest
    code, out = seed(capsys)
    assert code == 1 and "failed     agentic_rag.kb_chunks" in out


def test_dry_run_runs_and_writes_nothing(seeded, capsys):
    seeded.prompt_versions[-1]["prompt"] = "drifted"
    code, out = seed(capsys, "--dry-run")
    assert code == 0 and seeded.posts == [] and seeded.commands == []
    assert "updated    prompt" in out and out.count("would run") == 3


def test_cloud_mode_skips_the_postgres_judge_seeder(seeded, capsys):
    seeded.env.write_text(ENV_TEXT.replace("self-hosted", "cloud"))
    code, out = seed(capsys, "--skip-ingest", "--skip-librechat")
    assert code == 0 and seeded.commands == [] and "judges: DEPLOY_MODE=cloud" in out


@pytest.mark.parametrize("text", ["", "LANGFUSE_PUBLIC_KEY=pk\n"])
def test_missing_or_partial_env_fails_before_any_write(seeded, capsys, text):
    seeded.env.write_text(text)
    code, _ = seed(capsys)
    assert code == 1 and seeded.requests == [] and seeded.commands == []


def test_ingest_result_cases():
    assert s.ingest_result("failed", None, None) == "failed"
    assert s.ingest_result("skipped", (28, "a"), (28, "a")) == "skipped"
    assert s.ingest_result("reconciled", None, (28, "a")) == "created"
    assert s.ingest_result("reconciled", (28, "a"), (28, "a")) == "unchanged"
    assert s.ingest_result("reconciled", (28, "a"), (30, "b")) == "updated"
    assert s.ingest_result("reconciled", (28, "a"), None) == "failed"
