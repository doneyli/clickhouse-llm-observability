"""seed_langfuse.py's steps against an in-memory Langfuse: reconcile, idempotence, failures.

The real planners, paginate() and seeding steps run; only the transport is fake.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent
for p in (DEMO, DEMO / "scripts", DEMO / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import seed_langfuse as seed  # noqa: E402
from fakes_langfuse import FakeSeedAPI  # noqa: E402


@pytest.fixture
def api(monkeypatch):
    fake = FakeSeedAPI()
    monkeypatch.setattr(seed.API, "from_env", classmethod(lambda cls: fake))
    return fake


def by_name(api, name):
    return next(c for c in api.configs if c["name"] == name and not c.get("isArchived"))


def test_empty_project_seeds_everything_then_reseeding_writes_nothing(api, capsys):
    assert seed.main([]) == 0
    assert len(api.configs) == len(seed.SCORE_CONFIGS)
    assert all(api.production(n)["version"] == 1 for n in seed.load_prompts())
    assert len(api.placements[api.dashboards[0]["id"]]) == len(seed.WIDGETS)
    api.writes.clear()
    assert seed.main([]) == 0
    assert api.writes == []  # no new prompt versions, configs, widgets or placements
    out = capsys.readouterr().out
    assert out.rstrip().endswith("OK")


def test_config_only_drift_is_patched(api, capsys):
    api.seed_everything()
    by_name(api, "llm_calls")["description"] = "old"
    by_name(api, "db_time_share")["maxValue"] = 5
    by_name(api, "db_p95_ms")["maxValue"] = 10  # a bound the contract leaves open
    assert seed.main(["--only", "scores"]) == 0
    assert by_name(api, "llm_calls")["description"] != "old"
    assert by_name(api, "db_time_share")["maxValue"] == 1
    assert "maxValue" not in by_name(api, "db_p95_ms")
    assert [m for m, _ in api.writes] == ["PATCH"] * 3


def test_archived_only_config_is_unarchived_not_duplicated(api):
    api.seed_everything()
    by_name(api, "rounds")["isArchived"] = True
    assert seed.main(["--only", "scores"]) == 0
    assert sum(c["name"] == "rounds" for c in api.configs) == 1
    assert by_name(api, "rounds")


def test_type_conflict_fails_the_seed(api, capsys):
    api.seed_everything()
    by_name(api, "stop_reason")["dataType"] = "NUMERIC"
    assert seed.main(["--only", "scores"]) == 1
    out = capsys.readouterr().out
    assert "cannot be reconciled" in out and "FAILED" in out
    assert not [w for w in api.writes if w[0] == "POST"]  # never duplicated


def test_type_conflict_fails_a_dry_run_too(api):
    api.seed_everything()
    by_name(api, "stop_reason")["dataType"] = "NUMERIC"
    assert seed.main(["--dry-run", "--only", "scores"]) == 1
    assert api.writes == []


def test_partial_write_failure_fails_but_the_rest_land(api, capsys):
    api.fail = lambda m, p, b: 500 if m == "POST" and (b or {}).get("name") == "rounds" else None
    assert seed.main(["--only", "scores"]) == 1
    assert len(api.configs) == len(seed.SCORE_CONFIGS) - 1
    assert "rounds: create failed" in capsys.readouterr().out


def test_total_write_failure_fails(api):
    api.fail = lambda m, p, b: 503 if m != "GET" else None
    assert seed.main([]) == 1
    assert api.configs == [] and api.prompts == {}


def test_read_back_mismatch_fails(api, capsys):
    api.seed_everything()
    by_name(api, "llm_calls")["description"] = "old"
    api.ignore_patches = True  # 200 OK, nothing changed
    assert seed.main(["--only", "scores"]) == 1
    assert "llm_calls: still differs in description" in capsys.readouterr().out


def test_changed_prompt_gets_one_new_version_and_unchanged_none(api):
    api.seed_everything()
    name = next(iter(seed.load_prompts()))
    api.production(name)["prompt"] = "edited in the UI"
    assert seed.main(["--only", "prompts"]) == 0
    assert api.production(name)["version"] == 2
    assert all(api.production(n)["version"] == 1 for n in seed.load_prompts() if n != name)


def test_prompt_config_drift_and_missing_label_are_reconciled(api):
    api.seed_everything()
    names = list(seed.load_prompts())
    api.production(names[0])["config"] = {"temperature": 1}
    api.production(names[1])["labels"] = ["latest"]  # production label moved away
    assert seed.main(["--only", "prompts"]) == 0
    assert all(api.production(n)["version"] == 2 for n in names)


class ForgetfulAPI(FakeSeedAPI):
    """Answers a prompt create with 200 and a version, but stores nothing."""

    def __call__(self, method, path, body=None, params=None):
        if method == "POST" and path == "/api/public/v2/prompts":
            return {"version": 9}
        return super().__call__(method, path, body, params)


def test_prompt_read_back_failure_fails(monkeypatch, capsys):
    api = ForgetfulAPI()
    monkeypatch.setattr(seed.API, "from_env", classmethod(lambda cls: api))
    api.seed_everything()
    name = next(iter(seed.load_prompts()))
    api.production(name)["prompt"] = "stale"
    assert seed.main(["--only", "prompts"]) == 1
    assert f"x read back: {name}: production v1 differs" in capsys.readouterr().out


def test_widget_rejection_fails_the_seed(api, capsys):
    bad = seed.WIDGETS[0]["name"]
    api.fail = lambda m, p, b: 400 if m == "POST" and (b or {}).get("name") == bad else None
    assert seed.main(["--only", "dashboard"]) == 1
    out = capsys.readouterr().out
    assert f"x {bad}: widget rejected" in out and "FAILED" in out


def test_wrong_project_refuses_to_write(api):
    api.project = "another-project"
    with pytest.raises(SystemExit):
        seed.main([])
    assert api.writes == []
