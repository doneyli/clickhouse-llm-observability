"""seed_all.py offline: definitions read from the root seeders, and the reconcile decisions
for prompts, dataset items and history, against a fake Langfuse. No network, no Docker."""
from __future__ import annotations

import copy
import os

import pytest

import seed_all as s
import verify_demo as v

PROMPTS = s.prompt_specs(s.load_prompt_seeder())
SPEC = PROMPTS[1]
ITEMS = s.dataset_items(s.DATASET_SEEDER.read_text())


@pytest.fixture(autouse=True)
def restore_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# --- definitions ----------------------------------------------------------------------------

def test_prompt_specs_match_what_verify_expects():
    assert {p["name"] for p in PROMPTS} == {v.PROMPT_NAME}
    assert tuple(sorted(p["label"] for p in PROMPTS)) == tuple(sorted(v.EXPECTED_PROMPT_LABELS))
    assert all(p["config"]["temperature"] == 0.0 for p in PROMPTS)
    assert all("{{question}}" in p["prompt"] for p in PROMPTS)


def test_dataset_items_are_parsed_from_the_root_seeder():
    assert len(ITEMS) == v.MIN_DATASET_ITEMS
    assert {i["expected_output"]["route"] for i in ITEMS} == set(v.ROUTES) | {"fallback"}
    assert len({i["input"]["question"] for i in ITEMS}) == len(ITEMS)  # question is the match key
    mixed = next(i for i in ITEMS if i["input"]["question"] == "Is ClickHouse fast?")
    assert mixed["metadata"] == {"category": "ambiguous", "route": "fallback", "mixed_intent": True}


def test_dataset_items_accept_extra_as_a_keyword():
    src = 'ITEMS: List[Item] = [Item("q", "docs_simple", "clear", extra={"k": 1})]\n'
    assert s.dataset_items(src)[0]["metadata"] == {"category": "clear", "route": "docs_simple", "k": 1}


def test_dataset_items_without_a_list_is_an_error():
    with pytest.raises(ValueError):
        s.dataset_items("X = 1\n")


# --- prompts ----------------------------------------------------------------------------------

class FakePrompts:
    def __init__(self, served=None, status=404):
        self.served, self.status, self.posts = served, status, []

    def get(self, path, **params):
        if self.served is None:
            raise v.ApiError(self.status, "not found")
        return self.served

    def post(self, path, body):
        self.posts.append(body)


def served(**changes):
    row = {"type": SPEC["type"], "prompt": SPEC["prompt"], "config": copy.deepcopy(SPEC["config"]), "version": 2}
    row.update(changes)
    return row


def test_missing_prompt_label_is_created():
    lf = FakePrompts()
    assert s.reconcile_prompt(lf, SPEC) == "created"
    assert lf.posts[0]["labels"] == ["production"] and lf.posts[0]["prompt"] == SPEC["prompt"]


def test_identical_prompt_is_left_unchanged():
    lf = FakePrompts(served(config={"model": SPEC["config"]["model"], "temperature": 0}))  # JSON 0 == 0.0
    assert s.reconcile_prompt(lf, SPEC) == "unchanged"
    assert lf.posts == []


@pytest.mark.parametrize("change", [{"prompt": "edited in the UI"}, {"config": {"model": "other"}}])
def test_drift_adds_a_version_for_that_label_only(change):
    lf = FakePrompts(served(**change))
    assert s.reconcile_prompt(lf, SPEC) == "updated"
    assert len(lf.posts) == 1 and lf.posts[0]["labels"] == ["production"]


def test_prompt_dry_run_writes_nothing():
    lf = FakePrompts(served(prompt="drifted"))
    assert s.reconcile_prompt(lf, SPEC, dry_run=True) == "updated"
    assert lf.posts == []


def test_other_prompt_errors_propagate():
    with pytest.raises(v.ApiError):
        s.reconcile_prompt(FakePrompts(status=500), SPEC)


# --- dataset ----------------------------------------------------------------------------------

def stored(item, n, **changes):
    row = {"id": f"id{n}", "status": "ACTIVE", "input": copy.deepcopy(item["input"]),
           "expectedOutput": copy.deepcopy(item["expected_output"]), "metadata": copy.deepcopy(item["metadata"])}
    row.update(changes)
    return row


class FakeDataset:
    def __init__(self, rows=None):
        self.rows, self.posts = rows, []

    def get(self, path, **params):
        if self.rows is None:
            raise v.ApiError(404, "dataset not found")
        return {"id": "ds1"}

    def pages(self, path, params, cap=1000):
        assert params == {"datasetName": "ds"}
        return self.rows

    def post(self, path, body):
        self.posts.append((path, body))


def test_missing_dataset_is_created_with_every_item():
    lf = FakeDataset()
    assert s.reconcile_dataset(lf, "ds", ITEMS) == ("created", {"created": 30, "updated": 0, "unchanged": 0})
    assert lf.posts[0][0] == "/api/public/v2/datasets"
    assert all(p == "/api/public/dataset-items" and "id" not in b for p, b in lf.posts[1:])


def test_identical_dataset_writes_nothing():
    lf = FakeDataset([stored(i, n) for n, i in enumerate(ITEMS)])
    assert s.reconcile_dataset(lf, "ds", ITEMS) == ("unchanged", {"created": 0, "updated": 0, "unchanged": 30})
    assert lf.posts == []


def test_drifted_and_missing_items_are_fixed_and_pinned_items_left_alone():
    rows = [stored(i, n) for n, i in enumerate(ITEMS)]
    rows[0]["expectedOutput"] = {"route": "docs_simple"}     # edited in the UI
    rows[1]["status"] = "ARCHIVED"                           # archived by hand
    del rows[2]                                              # deleted
    rows.append({"id": "pinned", "status": "ACTIVE", "input": {"question": "a production misroute"},
                 "expectedOutput": {"route": "analytics_sql"}, "metadata": {"source": "production-misroute"}})
    lf = FakeDataset(rows)
    assert s.reconcile_dataset(lf, "ds", ITEMS) == ("unchanged", {"created": 1, "updated": 2, "unchanged": 27})
    upserts = {b.get("id") for _, b in lf.posts}
    assert upserts == {"id0", "id1", None}
    assert all(b.get("status", "ACTIVE") == "ACTIVE" for _, b in lf.posts)


def test_dataset_dry_run_writes_nothing():
    lf = FakeDataset()
    assert s.reconcile_dataset(lf, "ds", ITEMS, dry_run=True)[0] == "created"
    assert lf.posts == []


# --- history and main -----------------------------------------------------------------------

class FakeHistory:
    def __init__(self, older, recent):
        self.counts = [older, recent]

    def observations(self, cap=100, **filters):
        assert filters["name"] == v.ROUTER_OBS
        return [{}] * min(cap, self.counts.pop(0))


def test_history_present_needs_both_weeks():
    assert s.history_present(FakeHistory(150, 150))[0] is True
    ok, detail = s.history_present(FakeHistory(0, 150))  # week 1 aged out
    assert ok is False and detail.startswith("0 rows in days 8-14")


def test_dry_run_main_writes_nothing_and_runs_no_seeder(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL=https://lf.example\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    posts = []

    class Fake(FakeDataset):
        def __init__(self, *a):
            super().__init__(None)
            self.posts = posts

        def observations(self, cap=100, **filters):
            return []

    monkeypatch.setattr(v, "Langfuse", Fake)
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: pytest.fail("a seeder ran in a dry run"))
    assert s.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert posts == []
    assert "would run" in out and "router history via seed-router-history.py" in out
    assert os.environ["LANGFUSE_HOST"] == os.environ["LANGFUSE_BASE_URL"] == "https://lf.example"


def test_main_without_env_file_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "missing.env")
    assert s.main([]) == 1
