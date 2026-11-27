"""seed_step_dataset.py --link-latest offline: runs are found through Observations v2 with
the field groups the data needs (or the v1 list on a 404), never the removed trace
endpoints, and linked items are stable. HTTP-level fake (fake_langfuse.py); no network."""
from __future__ import annotations

import pytest

import seed_step_dataset as step
import verify_demo as v
from fake_langfuse import FakeServer


@pytest.fixture
def server(monkeypatch):
    srv = FakeServer()
    monkeypatch.setattr(v.urllib.request, "urlopen", srv.urlopen)
    srv.add_run("t1", question="What are the most downloaded Python packages?\n\n[Retry 2: rejected]")
    srv.add_run("t2", question="Which YouTube videos have the most views?")
    srv.observations.append({"id": "t3-root", "traceId": "t3", "startTime": srv.observations[0]["startTime"],
                             "parentObservationId": None, "type": "SPAN", "name": "text-to-sql"})  # no analysis
    return srv


@pytest.fixture
def lf():
    return v.Langfuse("http://lf.test", "pk", "sk")


@pytest.mark.parametrize("v2", [True, False])
def test_latest_runs_link_the_analysis_generation(server, lf, v2):
    server.v2_observations = v2
    links = sorted(step.latest_analysis_observations(lf, 10))
    assert links == [("t1", "t1-gen", "What are the most downloaded Python packages?"),
                     ("t2", "t2-gen", "Which YouTube videos have the most views?")]
    paths = [p for _, p, _, _ in server.requests]
    assert not any(p.startswith("/api/public/traces") for p in paths)
    if v2:
        per_trace = [q for _, p, q, _ in server.requests if p == "/api/public/v2/observations" and "traceId" in q]
        assert per_trace and all(set(q["fields"].split(",")) >= {"io", "metadata"} for q in per_trace)


def test_without_io_and_metadata_groups_nothing_links(server, lf, monkeypatch):
    """The fake projects fields like the API: dropping the groups loses input and metadata."""
    real = v.Langfuse.cursor

    def thin(self, path, params, **kw):
        return real(self, path, dict(params, fields="core,basic"), **kw)

    monkeypatch.setattr(v.Langfuse, "cursor", thin)
    assert step.latest_analysis_observations(lf, 10) == []


def test_link_latest_upserts_stable_items(server, lf):
    report = []
    assert step.seed(lf, False, link_latest=10, report=lambda st, w: report.append(st)) == 0
    rows = server.items[step.DATASET_NAME]
    linked = [r for r in rows if (r.get("metadata") or {}).get("linked_from")]
    assert sorted(r["id"] for r in linked) == ["step-link-t1-gen", "step-link-t2-gen"]
    assert {(r["sourceTraceId"], r["sourceObservationId"]) for r in linked} == {("t1", "t1-gen"), ("t2", "t2-gen")}
    assert all(r["expectedOutput"]["databases"] == step._infer_expected(r["input"]["question"]) for r in linked)
    server.requests.clear()
    assert step.seed(lf, False, link_latest=10, report=lambda st, w: None) == 0
    assert server.writes() == [] and len(server.items[step.DATASET_NAME]) == 10


def test_a_failed_run_read_is_a_failure(server, lf):
    server.fail[("GET", "/api/public/v2/observations")] = 500
    got = []
    assert step.seed(lf, False, link_latest=5, report=lambda st, w: got.append((st, w))) == 1
    assert ("failed", "--link-latest: reading recent runs: HTTP 500: {\"message\": \"injected failure\"}") in got


def test_analysis_link_reads_flattened_and_string_metadata():
    obs = [{"id": "c", "type": "CHAIN", "startTime": "1", "metadata": '{"langchain.purpose": "query_analysis"}',
            "input": '{"question": "Q one"}'},
           {"id": "g", "type": "GENERATION", "startTime": "2", "metadata": {"purpose": "query_analysis"}}]
    assert step.analysis_link(obs) == ("g", "Q one")
    assert step.analysis_link([o for o in obs if o["type"] == "CHAIN"]) is None
