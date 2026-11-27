"""seed_all.py offline: definitions read from the root seeders, reconciliation against an
in-memory Langfuse behind a fake urlopen, failure propagation, and the history seeder's
exit code. No network, Langfuse, Docker or model calls; subprocesses are faked."""
from __future__ import annotations

import copy
import importlib.util
import os
import subprocess
import sys
import urllib.request
from datetime import timedelta

import pytest

import fake_langfuse as fl
import seed_all as s
import verify_demo as v

PROMPTS = s.prompt_specs(s.load_prompt_seeder())
ITEMS = s.dataset_items(s.DATASET_SEEDER.read_text())


@pytest.fixture(autouse=True)
def restore_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def server(tmp_path, monkeypatch):
    fake = fl.FakeLangfuse()
    path = tmp_path / ".env"
    path.write_text(f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={fake.base}\n"
                    "DEPLOY_MODE=self-hosted\n")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    monkeypatch.setattr(v, "env_file", lambda profile: path)
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    fake.seeders = []  # commands run, in order

    def run(cmd, cwd=None, env=None):
        fake.seeders.append(os.path.basename(cmd[-1]))
        assert env["LANGFUSE_HOST"] == env["LANGFUSE_BASE_URL"] == fake.base  # the .env, not the shell
        rc = fake.seeder_rc.get(os.path.basename(cmd[-1]), 0)
        if os.path.basename(cmd[-1]) == "seed-router-history.py" and rc == 0 and fake.history_lands:
            fill_history(fake)
        return subprocess.CompletedProcess(cmd, rc)

    fake.seeder_rc, fake.history_lands = {}, True
    monkeypatch.setattr(s.subprocess, "run", run)
    return fake


def fill_history(fake, days=range(14)):
    for day in days:
        for n in range(16):
            fake.add_obs("route-query", f"h{day}-{n}", None, fl.ts(-timedelta(days=day, hours=1, minutes=n)))


def run_main(*argv):
    return s.main(list(argv), sleep=lambda _: None)


def summary_line(capsys):
    return capsys.readouterr().out.strip().splitlines()[-1]


# --- definitions ----------------------------------------------------------------------------

def test_prompt_specs_match_what_verify_expects():
    assert {p["name"] for p in PROMPTS} == {v.PROMPT_NAME}
    assert sorted(p["label"] for p in PROMPTS) == sorted(v.FULL_PROMPT_LABELS)
    assert all(p["config"]["temperature"] == 0.0 and "{{question}}" in p["prompt"] for p in PROMPTS)


def test_dataset_items_are_parsed_from_the_root_seeder_with_stable_ids():
    assert len(ITEMS) == v.MIN_DATASET_ITEMS
    assert {i["expected_output"]["route"] for i in ITEMS} == v.VALID_EXPECTED_ROUTES
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)
    assert s.dataset_items(s.DATASET_SEEDER.read_text())[0]["id"] == ITEMS[0]["id"]  # stable across runs
    mixed = next(i for i in ITEMS if i["input"]["question"] == "Is ClickHouse fast?")
    assert mixed["metadata"] == {"category": "ambiguous", "route": "fallback", "mixed_intent": True}


def test_dataset_items_accept_extra_as_a_keyword_and_reject_a_missing_list():
    src = 'ITEMS: List[Item] = [Item("q", "docs_simple", "clear", extra={"k": 1})]\n'
    assert s.dataset_items(src)[0]["metadata"] == {"category": "clear", "route": "docs_simple", "k": 1}
    with pytest.raises(ValueError):
        s.dataset_items("X = 1\n")


def test_seeded_widgets_are_the_ones_verify_checks():
    assert [w["name"] for w in v.WIDGETS] == [f"Router Ops · {n}" for n in (
        "Route distribution over time", "Fallback rate", "Avg router_confidence", "Misroute rate")]
    assert all(set(v.WIDGET_FIELDS) <= set(w) for w in v.WIDGETS)


# --- a full seed, then an unchanged reseed -------------------------------------------------

def test_empty_project_is_seeded_then_an_unchanged_reseed_writes_nothing(server, capsys):
    assert run_main() == 0
    first = summary_line(capsys)
    assert "failed" in first and first.endswith("0 failed")
    assert server.seeders == ["seed-code-evaluators.sh", "seed-router-history.py", "seed-router-judge.sh"]
    assert sorted(p["labels"][0] for p in server.prompts) == ["baseline", "production"]
    assert len(server.items[v.DATASET_NAME]) == 30
    assert len(server.dashboards) == 1 and len(server.dashboards[0]["definition"]["widgets"]) == 4
    writes, server.seeders = len(server.writes), []
    assert run_main() == 0
    out = capsys.readouterr().out
    assert "0 created, 0 updated" in out and "router history:" in out
    assert len(server.writes) == writes  # no new prompt versions, items, widgets or history
    assert server.seeders == ["seed-code-evaluators.sh", "seed-router-judge.sh"]  # history left alone
    assert len(server.prompts) == 2


def test_seeding_then_verify_is_ready_once_live_runs_exist(server, capsys):
    assert run_main() == 0
    ready = fl.ready_project(fl.FakeLangfuse(), v, s)
    server.observations += [o for o in ready.observations if o["traceId"].startswith("live")]
    server.scores += ready.scores
    server.health, server.experiments, server.experiment_items = ready.health, ready.experiments, ready.experiment_items
    for e in server.experiments:
        e["datasetId"] = server.datasets[v.DATASET_NAME]["id"]
    del v.RESULTS[:]
    assert v.main([]) == 0, [w for st, _, w in v.RESULTS if st == "FAIL"]


def test_dry_run_writes_nothing_and_runs_no_seeder(server, capsys):
    assert run_main("--dry-run") == 0
    assert server.writes == [] and server.seeders == []
    out = capsys.readouterr().out
    assert "would run" in out and "created    dashboard Router Ops" in out


def test_wrong_project_writes_nothing(server):
    server.project = {"id": "p9", "name": "someone-elses-project"}
    assert run_main() == 1
    assert server.writes == [] and server.seeders == []


def test_missing_or_partial_env_fails_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("request made"))
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "missing.env")
    assert run_main() == 1
    partial = tmp_path / ".env"
    partial.write_text("LANGFUSE_PUBLIC_KEY=pk\n")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://shell.invalid")
    monkeypatch.setattr(v, "env_file", lambda profile: partial)
    assert run_main() == 1


def test_cloud_mode_skips_the_postgres_seeders_truthfully(server, capsys):
    os.environ["DEPLOY_MODE"] = "cloud"
    with open(v.env_file(None), "a") as f:
        f.write("DEPLOY_MODE=cloud\n")
    assert run_main() == 0
    assert "seed-code-evaluators.sh" not in server.seeders and "seed-router-judge.sh" not in server.seeders
    assert capsys.readouterr().out.count("skipped") >= 2


# --- prompts ------------------------------------------------------------------------------------

def seed_prompts(server, **changes):
    for spec in PROMPTS:
        row = {"name": spec["name"], "version": len(server.prompts) + 1, "type": "text", "prompt": spec["prompt"],
               "config": copy.deepcopy(spec["config"]), "labels": [spec["label"]]}
        if spec["label"] == "production":
            row.update(changes)
        server.prompts.append(row)


def lf(server):
    return v.Langfuse(server.base, "pk", "sk")


def test_identical_prompt_is_unchanged_even_with_json_int_temperature(server):
    seed_prompts(server, config={"model": PROMPTS[1]["config"]["model"], "temperature": 0})
    assert s.reconcile_prompt(lf(server), PROMPTS[1]) == "unchanged"
    assert server.writes == []


@pytest.mark.parametrize("change", [{"prompt": "edited in the UI"}, {"config": {"model": "other", "temperature": 0.0}},
                                    {"type": "chat"}])
def test_drift_adds_one_version_carrying_the_label(server, change):
    seed_prompts(server, **change)
    assert s.reconcile_prompt(lf(server), PROMPTS[1]) == "updated"
    served = [p for p in server.prompts if "production" in p["labels"]]
    assert len(server.writes) == 1 and served[-1]["prompt"] == PROMPTS[1]["prompt"]


def test_missing_label_is_created(server):
    seed_prompts(server)
    server.prompts = [p for p in server.prompts if "baseline" not in p["labels"]]
    assert s.reconcile_prompt(lf(server), PROMPTS[0]) == "created"


def test_prompt_dry_run_writes_nothing(server):
    seed_prompts(server, prompt="drifted")
    assert s.reconcile_prompt(lf(server), PROMPTS[1], dry_run=True) == "updated"
    assert server.writes == []


def test_one_failing_prompt_label_does_not_hide_the_other(server, capsys):
    server.fail = lambda method, path, body: 500 if path == "/api/public/v2/prompts" and "baseline" in body["labels"] else None
    assert run_main() == 1
    out = capsys.readouterr().out
    assert "failed     prompt query-router-classifier (label baseline)" in out
    assert "created    prompt query-router-classifier (label production)" in out


# --- dataset -------------------------------------------------------------------------------------

def stored(item, **changes):
    row = {"id": item["id"], "status": "ACTIVE", "input": copy.deepcopy(item["input"]),
           "expectedOutput": copy.deepcopy(item["expected_output"]), "metadata": copy.deepcopy(item["metadata"])}
    row.update(changes)
    return row


def seed_dataset(server, rows, **ds):
    server.datasets["ds"] = dict({"id": "ds1", "name": "ds", "description": s.DATASET_DESCRIPTION,
                                  "metadata": s.DATASET_METADATA}, **ds)
    server.items["ds"] = rows


def test_missing_dataset_is_created_with_every_item_under_a_stable_id(server):
    assert s.reconcile_dataset(lf(server), "ds", ITEMS) == ("created", {"created": 30, "updated": 0, "unchanged": 0,
                                                                       "failed": 0}, [])
    assert [r["id"] for r in server.items["ds"]] == [i["id"] for i in ITEMS]


def test_identical_dataset_writes_nothing(server):
    seed_dataset(server, [stored(i) for i in ITEMS])
    assert s.reconcile_dataset(lf(server), "ds", ITEMS)[:2] == ("unchanged", {"created": 0, "updated": 0,
                                                                             "unchanged": 30, "failed": 0})
    assert server.writes == []


def test_dataset_definition_drift_is_updated(server):
    seed_dataset(server, [stored(i) for i in ITEMS], description="edited")
    assert s.reconcile_dataset(lf(server), "ds", ITEMS)[0] == "updated"
    assert server.datasets["ds"]["description"] == s.DATASET_DESCRIPTION


def test_drifted_archived_and_missing_items_are_fixed_and_pinned_items_left_alone(server):
    rows = [stored(i) for i in ITEMS]
    rows[0]["expectedOutput"] = {"route": "docs_simple"}                 # edited in the UI
    rows[1]["status"] = "ARCHIVED"                                       # archived by hand
    rows[3] = stored(ITEMS[3], id="legacy-random-id")                    # seeded by the old appending seeder
    rows[3]["metadata"] = {"category": "clear"}
    del rows[2]                                                          # deleted
    pinned = {"id": "pinned", "status": "ACTIVE", "input": {"question": "a production misroute"},
              "expectedOutput": {"route": "analytics_sql"}, "metadata": {"source": "production-misroute"}}
    seed_dataset(server, rows + [copy.deepcopy(pinned)])
    _, counts, errors = s.reconcile_dataset(lf(server), "ds", ITEMS)
    assert counts == {"created": 1, "updated": 3, "unchanged": 26, "failed": 0} and not errors
    ids = {b["id"] for _, _, b in server.writes}
    assert ids == {ITEMS[0]["id"], ITEMS[1]["id"], ITEMS[2]["id"], "legacy-random-id"}
    assert all(r["status"] == "ACTIVE" for r in server.items["ds"])
    assert next(r for r in server.items["ds"] if r["id"] == "pinned") == pinned


def test_partial_item_failures_are_counted_and_fail_the_run(server, capsys):
    bad = ITEMS[4]["id"]
    server.fail = lambda method, path, body: 500 if path == "/api/public/dataset-items" and body["id"] == bad else None
    assert run_main() == 1
    out = capsys.readouterr().out
    assert "failed     1 item(s)" in out and "created    29 item(s)" in out


def test_total_dataset_failure_fails_the_run(server):
    server.fail = lambda method, path, body: 500 if path.startswith("/api/public/v2/datasets") else None
    assert run_main() == 1


def test_dataset_dry_run_writes_nothing(server):
    assert s.reconcile_dataset(lf(server), "ds", ITEMS, dry_run=True)[0] == "created"
    assert server.writes == []


# --- dashboard ------------------------------------------------------------------------------------

def test_dashboard_drift_and_missing_placement_are_repaired(server):
    s.reconcile_dashboard(lf(server))
    server.widgets[0]["filters"] = []
    server.dashboards[0]["definition"]["widgets"].pop()
    results = dict((what, r) for r, what in s.reconcile_dashboard(lf(server)))
    assert results[f"widget {v.WIDGETS[0]['name']}"] == "updated"
    assert results[f"widget {v.WIDGETS[3]['name']}"] == "updated"
    assert results[f"widget {v.WIDGETS[1]['name']}"] == "unchanged"
    assert server.widgets[0]["filters"] == v.WIDGETS[0]["filters"]
    assert len(server.dashboards[0]["definition"]["widgets"]) == 4


def test_one_bad_widget_fails_without_hiding_the_others(server, capsys):
    server.fail = lambda method, path, body: 400 if path.endswith("dashboard-widgets") and method == "POST" \
        and body["view"] == "scores-boolean" else None
    assert run_main() == 1
    out = capsys.readouterr().out
    assert "failed     widget Router Ops · Misroute rate" in out
    assert "created    widget Router Ops · Fallback rate" in out


def test_server_without_a_dashboards_api_fails_the_run(server, capsys):
    server.dashboards_api = False
    assert run_main() == 1
    assert "no dashboards API" in capsys.readouterr().out


# --- history ----------------------------------------------------------------------------------------

def test_history_seeder_failure_fails_the_run(server, capsys):
    server.seeder_rc["seed-router-history.py"] = 1
    assert run_main() == 1
    assert "exited non-zero" in capsys.readouterr().out


def test_history_that_never_lands_fails_after_a_bounded_wait(server, capsys):
    server.history_lands = False
    sleeps = []
    assert s.main([], sleep=sleeps.append) == 1
    assert sum(sleeps) >= s.HISTORY_WAIT_S and len(sleeps) == s.HISTORY_WAIT_S // s.HISTORY_POLL_S
    assert "still short after" in capsys.readouterr().out


def test_thin_week_reseeds_history(server):
    fill_history(server, days=range(7))      # week 1 aged out
    run_main()
    assert "seed-router-history.py" in server.seeders


def test_postgres_seeder_failure_fails_the_run(server):
    server.seeder_rc["seed-router-judge.sh"] = 1
    assert run_main() == 1


def test_skip_evaluators_runs_neither_postgres_seeder(server):
    run_main("--skip-evaluators")
    assert server.seeders == ["seed-router-history.py"]


# --- the root history seeder's own exit code ----------------------------------------------------------

def load_history_seeder():
    spec = importlib.util.spec_from_file_location("seed_router_history", s.HISTORY_SEEDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("post", [
    lambda batch: {"successes": batch[1:], "errors": [{"id": batch[0]["id"], "status": 400}]},  # partial
    lambda batch: (_ for _ in ()).throw(OSError("connection refused")),                        # total
])
def test_history_seeder_exits_non_zero_on_rejected_or_lost_events(monkeypatch, post):
    mod = load_history_seeder()
    monkeypatch.setattr(mod, "_post_batch", post)
    monkeypatch.setattr(sys, "argv", ["seed-router-history.py", "--count", "5"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 1


def test_history_seeder_exits_zero_when_every_event_is_accepted(monkeypatch):
    mod = load_history_seeder()
    monkeypatch.setattr(mod, "_post_batch", lambda batch: {"successes": batch, "errors": []})
    monkeypatch.setattr(sys, "argv", ["seed-router-history.py", "--count", "5"])
    mod.main()
