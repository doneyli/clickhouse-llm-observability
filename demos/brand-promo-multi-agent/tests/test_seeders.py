"""The seed scripts offline, against an in-memory Langfuse (tests/langfuse_fake.py).

Covers what the review found: failures that must reach seed_all's exit code, prompts and
history that must not grow on an unchanged re-run, and the annotation queue on current
APIs. No network, no Langfuse, no model calls.
"""

from __future__ import annotations

import os
import random
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from langfuse_fake import FakeClient

from src import config as config_mod
from src.langfuse_rest import ApiError, Client, LangfuseEnvError, langfuse_settings


@pytest.fixture
def fake():
    return FakeClient()


# --- env loading -------------------------------------------------------------------------

def test_load_env_drops_hostile_shell_exports(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / ".env").write_text('LANGFUSE_PUBLIC_KEY="pk-file"\nLANGFUSE_SECRET_KEY="sk-file"\n'
                                   'LANGFUSE_HOST="https://file.example"\n')
    monkeypatch.setattr(config_mod, "__file__", str(tmp_path / "src" / "config.py"))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-other-project")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://other.example")  # outranks HOST in the SDK
    env = config_mod.load_env()
    assert env.langfuse_public_key == "pk-file" and env.langfuse_host == "https://file.example"
    assert "LANGFUSE_BASE_URL" not in os.environ
    assert langfuse_settings() == ("https://file.example", "pk-file", "sk-file")


def test_missing_env_file_leaves_no_langfuse_settings(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(config_mod, "__file__", str(tmp_path / "src" / "config.py"))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-shell")
    monkeypatch.setenv("LANGFUSE_HOST", "https://shell.example")
    config_mod.load_env()
    assert not [k for k in os.environ if k.startswith("LANGFUSE_")]
    with pytest.raises(LangfuseEnvError):
        langfuse_settings()


def test_partial_env_fails_before_any_request():
    with pytest.raises(LangfuseEnvError) as e:
        langfuse_settings({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})
    assert "LANGFUSE_HOST" in str(e.value)


def test_seeders_fail_without_keys(monkeypatch):
    import scripts.seed_annotation_queue as q
    import scripts.seed_dataset as d
    import scripts.seed_prompts as p
    import scripts.setup_score_configs as sc

    for mod in (p, d, q):
        monkeypatch.setattr(mod, "load_env", lambda: None)
    monkeypatch.setattr(config_mod, "load_env", lambda: None)
    for k in [k for k in os.environ if k.startswith("LANGFUSE_")]:
        monkeypatch.delenv(k)
    assert p.seed_prompts() == 1
    assert d.seed_dataset() == 1
    assert q.seed_annotation_queue() == 1
    assert sc.main([]) == 1


# --- REST client -------------------------------------------------------------------------

def test_request_builds_the_url_and_raises_on_http_errors(monkeypatch):
    seen = {}

    class Resp:
        def __init__(self, raw):
            self.raw = raw

        def read(self):
            return self.raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["url"], seen["method"] = req.full_url, req.get_method()
        return Resp(b'{"data": []}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = Client("https://lf.example/", "pk", "sk")
    c.get("/api/public/v2/observations", isRootObservation=True, filter='[{"a": 1}]', cursor=None)
    assert seen["method"] == "GET"
    assert seen["url"].startswith("https://lf.example/api/public/v2/observations?")
    assert "isRootObservation=true" in seen["url"] and "cursor" not in seen["url"]
    assert "filter=%5B%7B%22a%22%3A+1%7D%5D" in seen["url"]

    import urllib.error

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(ApiError) as e:
        c.get("/x")
    assert e.value.status == 500


def test_cursor_follows_empty_pages_and_rejects_a_repeated_cursor(fake):
    fake.rules = [{"name": f"r{i}"} for i in range(5)]
    assert [r["name"] for r in fake.cursor("/api/public/v2/evaluation-rules", {})] == [f"r{i}" for i in range(5)]

    class Loop(Client):
        def get(self, path, **params):
            return {"data": [], "meta": {"cursor": "same"}}

    with pytest.raises(ApiError, match="repeated"):
        Loop("https://x", "pk", "sk").cursor("/p", {})


# --- prompts -----------------------------------------------------------------------------

def test_prompts_create_then_unchanged_then_update_on_drift(fake):
    from scripts.seed_prompts import seed_prompts

    assert seed_prompts(fake) == 0
    assert len(fake.writes()) == 12 and all(len(v) == 1 for v in fake.prompts.values())

    fake.calls.clear()
    assert seed_prompts(fake) == 0
    assert fake.writes() == []  # an unchanged re-run appends no version

    fake.prompts["promo-planner/orchestrator/system"][-1]["prompt"] = "edited in the UI"
    fake.prompts["promo-planner/judge/response-factuality"][-1]["labels"] = ["latest"]  # label moved off
    fake.calls.clear()
    assert seed_prompts(fake) == 0
    assert len(fake.writes()) == 2
    assert len(fake.prompts["promo-planner/orchestrator/system"]) == 2
    assert "production" in fake.prompts["promo-planner/orchestrator/system"][-1]["labels"]


def test_prompt_failures_are_counted_and_fail_the_step(fake, capsys):
    from scripts.seed_prompts import seed_prompts

    fake.fail[("GET", "/api/public/v2/prompts/promo-planner%2Fjudge")] = ApiError(500, "boom")
    assert seed_prompts(fake) == 1
    out = capsys.readouterr().out
    assert "9 created" in out and "3 failed" in out

    total = FakeClient()
    total.fail[("GET", "/api/public/v2/prompts/")] = ApiError(None, "connection refused")
    assert seed_prompts(total) == 1


# --- score configs -----------------------------------------------------------------------

def test_score_configs_reconcile(fake):
    from scripts.setup_score_configs import main

    assert main([], fake) == 0
    assert len(fake.score_configs) == 20
    fake.calls.clear()
    assert main([], fake) == 0
    assert fake.writes() == []

    gate = next(c for c in fake.score_configs if c["name"] == "certification_gate")
    gate["maxValue"], gate["isArchived"] = 5, True
    review = next(c for c in fake.score_configs if c["name"] == "human_brief_review")
    review["categories"] = review["categories"][:2]
    fake.calls.clear()
    assert main([], fake) == 0
    assert sorted(p for m, p in fake.writes()) == sorted(
        [f"/api/public/score-configs/{gate['id']}", f"/api/public/score-configs/{review['id']}"])
    assert gate["maxValue"] == 1 and gate["isArchived"] is False and len(review["categories"]) == 3


def test_score_config_type_mismatch_and_list_failure_fail(fake):
    from scripts.setup_score_configs import main

    fake.score_configs.append({"id": "x", "name": "sku_validity", "dataType": "BOOLEAN"})
    assert main([], fake) == 1
    broken = FakeClient()
    broken.fail[("GET", "/api/public/score-configs")] = ApiError(500, "boom")
    assert main([], broken) == 1


# --- dataset -----------------------------------------------------------------------------

def test_dataset_reconciles_and_keeps_unrelated_items(fake):
    from scripts.seed_dataset import DATASET_NAME, seed_dataset

    fake.datasets[DATASET_NAME] = {"id": "d1", "name": DATASET_NAME}
    fake.items["someone-else"] = {"datasetName": DATASET_NAME, "id": "someone-else", "input": {"q": 1},
                                  "expectedOutput": None, "metadata": None, "status": "ACTIVE"}
    assert seed_dataset(fake) == 0
    assert len(fake.items) == 76
    fake.calls.clear()
    assert seed_dataset(fake) == 0
    assert fake.writes() == []

    owned = [i for i in fake.items.values() if i["id"].startswith(f"{DATASET_NAME}::")]
    owned[0]["status"] = "ARCHIVED"
    owned[1]["expectedOutput"] = {"intent": "drifted"}
    fake.calls.clear()
    assert seed_dataset(fake) == 0
    assert len(fake.writes()) == 2 and fake.items[owned[0]["id"]]["status"] == "ACTIVE"
    assert fake.items[owned[1]["id"]]["expectedOutput"] != {"intent": "drifted"}
    assert fake.items["someone-else"]["input"] == {"q": 1}


def test_dataset_write_and_builder_failures_fail(fake, monkeypatch):
    from scripts.seed_dataset import seed_dataset

    fake.fail[("POST", "/api/public/dataset-items")] = ApiError(500, "boom")
    assert seed_dataset(fake) == 1

    import src.evals.dataset_builder as builder
    monkeypatch.setattr(builder, "build_generated_items", lambda seed: (_ for _ in ()).throw(ValueError("bad")))
    assert seed_dataset(FakeClient()) == 1


# --- annotation queue --------------------------------------------------------------------

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def _history(fake, n=12, ambiguous=()):
    for i in range(n):
        tid = f"h{i}"
        fake.observations.append({"id": f"o{i}", "traceId": tid, "traceName": "promo_planner_run",
                                  "name": "compliance_agent", "startTime": "2026-11-20T10:00:00Z"})
        value = 0.7 if i in ambiguous else 0.95
        fake.scores.append({"id": f"s{i}", "name": "response-factuality", "value": value, "dataType": "NUMERIC",
                            "subject": {"kind": "trace", "id": tid}})
    fake.score_configs.append({"id": "sc-review", "name": "human_brief_review", "dataType": "CATEGORICAL"})


def test_queue_joins_ambiguous_scores_to_history_traces(fake):
    from scripts.seed_annotation_queue import seed_annotation_queue

    _history(fake, ambiguous=(2, 5))
    fake.scores.append({"id": "live", "name": "response-factuality", "value": 0.7, "dataType": "NUMERIC",
                        "subject": {"kind": "trace", "id": "not-history"}})
    assert seed_annotation_queue(fake, now=NOW) == 0
    assert fake.queues[0]["scoreConfigIds"] == ["sc-review"]
    assert sorted(i["objectId"] for i in fake.queue_items) == ["h2", "h5"]
    assert all(i["objectType"] == "TRACE" for i in fake.queue_items)
    # Scores v3 is asked for the subject group, never for "core"
    score_calls = [p for m, path, p, _ in fake.calls if path == "/api/public/v3/scores"]
    assert score_calls and all(p["fields"] == "subject" for p in score_calls)

    fake.calls.clear()
    assert seed_annotation_queue(fake, now=NOW) == 0
    assert fake.writes() == []  # deduplicated: the same two traces are already queued


def test_queue_falls_back_to_newest_history_and_stops_at_target(fake):
    from scripts.seed_annotation_queue import TARGET_ITEMS, seed_annotation_queue

    _history(fake, n=15)
    assert seed_annotation_queue(fake, now=NOW) == 0
    assert len(fake.queue_items) == TARGET_ITEMS
    fake.calls.clear()
    assert seed_annotation_queue(fake, now=NOW) == 0
    assert fake.writes() == []


def test_queue_failures_fail(fake):
    from scripts.seed_annotation_queue import seed_annotation_queue

    assert seed_annotation_queue(FakeClient(), now=NOW) == 1  # no human_brief_review config
    no_history = FakeClient()
    no_history.score_configs.append({"id": "sc", "name": "human_brief_review", "dataType": "CATEGORICAL"})
    assert seed_annotation_queue(no_history, now=NOW) == 1
    _history(fake, ambiguous=(1,))
    fake.fail[("GET", "/api/public/v3/scores")] = ApiError(500, "boom")
    assert seed_annotation_queue(fake, now=NOW) == 1
    writes = FakeClient()
    _history(writes, ambiguous=(1,))
    writes.fail[("POST", "/api/public/annotation-queues/")] = ApiError(500, "boom")
    assert seed_annotation_queue(writes, now=NOW) == 1


# --- dashboards and judges (manual steps) ------------------------------------------------

def test_dashboards_and_judges_report_manual_steps(fake):
    from scripts.seed_dashboards import DASHBOARDS, seed_dashboards
    from scripts.seed_evaluators import EVALUATORS, seed_evaluators

    assert seed_dashboards(fake) == 2 and seed_evaluators(fake) == 2
    fake.dashboards = [{"name": d["name"]} for d in DASHBOARDS]
    fake.rules = [{"name": e["name"], "enabled": True} for e in EVALUATORS]
    assert seed_dashboards(fake) == 0 and seed_evaluators(fake) == 0
    fake.rules[0]["enabled"] = False
    assert seed_evaluators(fake) == 2
    fake.fail[("GET", "/api/public/unstable/dashboards")] = ApiError(500, "boom")
    fake.fail[("GET", "/api/public/v2/evaluation-rules")] = ApiError(500, "boom")
    assert seed_dashboards(fake) == 1 and seed_evaluators(fake) == 1
    assert fake.writes() == []


# --- history -----------------------------------------------------------------------------

def _gen_recorder(results=None):
    calls = []

    def generate(**kw):
        calls.append(kw)
        return results if results is not None else [{"agent": "x"}] * kw["total"]

    return calls, generate


def _land(fake, tid):
    fake.observations.append({"id": f"o-{tid}", "traceId": tid, "traceName": "promo_planner_run",
                              "name": "classify_intent", "startTime": "2026-11-20T10:00:00Z"})


def test_history_creates_then_is_unchanged(fake):
    from scripts.generate_history import generate_history
    from src.config import load_config
    from src.synthetic import history_batch as hb

    today = date(2026, 11, 27)
    calls, gen = _gen_recorder()
    assert generate_history(total=10, client=fake, generate=gen, today=today) == 0
    assert len(calls) == 1 and calls[0]["anchor"] == hb.anchor(today)
    key = hb.batch_key(load_config(), 10, 42, today)
    assert calls[0]["trace_id_for"](0) == hb.trace_id(key, 0)

    _land(fake, hb.trace_id(key, 0))
    _land(fake, hb.trace_id(key, 9))
    assert generate_history(total=10, client=fake, generate=gen, today=today) == 0
    assert len(calls) == 1  # complete batch: nothing written
    # still current four days later, inside the reuse window
    assert generate_history(total=10, client=fake, generate=gen, today=date(2026, 12, 1)) == 0
    assert len(calls) == 1
    assert fake.writes() == []


def test_history_resumes_a_partial_batch_and_fails_loudly(fake):
    from scripts.generate_history import generate_history
    from src.config import load_config
    from src.synthetic import history_batch as hb

    today = date(2026, 11, 27)
    key = hb.batch_key(load_config(), 10, 42, today)
    _land(fake, hb.trace_id(key, 0))
    calls, gen = _gen_recorder()
    assert generate_history(total=10, client=fake, generate=gen, today=today) == 0
    assert len(calls) == 1 and calls[0]["trace_id_for"](9) == hb.trace_id(key, 9)

    _, failing = _gen_recorder([{"agent": "x"}, {"error": "batch rejected", "agent": "batch"}])
    assert generate_history(total=10, client=FakeClient(), generate=failing, today=today) == 1

    broken = FakeClient()
    broken.fail[("GET", "/api/public/v2/observations")] = ApiError(500, "boom")
    calls, gen = _gen_recorder()
    assert generate_history(total=10, client=broken, generate=gen, today=today) == 1
    assert calls == []  # an unknown state never writes another batch


def test_history_read_back_on_a_pinned_v3_server_uses_v1_observations(fake):
    from src.config import load_config
    from src.synthetic import history_batch as hb

    fake.v2_observations = False
    day = date(2026, 11, 27)
    key = hb.batch_key(load_config(), 10, 42, day)
    _land(fake, hb.trace_id(key, 0))
    _land(fake, hb.trace_id(key, 9))
    assert hb.batch_state(fake, key, 10, day, 30) == "complete"


def test_batch_key_follows_the_config(monkeypatch):
    from src.config import load_config
    from src.synthetic import history_batch as hb

    cfg = load_config()
    day = date(2026, 11, 27)
    k = hb.batch_key(cfg, 50000, 42, day)
    assert hb.batch_key(cfg, 50000, 42, day) == k
    renamed = cfg.model_copy(deep=True)
    renamed.agent_fleet.other_agents[0].name = "CareDesk"
    assert hb.batch_key(renamed, 50000, 42, day) != k
    assert hb.batch_key(cfg, 50000, 42, date(2026, 11, 28)) != k


def test_generated_batch_is_deterministic():
    """Two runs of the same batch produce the same trace, observation and score ids and the
    same timestamps, so re-sending upserts in place instead of appending."""
    from src.synthetic import history_batch as hb
    from src.synthetic.trace_generator import generate_traces

    sent = []

    def run():
        lf = MagicMock()
        lf.api.ingestion.batch.side_effect = lambda batch: sent.append(batch) or MagicMock(errors=[])
        with patch("langfuse.Langfuse", return_value=lf):
            generate_traces(total=12, hero_share=0.5, days_back=30, business_hours=True, seed=7,
                            anchor=hb.anchor(date(2026, 11, 27)), trace_id_for=lambda i: hb.trace_id("k", i))
        events = [e for b in sent for e in b]
        sent.clear()
        return events

    def shape(events):
        return [(e.type, e.body.id, str(getattr(e.body, "start_time", None) or getattr(e.body, "timestamp", None)))
                for e in events]

    a, b = run(), run()
    assert shape(a) == shape(b) and len(a) > 12
    traces = [e.body for e in a if e.type == "trace-create"]
    assert [t.id for t in traces] == [hb.trace_id("k", i) for i in range(12)]
    assert all(str(t.timestamp) < "2026-11-28" for t in traces)
    assert {e.id for e in a}.isdisjoint({e.id for e in b})  # envelope ids stay random


def test_unseeded_builders_keep_random_ids():
    from src.synthetic.trace_generator import _build_promo_planner_events

    a = _build_promo_planner_events(random.Random(1), 30, True)
    b = _build_promo_planner_events(random.Random(1), 30, True)
    assert a[0].type == "trace-create" and a[0].body.id != b[0].body.id


# --- seed_all ----------------------------------------------------------------------------

def test_seed_all_aggregates_step_results():
    from scripts.seed_all import main

    def boom():
        raise RuntimeError("kaput")

    assert main([("a", lambda: 0), ("b", lambda: 0)]) == 0
    assert main([("a", lambda: 0), ("b", lambda: 2)]) == 2
    assert main([("a", lambda: 1), ("b", lambda: 2)]) == 1
    assert main([("a", boom), ("b", lambda: 0)]) == 1
    assert main([("a", lambda: 7)]) == 1  # any other code is a failure, never a warning
    ran = []
    assert main([("Generate Synthetic History", lambda: 1),
                 ("Seed Annotation Queue", lambda: ran.append(1) or 0)]) == 1
    assert ran == []


def test_seed_all_propagates_a_nested_seeder_failure(fake):
    from scripts.seed_all import main
    from scripts.seed_prompts import seed_prompts

    fake.fail[("POST", "/api/public/v2/prompts")] = ApiError(500, "boom")
    assert main([("Seed Prompts", lambda: seed_prompts(fake))]) == 1


def test_seed_all_default_steps_are_wired():
    from scripts.seed_all import default_steps

    names = [n for n, _ in default_steps()]
    assert names.index("Generate Synthetic History") < names.index("Seed Annotation Queue")
    assert names.index("Setup Score Configs") < names.index("Seed Annotation Queue")
