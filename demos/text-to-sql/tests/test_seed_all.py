"""seed_all.py offline: the per-prompt and per-item reconcile decisions, the step order and
exit code, and the environment the seeders get. A fake Langfuse and a fake runner: no
network, no subprocesses, no Langfuse, no model calls."""
from __future__ import annotations

import copy
import os

import pytest

import seed_all as s
import verify_demo as v

STEP = s.load_module("seed_step_dataset", s.STEP_SEEDER)
PROMPTS = s.prompt_entries(s.load_module("seed_app_prompts", s.PROMPT_SEEDER))


def row_for(item, **changes):
    row = {"id": "i-" + item.input["question"][:8], "input": copy.deepcopy(item.input),
           "expectedOutput": copy.deepcopy(item.expected_output), "metadata": copy.deepcopy(item.metadata),
           "status": "ACTIVE"}
    row.update(changes)
    return row


# --- The per-step dataset ----------------------------------------------------------------------

def test_matching_items_are_unchanged():
    plan = s.step_plan(STEP.CURATED_ITEMS, [row_for(i) for i in STEP.CURATED_ITEMS])
    assert {r for r, _, _ in plan} == {"unchanged"}
    assert len(plan) == len(STEP.CURATED_ITEMS)


def test_missing_items_are_created_and_drifted_ones_updated():
    first, second, *rest = STEP.CURATED_ITEMS
    rows = [row_for(second, expectedOutput={"databases": ["wrong"]})] + [row_for(i) for i in rest]
    plan = s.step_plan(STEP.CURATED_ITEMS, rows)
    assert [r for r, _, _ in plan[:2]] == ["created", "updated"]
    assert plan[1][2] == rows[0]["id"]           # the update targets the existing id
    assert {r for r, _, _ in plan[2:]} == {"unchanged"}


def test_trace_linked_and_archived_rows_do_not_count():
    item = STEP.CURATED_ITEMS[0]
    rows = [row_for(item, metadata={"linked_from": "trace"}), row_for(item, status="ARCHIVED")]
    assert s.step_plan([item], rows)[0][0] == "created"


def test_apply_posts_only_changes_and_upserts_by_id():
    class Fake:
        posts = []

        def post(self, path, body):
            self.posts.append((path, body))

    item = STEP.CURATED_ITEMS[0]
    lf = Fake()
    s.apply_step_plan(lf, "ds", [("unchanged", item, "a"), ("updated", item, "b"), ("created", item, None)])
    assert [b.get("id") for _, b in lf.posts] == ["b", None]
    assert all(p == "/api/public/dataset-items" and b["datasetName"] == "ds" for p, b in lf.posts)


# --- Prompts -----------------------------------------------------------------------------------

class PromptFake:
    def __init__(self, served):
        self.served = served  # (name, label) -> text, absent means 404

    def get(self, path, **params):
        name = v.urllib.parse.unquote(path.rsplit("/", 1)[1])
        key = (name, params["label"])
        if key not in self.served:
            raise v.ApiError(404, "not found")
        return {"prompt": self.served[key], "version": 1}


def test_prompt_plan_follows_the_seeders_rule():
    (n1, l1, t1), (n2, l2, t2), (n3, l3, _t3) = PROMPTS[:3]
    lf = PromptFake({(n1, l1): t1 + "\n", (n2, l2): "edited in the UI"})
    assert s.prompt_plan(lf, PROMPTS[:3]) == [(n1, l1, "unchanged"), (n2, l2, "updated"), (n3, l3, "created")]


def test_prompt_plan_raises_on_other_errors():
    class Broken:
        def get(self, path, **params):
            raise v.ApiError(500, "boom")

    with pytest.raises(v.ApiError):
        s.prompt_plan(Broken(), PROMPTS[:1])


def test_the_prompt_seeder_covers_every_text_to_sql_prompt_the_app_fetches():
    names = {n for n, _, _ in PROMPTS}
    assert {"text-to-sql-analysis", "text-to-sql-response", "text-to-sql-gate-grounding",
            "text-to-sql-generator", "text-to-sql-critic"} <= names


# --- The whole run -----------------------------------------------------------------------------

class SeedFake(PromptFake):
    """Every prompt already up to date; the step dataset present and complete."""

    def __init__(self, step_exists=True):
        super().__init__({(n, l): t for n, l, t in PROMPTS})
        self.step_exists = step_exists
        self.posts = []

    def get(self, path, **params):
        if path == "/api/public/v2/datasets":
            data = [{"id": "d1", "name": STEP.DATASET_NAME}] if self.step_exists else []
            return {"data": data, "meta": {"totalPages": 1}}
        if path == "/api/public/dataset-items":
            return {"data": [row_for(i) for i in STEP.CURATED_ITEMS], "meta": {"totalPages": 1}}
        return super().get(path, **params)

    def post(self, path, body):
        self.posts.append(body)


class Runner:
    def __init__(self, fail=()):
        self.cmds, self.fail = [], fail

    def __call__(self, cmd, env):
        self.cmds.append(os.path.basename(cmd[-1]))
        return 1 if self.cmds[-1] in self.fail else 0


def test_up_to_date_project_reruns_only_the_upserting_seeders():
    runner = Runner()
    t = s.seed(SeedFake(), {}, "py", dry_run=False, runner=runner, langfuse_ok=lambda: True)
    assert runner.cmds == ["seed-code-evaluators.sh", "seed_refine_datasets.py"]
    assert t.counts["failed"] == 0 and t.counts["created"] == t.counts["updated"] == 0


def test_fresh_project_runs_every_seeder_in_order():
    lf = SeedFake(step_exists=False)
    lf.served = {}
    runner = Runner()
    t = s.seed(lf, {}, "py", dry_run=False, runner=runner, langfuse_ok=lambda: True)
    assert runner.cmds == ["seed-app-prompts.py", "seed-code-evaluators.sh",
                           "seed_step_dataset.py", "seed_refine_datasets.py"]
    assert t.counts["created"] == len(PROMPTS) + 1


def test_dry_run_writes_nothing_and_runs_nothing():
    lf = SeedFake(step_exists=False)
    lf.served = {}
    runner = Runner()
    t = s.seed(lf, {}, "py", dry_run=True, runner=runner, langfuse_ok=lambda: True)
    assert runner.cmds == [] and lf.posts == []
    assert t.counts["failed"] == 0


def test_a_failing_seeder_fails_the_run():
    runner = Runner(fail={"seed-code-evaluators.sh"})
    t = s.seed(SeedFake(), {}, "py", dry_run=False, runner=runner, langfuse_ok=lambda: True)
    assert t.counts["failed"] == 1
    assert runner.cmds[-1] == "seed_refine_datasets.py"   # later steps still run


def test_missing_langfuse_package_fails_the_sdk_steps_without_running_them():
    runner = Runner()
    t = s.seed(SeedFake(step_exists=False), {}, "py", dry_run=False, runner=runner, langfuse_ok=lambda: False)
    assert "seed_step_dataset.py" not in runner.cmds and "seed_refine_datasets.py" not in runner.cmds
    assert t.counts["failed"] == 2


def test_seeders_get_one_url_under_both_names(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://stale.example")
    env = s.child_env("http://localhost:3001")
    assert env["LANGFUSE_HOST"] == env["LANGFUSE_BASE_URL"] == "http://localhost:3001"


def test_no_env_file_fails_before_any_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / ".env")
    monkeypatch.setattr(v, "Langfuse", None)
    assert s.main([]) == 1
    assert "no env file" in capsys.readouterr().err
