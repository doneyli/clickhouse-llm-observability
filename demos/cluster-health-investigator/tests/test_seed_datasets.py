"""seed_datasets.py reconcile and --from-traces capture against the request-level fake:
created / updated / unchanged per item, no writes when unchanged, archived and drifted items
repaired, user items untouched, failures propagated, capture from observations (v2 with
JSON-string io, and the v1 list on a 3.x server) with stable ids. Offline."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, os.path.dirname(HERE))

import seed_datasets as sd  # noqa: E402
import verify_demo as v  # noqa: E402
from fake_langfuse import FakeLangfuse  # noqa: E402

NOW = datetime(2099, 1, 1, 0, 30, tzinfo=timezone.utc)


def lf():
    return v.Langfuse("https://lf.example", "pk", "sk")


@pytest.fixture
def fake(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    monkeypatch.setattr(sd, "prepare_env", lambda: (True, "test env"))
    monkeypatch.setattr(sd, "client_from_env", lf)
    return fake


def test_golden_ids_are_the_ones_verify_checks():
    ids = {}
    for it in sd.golden_items():
        ids.setdefault(it["datasetName"], []).append(it["id"])
    assert ids == v.GOLDEN_ITEMS


def test_fresh_project_creates_everything(fake):
    assert sd.main([]) == 0
    assert set(fake.datasets) == set(sd.DATASETS)
    assert sorted(fake.items[sd.PLAN_DATASET]) == v.GOLDEN_ITEMS[sd.PLAN_DATASET]
    assert all(i["status"] == "ACTIVE" for i in fake.items[sd.WORKER_DATASET].values())


def test_unchanged_reseed_writes_nothing(fake, capsys):
    sd.main([])
    fake.calls.clear()
    capsys.readouterr()
    assert sd.main([]) == 0
    assert fake.writes() == []
    out = capsys.readouterr().out
    assert "unchanged dataset cluster-health/plan-quality" in out and "unchanged 18 item(s)" in out


def test_archived_and_drifted_items_are_repaired_and_user_items_left_alone(fake, capsys):
    sd.main([])
    fake.items[sd.PLAN_DATASET]["chp-03"]["status"] = "ARCHIVED"
    fake.items[sd.WORKER_DATASET]["chw-01"]["metadata"] = {"source": "edited"}
    fake.items[sd.PLAN_DATASET]["user-1"] = {"id": "user-1", "status": "ACTIVE", "input": {"symptom": "mine"}}
    fake.datasets[sd.PLAN_DATASET]["description"] = "edited"
    fake.calls.clear()
    capsys.readouterr()
    assert sd.main([]) == 0
    posted = sorted(b.get("id") or b.get("name") for m, p, _, b in fake.calls if m == "POST")
    assert posted == ["chp-03", "chw-01", sd.PLAN_DATASET]
    assert fake.items[sd.PLAN_DATASET]["chp-03"]["status"] == "ACTIVE"
    assert fake.items[sd.PLAN_DATASET]["user-1"]["input"] == {"symptom": "mine"}
    assert "updated   2 item(s): chp-03, chw-01" in capsys.readouterr().out


def test_partial_write_failure_fails(fake):
    sd.main([])
    fake.items[sd.WORKER_DATASET]["chw-02"]["input"] = {}
    fake.fail[("POST", "/api/public/dataset-items")] = v.ApiError(500, "boom")
    assert sd.main([]) == 1


def test_total_failure_fails(fake):
    fake.fail[("POST", "/api/public/v2/datasets")] = v.ApiError(500, "boom")
    assert sd.main([]) == 1


def test_a_required_read_failure_fails_and_writes_nothing_for_that_dataset(fake):
    sd.main([])
    fake.fail[("GET", "/api/public/dataset-items")] = v.ApiError(500, "boom")
    fake.calls.clear()
    assert sd.main([]) == 1
    assert ("POST", "/api/public/dataset-items") not in fake.writes()


def test_missing_env_fails_before_any_request(monkeypatch):
    monkeypatch.setattr(sd, "prepare_env", lambda: (False, "missing"))
    monkeypatch.setattr(sd, "client_from_env", lambda: pytest.fail("no client without an env"))
    assert sd.main([]) == 1


@pytest.mark.parametrize("v3_server", [False, True])
def test_capture_links_items_to_real_spans_with_stable_ids(monkeypatch, v3_server):
    fake = FakeLangfuse(v3_server=v3_server).install(monkeypatch)
    fake.add_run("old", ["disk_usage"], start="2098-12-30T00:00:00.000Z", symptom="old one")
    fake.add_run("t1", ["slow_queries", "query_errors"], rounds=2, symptom="slow dashboard")
    items = sd.captured_items(lf(), 1, now=NOW)
    plan = [i for i in items if i["datasetName"] == sd.PLAN_DATASET]
    work = [i for i in items if i["datasetName"] == sd.WORKER_DATASET]
    assert len(plan) == 1 and plan[0]["input"] == {"symptom": "slow dashboard"}
    orch = [o for o in fake.observations if o["traceId"] == "t1" and o["name"] == "orchestrator"][0]
    assert plan[0]["sourceObservationId"] == orch["id"] and plan[0]["id"] == f"chp-src-{orch['id']}"
    assert sorted(i["input"]["analysis_type"] for i in work) == ["query_errors", "slow_queries"]
    assert all(i["sourceTraceId"] == "t1" and i["id"].startswith("chw-src-") for i in work)
    if not v3_server:
        v2 = [c for c in fake.calls if c[1] == "/api/public/v2/observations"]
        assert {c[2]["fields"] for c in v2} == {"core,basic,io", "core,basic,io,metadata"}
    assert not any("/traces" in c[1] for c in fake.calls)


def test_from_traces_via_main_links_items_and_fails_on_a_read_error(fake):
    fake.add_run("t1", ["slow_queries"], start=v.iso(datetime.now(timezone.utc)))
    assert sd.main(["--from-traces", "3"]) == 0
    linked = [i for i in fake.items[sd.PLAN_DATASET].values() if i.get("sourceObservationId")]
    assert len(linked) == 1 and linked[0]["sourceTraceId"] == "t1"
    fake.fail[("GET", "/api/public/v2/observations")] = v.ApiError(500, "boom")
    assert sd.main(["--from-traces", "3"]) == 1


def test_capture_reseed_is_idempotent(monkeypatch):
    fake = FakeLangfuse().install(monkeypatch)
    fake.add_run("t1", ["slow_queries"])
    client = lf()
    sd.reconcile_datasets(client)
    first = sd.reconcile_items(client, sd.golden_items() + sd.captured_items(client, 5, now=NOW))
    assert list(first.values()).count("created") == 18 + 2
    fake.calls.clear()
    second = sd.reconcile_items(client, sd.golden_items() + sd.captured_items(client, 5, now=NOW))
    assert set(second.values()) == {"unchanged"} and fake.writes() == []
