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
#
# These drive generate_history end to end: the real planner and generator build the batch,
# FakeClient.ingest upserts its events by id, and the read-back goes through the fake's
# Metrics API, so a wrong query shape or a count that misses a gap fails here.

TODAY = date(2026, 11, 27)
SMALL = 40  # traces per test batch: 8 PromoPlanner, 32 fleet


def _cfg():
    from src.config import load_config

    return load_config()


def _run_history(fake, cfg=None, today=TODAY, **kw):
    from scripts.generate_history import generate_history

    kw.setdefault("confirm_timeout", 0)
    return generate_history(total=SMALL, client=fake, ingest=fake.ingest, today=today, cfg=cfg or _cfg(),
                            sleep=lambda s: None, **kw)


def _stamps(fake):
    return {o.get("version") for o in fake.observations}


def test_history_creates_confirms_then_is_unchanged(fake):
    from src.synthetic import history_batch as hb

    assert _run_history(fake) == 0
    key = hb.batch_key(_cfg(), SMALL, 42, TODAY)
    assert set(fake.traces) == {hb.trace_id(key, i) for i in range(SMALL)}
    assert _stamps(fake) == {hb.stamp(key, SMALL)}
    rows = len(fake.observations)

    fake.calls.clear()
    fake.ingest_calls = 0
    assert _run_history(fake) == 0
    assert _run_history(fake, today=date(2026, 12, 1)) == 0  # still current inside the reuse window
    assert fake.ingest_calls == 0 and fake.writes() == [] and len(fake.observations) == rows


def test_history_repairs_missing_middle_rows_with_both_ends_present(fake):
    """132-6: the first and last trace are no proof. Rows lost from the middle of the batch
    read as partial, the re-run re-sends the same ids, and the next run writes nothing."""
    from src.synthetic import history_batch as hb

    key = hb.batch_key(_cfg(), SMALL, 42, TODAY)
    middle = {hb.trace_id(key, i) for i in range(15, 25)}
    fake.drop = lambda e: (getattr(e.body, "trace_id", None) or e.body.id) in middle
    assert _run_history(fake) == 1  # sent, but the read-back finds the gap: not confirmed
    assert hb.trace_id(key, 0) in fake.traces and hb.trace_id(key, SMALL - 1) in fake.traces
    assert not middle & set(fake.traces)

    fake.drop = None
    assert _run_history(fake) == 0  # partial: the same batch is re-sent and confirmed
    assert set(fake.traces) == {hb.trace_id(key, i) for i in range(SMALL)}
    rows, ids = len(fake.observations), {o["id"] for o in fake.observations}
    assert len(ids) == rows  # upserted in place, not appended

    fake.calls.clear()
    fake.ingest_calls = 0
    assert _run_history(fake) == 0
    assert fake.ingest_calls == 0 and fake.writes() == []


def test_history_repairs_a_lost_middle_observation_and_score(fake):
    """A trace can be partly there: one lost observation or score is a short group too."""
    from src.synthetic import history_batch as hb
    from src.synthetic.trace_generator import batch_counts, iter_batch

    assert _run_history(fake) == 0
    victim = next(o for o in fake.observations if o["name"] == "tool.query_inventory")
    fake.observations.remove(victim)
    assert _run_history(fake) == 0
    assert victim["id"] in {o["id"] for o in fake.observations}

    assert len(fake.scores) == 8  # 2 scored PromoPlanner traces x 4 judges in this batch
    lost = fake.scores.pop(3)
    assert _run_history(fake) == 0
    assert lost["id"] in {x["id"] for x in fake.scores}

    cfg = _cfg()
    synth = cfg.synthetic_history
    expected = batch_counts(iter_batch(SMALL, synth.hero_agent_share, synth.days_back,
                                       synth.business_hours_weighting, seed=42, cfg=cfg))
    key = hb.batch_key(cfg, SMALL, 42, TODAY)
    assert hb.batch_state(fake, key, SMALL, TODAY, synth.days_back, expected) == ("complete", [])
    fake.observations.remove(next(o for o in fake.observations if o["name"] == "historian"))
    state, short = hb.batch_state(fake, key, SMALL, TODAY, synth.days_back, expected)
    assert state == "partial" and short == ["promo_planner_run/historian: 7/8 observations"]


def test_history_unconfirmed_write_and_read_failures_exit_1(fake):
    from src.synthetic import history_batch as hb

    def failing(chunk):
        return ["event rejected"]

    from scripts.generate_history import generate_history
    assert generate_history(total=SMALL, client=FakeClient(), ingest=failing, today=TODAY, cfg=_cfg(),
                            confirm_timeout=0) == 1

    broken = FakeClient()
    broken.fail[("GET", "/api/public/v2/metrics")] = ApiError(500, "boom")
    assert _run_history(broken) == 1
    assert broken.ingest_calls == 0 and broken.traces == {}  # an unknown state never writes

    # landing slowly: polled until complete, within the bound
    slow, polls = FakeClient(), []
    key = hb.batch_key(_cfg(), SMALL, 42, TODAY)
    held: list = []
    slow.drop = lambda e: held.append(e) or True
    clock = iter(range(0, 10_000, 10))

    def sleep(s):
        polls.append(s)
        slow.drop = None
        slow.ingest(held)
        held.clear()

    from scripts.generate_history import generate_history as gh
    assert gh(total=SMALL, client=slow, ingest=slow.ingest, today=TODAY, cfg=_cfg(), confirm_timeout=60,
              poll_s=15, sleep=sleep, clock=lambda: next(clock)) == 0
    assert polls == [15] and hb.trace_id(key, 5) in slow.traces


@pytest.mark.parametrize("change", ["regions", "brand", "retailer", "sku", "fleet", "definition"])
def test_history_key_covers_every_generation_input(change, monkeypatch):
    """132-7: each input the generator reads changes the key; identical inputs do not."""
    from src.synthetic import history_batch as hb

    cfg = _cfg()
    k = hb.batch_key(cfg, 50000, 42, TODAY)
    assert hb.batch_key(_cfg(), 50000, 42, TODAY) == k
    other = cfg.model_copy(deep=True)
    definition = None
    if change == "regions":
        other.regions = [*other.regions[:-1], "Pacific Northwest Test"]
    elif change == "brand":
        other.catalog.brand_families[0].name = "Renamed Brand"
    elif change == "retailer":
        other.retail_partners = [*other.retail_partners[:-1], "Corner Store Co"]
    elif change == "sku":
        other.catalog.brand_families[0].hero_skus = [*other.catalog.brand_families[0].hero_skus[:-1], "NEW-SKU-1"]
    elif change == "fleet":
        other.agent_fleet.other_agents[0].name = "CareDesk"
    else:
        definition = hb.definition_digest(("trace_generator.py", "query_templates.py"))
    assert hb.batch_key(other, 50000, 42, TODAY, definition) != k


@pytest.mark.parametrize("change", ["regions", "brand", "retailer", "sku"])
def test_history_retheme_writes_new_content_and_removes_only_the_owned_old_batch(fake, change):
    """A region-, brand-, retailer- or SKU-only change is not reported unchanged: the new
    content is written and confirmed, the superseded owned batch is deleted, and rows the
    generator did not write survive. The re-themed config is then a no-op."""
    from src.synthetic import history_batch as hb

    assert _run_history(fake) == 0
    old = hb.batch_key(_cfg(), SMALL, 42, TODAY)
    fake.observations.append({"id": "user-1", "traceId": "user-trace", "traceName": "my_app",
                              "name": "user span", "startTime": "2026-11-26T10:00:00+00:00", "version": "v7"})
    cfg = _cfg()
    if change == "regions":
        cfg.regions = ["Atlantis"]
    elif change == "brand":
        for bf in cfg.catalog.brand_families:
            bf.name = f"Neo {bf.name}"
    elif change == "retailer":
        cfg.retail_partners = ["Corner Store Co"]
    else:
        for bf in cfg.catalog.brand_families:
            bf.hero_skus = [f"NEW-{s}" for s in bf.hero_skus]
    new = hb.batch_key(cfg, SMALL, 42, TODAY)
    assert new != old

    assert _run_history(fake, cfg=cfg) == 0
    assert set(fake.traces) == {hb.trace_id(new, i) for i in range(SMALL)}
    assert _stamps(fake) == {hb.stamp(new, SMALL), "v7"}
    assert any(o["id"] == "user-1" for o in fake.observations)
    deleted = [b["traceIds"] for m, p, _, b in fake.calls if m == "DELETE"]
    assert {t for chunk in deleted for t in chunk} == {hb.trace_id(old, i) for i in range(SMALL)}

    fake.calls.clear()
    fake.ingest_calls = 0
    assert _run_history(fake, cfg=cfg) == 0
    assert fake.ingest_calls == 0 and fake.writes() == []


def test_history_keep_superseded_and_failed_delete(fake):
    from src.synthetic import history_batch as hb

    assert _run_history(fake) == 0
    cfg = _cfg()
    cfg.regions = ["Atlantis"]
    assert _run_history(fake, cfg=cfg, keep_superseded=True) == 0
    assert len(_stamps(fake)) == 2 and not [c for c in fake.calls if c[0] == "DELETE"]
    fake.fail[("DELETE", "/api/public/traces")] = ApiError(500, "boom")
    assert _run_history(fake, cfg=cfg) == 1
    with pytest.raises(ValueError):
        hb.delete_batch(fake, "v7")  # never deletes by a version it did not write


def test_history_read_back_on_a_pinned_v3_server_uses_v1_metrics(fake):
    fake.v2_metrics = False
    assert _run_history(fake) == 0
    fake.calls.clear()
    assert _run_history(fake) == 0 and fake.writes() == []
    assert {p for _, p, _, _ in fake.calls} == {"/api/public/v2/metrics", "/api/public/metrics"}


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
                            anchor=hb.anchor(date(2026, 11, 27)), trace_id_for=lambda i: hb.trace_id("k", i),
                            version="brand-promo-history/k/12")
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
    assert all(e.body.version == "brand-promo-history/k/12" for e in a if e.type != "score-create")
    assert {e.id for e in a}.isdisjoint({e.id for e in b})  # envelope ids stay random


def test_unseeded_builders_keep_random_ids():
    from src.synthetic.trace_generator import _build_promo_planner_events

    a = _build_promo_planner_events(random.Random(1), 30, True)
    b = _build_promo_planner_events(random.Random(1), 30, True)
    assert a[0].type == "trace-create" and a[0].body.id != b[0].body.id
    assert a[0].body.version is None


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
