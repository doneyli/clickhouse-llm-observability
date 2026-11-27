"""seed_all.py offline, against the HTTP-level fake Langfuse (fake_langfuse.py): unchanged
reseeding writes nothing, every kind of prompt drift is found and verified after the
seeder runs, dataset items reconcile in place (ACTIVE, by id or question), and partial,
total and read failures reach the exit code. The subprocess seeders are replaced by a fake
runner; no network, no subprocesses, no Langfuse, no model calls."""
from __future__ import annotations

import pytest

import seed_all as s
import verify_demo as v
from fake_langfuse import FakeServer, ready_server

PROMPT_MOD = s.load_module("seed_app_prompts", s.PROMPT_SEEDER)
SPECS = s.prompt_specs(PROMPT_MOD)


class Runner:
    """Stands in for the subprocess seeders. `fixes_prompts` makes the prompt seeder write
    every drifted definition, as a correct seeder does; otherwise it writes nothing."""

    def __init__(self, lf, fixes_prompts=True, fail=()):
        self.lf, self.fixes, self.fail, self.cmds = lf, fixes_prompts, fail, []

    def __call__(self, cmd, env):
        name = cmd[-1].rsplit("/", 1)[-1]
        self.cmds.append(name)
        if name in self.fail:
            return 1
        if name == "seed-app-prompts.py" and self.fixes:
            for spec, drift in s.prompt_plan(self.lf, SPECS):
                if drift:
                    self.lf.post("/api/public/v2/prompts", {
                        "name": spec["name"], "type": spec["type"], "prompt": spec["prompt"],
                        "config": spec["config"], "labels": [spec["label"]]})
        return 0


@pytest.fixture
def server(monkeypatch):
    return ready_server(monkeypatch)


@pytest.fixture
def lf():
    return v.Langfuse("http://lf.test", "pk", "sk")


def seed(lf, runner, dry_run=False):
    return s.seed(lf, {}, dry_run, runner=runner, prompt_module=PROMPT_MOD)


def lines(capsys):
    return capsys.readouterr().out


# --- Unchanged reseeding --------------------------------------------------------------------

def test_unchanged_reseed_writes_nothing(server, lf, capsys):
    runner = Runner(lf)
    t = seed(lf, runner)
    assert server.writes() == []                                  # no version, no item, no dataset write
    assert runner.cmds == ["seed-code-evaluators.sh"]             # the prompt seeder is not even started
    assert t.counts["created"] == t.counts["updated"] == t.counts["failed"] == 0
    assert t.counts["unchanged"] == len(SPECS) + 3 + 8 + 10 + 6   # prompts, 3 datasets, their items


def test_fresh_project_creates_everything_and_a_rerun_is_unchanged(monkeypatch, lf):
    server = FakeServer()
    monkeypatch.setattr(v.urllib.request, "urlopen", server.urlopen)
    t = seed(lf, Runner(lf))
    assert t.counts["failed"] == 0
    assert t.counts["created"] == len(SPECS) + 3 + 8 + 10 + 6
    server.requests.clear()
    again = seed(lf, Runner(lf))
    assert server.writes() == [] and again.counts["created"] == again.counts["updated"] == 0


# --- Prompt drift: type, text, config, label --------------------------------------------------

def served(server, name, label):
    return next(p for p in server.prompts[name] if label in p["labels"])


@pytest.mark.parametrize("field,change", [
    ("config", {"model": "claude-old", "temperature": 0.7}),
    ("text", "edited in the UI"),
    ("type", "chat"),
])
def test_drift_in_any_field_is_updated_and_verified(server, lf, capsys, field, change):
    key = {"text": "prompt"}.get(field, field)
    served(server, "text-to-sql-critic", "production")[key] = change
    t = seed(lf, Runner(lf))
    out = lines(capsys)
    assert t.counts["updated"] == 1 and t.counts["failed"] == 0
    assert f"updated    prompt text-to-sql-critic (label production): {field}" in out
    assert s.prompt_plan(lf, SPECS) == [(sp, []) for sp in SPECS]


def test_config_drift_the_seeder_leaves_behind_is_a_failure_not_an_update(server, lf, capsys):
    """A text-only seeder would say "up to date" and change nothing; that must fail."""
    served(server, "text-to-sql-gate-grounding", "production")["config"] = {"model": "x"}
    t = seed(lf, Runner(lf, fixes_prompts=False))
    out = lines(capsys)
    assert t.counts["failed"] == 1 and t.counts["updated"] == 0
    assert "config still differ after scripts/seed-app-prompts.py" in out


def test_a_missing_label_is_added_and_other_labels_survive(server, lf, capsys):
    critic = server.prompts["text-to-sql-critic"]
    for p in critic:
        p["labels"] = [x for x in p["labels"] if x != "opinion-only"]
    critic[0]["labels"].append("my-experiment")                  # a user's own label
    t = seed(lf, Runner(lf))
    assert (t.counts["updated"], t.counts["created"]) == (1, 0)
    assert "updated    prompt text-to-sql-critic (label opinion-only): label added" in lines(capsys)
    assert any("my-experiment" in p["labels"] for p in critic)
    assert any("opinion-only" in p["labels"] for p in critic)


def test_a_prompt_read_error_fails_the_step_and_later_steps_still_run(server, lf, capsys):
    server.fail[("GET", "/api/public/v2/prompts/")] = 500
    runner = Runner(lf)
    t = seed(lf, runner)
    out = lines(capsys)
    assert t.counts["failed"] == 1 and "Managed prompts (scripts/seed-app-prompts.py): ApiError: HTTP 500" in out
    assert "seed-code-evaluators.sh" in runner.cmds
    assert t.counts["unchanged"] == 3 + 8 + 10 + 6


def test_a_failing_seeder_exit_code_is_reported(server, lf):
    served(server, "text-to-sql-analysis", "candidate")["prompt"] = "drifted"
    t = seed(lf, Runner(lf, fixes_prompts=False, fail={"seed-app-prompts.py", "seed-code-evaluators.sh"}))
    assert t.counts["failed"] == 3   # the prompt seeder, the prompt it left behind, the evaluator seeder


# --- Dataset items ------------------------------------------------------------------------------

def test_archived_items_come_back_active_without_duplicates(server, lf):
    rows = server.items["text-to-sql-analysis-step"]
    for row in rows:
        row["status"] = "ARCHIVED"
    t = seed(lf, Runner(lf))
    assert t.counts["updated"] == 8 and len(rows) == 8
    assert {r["status"] for r in rows} == {"ACTIVE"}


def test_changed_and_missing_items_reconcile_in_place(server, lf):
    rows = server.items["text-to-sql/critic-accuracy"]
    rows[0]["expectedOutput"] = {"verdict": "accept"}
    del rows[1]
    t = seed(lf, Runner(lf))
    assert (t.counts["updated"], t.counts["created"]) == (1, 1)
    assert sorted(r["id"] for r in rows) == [f"crit-{i:02d}" for i in range(1, 11)]


def test_unrelated_and_linked_items_are_left_alone(server, lf):
    step = server.items["text-to-sql-analysis-step"]
    first_q = step[0]["input"]["question"]
    step.append({"id": "mine", "input": {"question": "my own"}, "expectedOutput": {}, "status": "ACTIVE"})
    step.append({"id": "link", "input": {"question": first_q}, "expectedOutput": {"databases": []},
                 "metadata": {"linked_from": "trace"}, "status": "ACTIVE"})
    t = seed(lf, Runner(lf))
    assert server.writes() == [] and t.counts["failed"] == 0
    assert len(step) == 10


def test_partial_item_write_failure_reports_each_item(server, lf, capsys):
    rows = server.items["text-to-sql/converged-sql"]
    for row in rows:
        row["metadata"] = {"stale": True}
    server.fail[("POST", "/api/public/dataset-items")] = [None, 500, None, 500, None, None]
    t = seed(lf, Runner(lf))
    out = lines(capsys)
    assert (t.counts["updated"], t.counts["failed"]) == (4, 2)
    assert "failed     item conv-02 in text-to-sql/converged-sql: HTTP 500" in out


def test_total_write_failure_fails_every_item(monkeypatch, lf):
    server = FakeServer()
    monkeypatch.setattr(v.urllib.request, "urlopen", server.urlopen)
    server.fail[("POST", "/api/public/")] = 503
    t = seed(lf, Runner(lf, fixes_prompts=False))
    assert t.counts["created"] == 0 and t.counts["failed"] >= 3


def test_dry_run_writes_nothing_and_runs_nothing(monkeypatch, lf):
    server = FakeServer()
    monkeypatch.setattr(v.urllib.request, "urlopen", server.urlopen)
    runner = Runner(lf)
    t = seed(lf, runner, dry_run=True)
    assert runner.cmds == [] and server.writes() == []
    assert t.counts["failed"] == 0 and t.counts["created"] == len(SPECS) + 3 + 8 + 10 + 6


# --- Environment ----------------------------------------------------------------------------------

def test_seeders_get_one_url_under_both_names(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://stale.example")
    env = s.child_env("http://localhost:3001")
    assert env["LANGFUSE_HOST"] == env["LANGFUSE_BASE_URL"] == "http://localhost:3001"


def test_no_env_file_fails_before_any_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / ".env")
    monkeypatch.setattr(v, "in_container", lambda: False)
    monkeypatch.setattr(v, "Langfuse", None)
    assert s.main([]) == 1
    assert "no env file" in capsys.readouterr().err


def test_a_malformed_base_url_fails_before_any_request(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=http://[::gg]\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "in_container", lambda: False)
    monkeypatch.setattr(v, "Langfuse", None)
    assert s.main([]) == 1
    assert "is not a valid URL" in capsys.readouterr().err


def test_the_prompt_seeder_covers_every_text_to_sql_prompt_the_app_fetches():
    names = {sp["name"] for sp in SPECS}
    assert {"text-to-sql-analysis", "text-to-sql-response", "text-to-sql-gate-grounding",
            "text-to-sql-generator", "text-to-sql-critic"} <= names
    assert all(sp["type"] == "text" and isinstance(sp["config"], dict) for sp in SPECS)


# --- With the real root prompt seeder --------------------------------------------------------

class RealPromptSeeder(Runner):
    """Runs scripts/seed-app-prompts.py's own main() in-process against the fake server, with
    the environment seed_all hands it, instead of a subprocess."""

    def __init__(self, lf, server):
        super().__init__(lf)
        self.server = server

    def __call__(self, cmd, env):
        name = cmd[-1].rsplit("/", 1)[-1]
        self.cmds.append(name)
        if name != "seed-app-prompts.py":
            return 0
        return PROMPT_MOD.main([], environ=env, opener=self.server.urlopen)


@pytest.mark.parametrize("field,change", [("config", {"model": "claude-old", "temperature": 0.7}),
                                          ("type", "chat")])
def test_the_real_seeder_fixes_drift_that_used_to_pass_as_unchanged(server, lf, field, change):
    served(server, "text-to-sql-response", "production")[field] = change
    env = s.child_env("http://lf.test")
    env.update(LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
    t = s.seed(lf, env, False, runner=RealPromptSeeder(lf, server), prompt_module=PROMPT_MOD)
    assert (t.counts["updated"], t.counts["failed"]) == (1, 0)
    assert s.prompt_plan(lf, SPECS) == [(sp, []) for sp in SPECS]
    assert [b["name"] for p, b in server.writes() if p == "/api/public/v2/prompts"] == ["text-to-sql-response"]
