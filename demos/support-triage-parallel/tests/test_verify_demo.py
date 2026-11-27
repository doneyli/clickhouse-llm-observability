"""verify_demo.py offline, through a fake Langfuse at the urllib level (tests/fake_langfuse.py):
real query strings, field projection, cursor and page pagination, the .env rule, and the
verdict for each ready and not-ready state. No network, Langfuse, Docker or model calls."""
from __future__ import annotations

import os
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

import seed_datasets
import seed_prompts
import verify_demo as v
from fake_langfuse import FakeLangfuse

BASE = "https://lf.test"
_now = datetime.now(timezone.utc)
NOW = v.iso(_now - timedelta(hours=1))
EARLIER = v.iso(_now - timedelta(hours=2))
OLDER = v.iso(_now - timedelta(hours=3))


@pytest.fixture(autouse=True)
def isolated():
    """Fresh results, and os.environ restored: load_env writes to it."""
    saved = dict(os.environ)
    del v.RESULTS[:]
    yield
    del v.RESULTS[:]
    os.environ.clear()
    os.environ.update(saved)


# --- a ready project ----------------------------------------------------------------------

def run_rows(trace_id, start, *, tie=False, root=v.TRACE_NAME):
    names = [root] + v.STAGE_OBSERVATIONS[1:] + ([v.TIE_OBSERVATION] if tie else [])
    return [{"id": f"{trace_id}-{i}", "traceId": trace_id, "name": n, "type": "SPAN", "startTime": start,
             "parentObservationId": None if i == 0 else f"{trace_id}-0"} for i, n in enumerate(names)]


def score(trace_id, name, value=1.0, comment="", ts=NOW):
    return {"id": f"{trace_id}-{name}", "name": name, "value": value, "comment": comment, "timestamp": ts,
            "subject": {"kind": "observation", "id": f"{trace_id}-x", "traceId": trace_id}}


def ready_project() -> FakeLangfuse:
    lf = FakeLangfuse()
    for name, (text, config) in v.expected_prompts().items():
        versions = [{"name": name, "type": "text", "prompt": text, "config": config, "labels": ["production"],
                     "version": 1}]
        if name == v.VERSIONED_PROMPT:
            versions = [dict(versions[0], prompt=seed_prompts.SQL_VOTER_V1, labels=[]),
                        dict(versions[0], version=2)]
        lf.prompts[name] = versions
    lf.datasets = [{"id": "ds-cat", "name": seed_datasets.CATEGORY_DATASET},
                   {"id": "ds-vote", "name": seed_datasets.SQL_VOTING_DATASET}]
    lf.items = {
        seed_datasets.CATEGORY_DATASET: [
            {"id": i, "status": "ACTIVE", "input": inp, "expectedOutput": out}
            for i, inp, out in seed_datasets.category_items()],
        seed_datasets.SQL_VOTING_DATASET: [
            {"id": i, "status": "ACTIVE", "input": inp,
             "expectedOutput": {"result_signature": f"sig-{n:02d}", "canonical_sql": sql}}
            for n, (i, inp, sql) in enumerate(seed_datasets.sql_voting_items())],
    }
    lf.observations = (run_rows("t1", NOW) + run_rows("t2", EARLIER, tie=True)
                       + run_rows("e1", OLDER, tie=True, root="experiment-item-run"))
    agreed = "5/5 valid samples agreed; strategy=result-signature"
    split = "2/5 valid samples agreed; strategy=result-signature"
    lf.scores = ([score("t1", "consensus_confidence", 1.0, agreed), score("t1", "sql_validity_rate"),
                  score("t1", "policy_flagged", 0.0)]
                 + [score("t1", n) for n in v.EVALUATOR_SCORES]
                 + [score("t2", "consensus_confidence", 0.4, split), score("e1", "consensus_confidence", 0.4, split)])
    lf.experiments = [{"id": f"x{n}", "datasetId": "ds-vote", "name": f"aggregator-{s}"}
                      for n, s in enumerate(v.STRATEGIES)]
    lf.dataset_runs = {seed_datasets.SQL_VOTING_DATASET: [{"name": e["name"]} for e in lf.experiments]}
    return lf


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A ready project behind the fake transport, with a root .env and hostile shell exports."""
    env = tmp_path / ".env"
    env.write_text(f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={BASE}\n"
                   "ANTHROPIC_API_KEY=sk-ant-test\nPUBLIC_CH_HOST=lf.test\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-other-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example")
    return ready_project().install(monkeypatch)


def run(argv=()):
    code = v.main(list(argv))
    return code, v.summary()[0]


def statuses(fragment):
    return [s for s, _, what in v.RESULTS if fragment in what]


# --- READY -------------------------------------------------------------------------------

def test_ready_on_a_v4_server_with_the_current_reads(project, capsys):
    code, line = run(["--links"])
    out = capsys.readouterr().out
    assert code == 0 and line.startswith("READY") and "WARN" not in out, out
    assert "read through v1" not in out and "(experiments read)" in out
    assert f"{BASE}/project/p1/traces/t1" in out and f"{BASE}/project/p1/traces/t2" in out
    assert f"{BASE}/project/p1/datasets/ds-vote" in out
    assert not any("/api/public/observations?" in r for r in project.requests)
    assert not any("/traces" in r.split("?")[0] for r in project.requests)   # no legacy trace reads
    assert all("pk-other-project" not in r for r in project.requests)


def test_ready_on_the_pinned_v3_server_through_the_justified_fallbacks(project, capsys):
    project.v2_observations = project.experiments_api = False
    code, line = run()
    out = capsys.readouterr().out
    assert code == 0 and line.startswith("READY"), out
    assert "read through v1" in out and "(dataset runs read)" in out


def test_empty_cursor_pages_are_followed(project):
    project.page_size, project.empty_page_after_first = 1, True
    assert run()[0] == 0
    assert any("cursor=empty-" in r for r in project.requests)


# --- configuration -----------------------------------------------------------------------

def test_missing_env_file_with_hostile_shell_keys_makes_no_request(tmp_path, monkeypatch, capsys):
    lf = FakeLangfuse().install(monkeypatch)
    monkeypatch.setattr(v, "env_file", lambda profile: tmp_path / "missing.env")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")
    monkeypatch.setenv("LANGFUSE_BASE_URL", BASE)
    assert run() == (1, v.summary()[0]) and v.summary()[0].startswith("NOT READY")
    assert lf.requests == []


def test_partial_env_file_fails_before_any_request(tmp_path, monkeypatch):
    lf = FakeLangfuse().install(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_BASE_URL=https://lf.test\nANTHROPIC_API_KEY=x\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")      # must not complete the pair
    assert run()[0] == 1 and lf.requests == []


def test_wrong_project_is_not_ready(project):
    project.project = {"id": "p9", "name": "someone-elses-project"}
    assert run()[0] == 1 and statuses("keys map to project") == ["FAIL"]


def test_empty_project_list_is_not_ready(project):
    project.project = None
    assert run()[0] == 1


def test_unreachable_playground_is_not_ready(project, monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_BASE_URL={BASE}\n"
                   "ANTHROPIC_API_KEY=x\nPUBLIC_CH_HOST=playground.invalid\n")
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    assert run()[0] == 1 and statuses("ClickHouse playground") == ["FAIL"]


def test_a_required_read_failure_reaches_the_verdict(project, capsys):
    project.fail = {"/api/public/v3/scores": 500}
    assert run()[0] == 1
    assert "Langfuse read failed: HTTP 500" in capsys.readouterr().out


def test_a_repeated_cursor_is_an_error_not_a_short_result(project, capsys):
    project.repeat_cursor = True
    assert run()[0] == 1
    assert "same cursor twice" in capsys.readouterr().out


# --- seeded artifacts ----------------------------------------------------------------------

def test_missing_production_label_is_not_ready(project):
    for version in project.prompts["support-triage-category"]:
        version["labels"] = []
    assert run()[0] == 1 and statuses("prompt support-triage-category with label") == ["FAIL"]


def test_config_only_prompt_drift_warns(project):
    project.prompts["support-triage-summary"][-1]["config"] = {"model": "claude-haiku-4-5", "temperature": 0.9}
    code, line = run()
    assert code == 0 and "1 warning" in line
    assert statuses("support-triage-summary: production type, text or config differs") == ["WARN"]


@pytest.mark.parametrize("statuses_by_item, usable", [
    ({"all": "ARCHIVED"}, 0),                  # archived-only: totalItems still says 12
    ({"cat-03": "ARCHIVED", "cat-07": "ARCHIVED"}, 10),
])
def test_archived_items_are_not_usable(project, capsys, statuses_by_item, usable):
    for item in project.items[seed_datasets.CATEGORY_DATASET]:
        item["status"] = statuses_by_item.get("all") or statuses_by_item.get(item["id"], "ACTIVE")
    assert run()[0] == 1
    out = capsys.readouterr().out
    assert f"support-triage/category-branch: {usable}/12 expected items ACTIVE and usable" in out
    if usable:
        assert "cat-03 archived" in out and "cat-07 archived" in out


def test_changed_item_content_is_not_usable(project, capsys):
    project.items[seed_datasets.CATEGORY_DATASET][0]["expectedOutput"] = {"category": "billing"}
    assert run()[0] == 1 and "cat-00 content differs" in capsys.readouterr().out


def test_unpinned_signature_is_not_usable(project, capsys):
    project.items[seed_datasets.SQL_VOTING_DATASET][4]["expectedOutput"]["result_signature"] = None
    assert run()[0] == 1 and "vote-04 no pinned result signature" in capsys.readouterr().out


def test_missing_dataset_is_not_ready(project):
    project.datasets = project.datasets[:1]
    assert run()[0] == 1 and statuses("dataset support-triage/sql-voting exists") == ["FAIL"]


# --- the latest run and the acts --------------------------------------------------------------

def test_missing_stage_is_not_ready(project, capsys):
    project.observations = [o for o in project.observations if not (o["traceId"] == "t1" and o["name"] == "vote-sql")]
    assert run()[0] == 1 and "missing vote-sql" in capsys.readouterr().out


def test_missing_score_is_not_ready(project, capsys):
    project.scores = [s for s in project.scores if not (s["subject"]["traceId"] == "t1" and s["name"] != "consensus_confidence")]
    assert run()[0] == 1 and "missing sql_validity_rate, policy_flagged" in capsys.readouterr().out


@pytest.mark.parametrize("short", [False, True])
def test_no_tie_trace_is_not_ready_on_either_path(project, short):
    project.observations = [o for o in project.observations if o["name"] != v.TIE_OBSERVATION]
    assert run(["--short"] if short else [])[0] == 1
    assert statuses("Act 3 tie trace") == ["FAIL"]


def test_a_tie_in_an_experiment_trace_does_not_count(project):
    project.observations = [o for o in project.observations if not (o["traceId"] == "t2" and o["name"] == v.TIE_OBSERVATION)]
    assert run()[0] == 1 and statuses("Act 3 tie trace") == ["FAIL"]


def test_a_judge_consensus_run_is_not_a_split_vote(project):
    for s in project.scores:
        if s["subject"]["traceId"] == "t2":
            s["comment"] = "2/5 valid samples agreed; strategy=judge-consensus"
    assert run()[0] == 1 and statuses("Act 3 tie trace") == ["FAIL"]


def test_missing_aggregator_run_fails_the_full_script_and_warns_on_the_short_path(project):
    project.experiments = [e for e in project.experiments if e["name"] != "aggregator-majority-exact"]
    assert run()[0] == 1 and statuses("aggregator runs") == ["FAIL"]
    del v.RESULTS[:]
    code, line = run(["--short"])
    assert code == 0 and statuses("aggregator runs") == ["WARN"]


def test_n_sweep_runs_do_not_stand_in_for_the_comparison(project):
    project.experiments = [{"id": "x", "datasetId": "ds-vote", "name": f"aggregator-{s}-n3"} for s in v.STRATEGIES]
    assert run()[0] == 1 and statuses("aggregator runs") == ["FAIL"]


def test_missing_evaluator_score_fails_the_full_script_and_warns_on_the_short_path(project):
    project.scores = [s for s in project.scores if s["name"] != "correlated-vote-risk"]
    assert run()[0] == 1 and statuses("evaluator score correlated-vote-risk") == ["FAIL"]
    del v.RESULTS[:]
    assert run(["--short"])[0] == 0 and statuses("evaluator score correlated-vote-risk") == ["WARN"]


def test_stale_run_only_warns(project):
    shift = {NOW: 3, EARLIER: 4, OLDER: 5}
    for o in project.observations:
        o["startTime"] = v.iso(_now - timedelta(days=shift[o["startTime"]]))
    code, line = run()
    assert code == 0 and statuses("latest run is") == ["WARN"]


# --- helpers -------------------------------------------------------------------------------

def test_check_records_pass_fail_and_warn(capsys):
    assert v.check(True, "a") is True
    assert v.check(False, "b") is False
    assert v.check(False, "c", required=False, hint="do x") is False
    assert [s for s, _, _ in v.RESULTS] == ["PASS", "FAIL", "WARN"]


def test_summary_counts_and_exit_codes():
    v.check(True, "a")
    v.check(False, "b", required=False)
    assert v.summary() == ("READY (1/2 checks passed, 1 warning)", 0)
    v.check(False, "c")
    assert v.summary() == ("NOT READY (1/3 checks passed, 1 required failed, 1 warning)", 1)


def test_parse_env_handles_comments_quotes_and_export():
    text = '# comment\n\nexport A=1\nB="two words"  \nC=three # trailing\nD=\'x # y\'\nE=\nnot a line\n'
    assert v.parse_env(text) == {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""}


def test_load_env_drops_shell_langfuse_keys_even_without_a_file(tmp_path):
    environ = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "LANGFUSE_HOST": "x", "OTHER": "kept"}
    assert v.load_env(tmp_path / ".env", environ) is False
    assert environ == {"OTHER": "kept"}


def test_container_profile_reads_the_root_env():
    assert v.env_file("container") == v.REPO_ROOT / ".env"
    text = (v.DEMO_DIR / "demo.yaml").read_text()
    assert v.manifest_value(text, "", "profile") == "container"
    assert v.manifest_value(text, "langfuse", "project") == "LLM Observability Demo"


def test_parse_time_accepts_api_timestamps():
    assert v.parse_time("2026-11-26T10:00:00.123Z").microsecond == 123000
    assert v.parse_time("2026-11-26T10:00:00.123456789+00:00").microsecond == 123456


def test_network_failure_is_an_api_error(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(v.urllib.request, "urlopen", refuse)
    with pytest.raises(v.ApiError) as e:
        v.Langfuse("http://langfuse.invalid", "pk", "sk").get("/api/public/projects")
    assert e.value.status is None


def test_expected_definitions_match_the_seeders():
    assert sorted(v.expected_prompts()) == sorted(v.SEED_PROMPTS) and len(v.SEED_PROMPTS) == 7
    assert v.EXPECTED_DATASETS == {seed_datasets.CATEGORY_DATASET: len(seed_datasets.CATEGORY_ITEMS),
                                   seed_datasets.SQL_VOTING_DATASET: len(seed_datasets.SQL_VOTING_ITEMS)}


@pytest.mark.parametrize("name, strategy", [
    ("aggregator-result-signature", "result-signature"),
    ("aggregator-majority-exact-rehearsal", "majority-exact"),
    ("aggregator-judge-consensus-n3", None),
    ("aggregator-judge-consensus-n3-x", None),
    ("something-else", None),
])
def test_strategy_of_run(name, strategy):
    assert v.strategy_of_run(name) == strategy


def test_links_cover_every_act():
    links = v.act_links(BASE, {"project_id": "p1", "trace_id": "t1", "tie_trace_id": "t2",
                               "experiment_dataset_id": "d1"})
    assert [label.split(" ·")[0] for label, _ in links] == ["Act 1-2", "Act 3-4", "Act 5", "Act 6", "Act 7",
                                                           "Show me the code"]
