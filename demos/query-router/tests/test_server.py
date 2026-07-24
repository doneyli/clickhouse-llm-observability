"""FastAPI app construction + run() orchestration (pipeline + HTTP stubbed)."""

from fastapi.testclient import TestClient

import server


def test_app_constructs():
    assert server.app.title == "Query Router Demo"


def test_run_merges_decision_and_result(monkeypatch):
    monkeypatch.setattr(server, "classify", lambda q: {
        "route": "docs_complex", "confidence": 0.9, "rationale": "comparative",
        "fallback_triggered": False, "fallback_reason": None,
        "router_observation_id": "obs-1", "router_trace_id": "tr-1"})
    monkeypatch.setattr(server, "dispatch",
                        lambda decision, q, sid: {"answer": "grounded answer", "handled_by": "docs_complex"})
    out = server.run("compare X and Y", session_id="sess-x")
    assert out["question"] == "compare X and Y"
    assert out["route"] == "docs_complex"
    assert out["handled_by"] == "docs_complex"
    assert out["answer"] == "grounded answer"
    assert out["session_id"] == "sess-x"
    assert out["router_observation_id"] == "obs-1"


def test_query_endpoint(monkeypatch):
    monkeypatch.setattr(server, "classify", lambda q: {
        "route": "analytics_sql", "confidence": 0.96, "rationale": "numbers",
        "fallback_triggered": False, "fallback_reason": None})
    monkeypatch.setattr(server, "dispatch",
                        lambda decision, q, sid: {"answer": "42", "handled_by": "analytics_sql"})
    client = TestClient(server.app)
    r = client.post("/query", json={"question": "how many rides?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "analytics_sql"
    assert body["handled_by"] == "analytics_sql"
    assert body["session_id"].startswith("query-router-")


def test_health_reports_handlers(monkeypatch):
    def fake_get(url, timeout=None):
        return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(server.httpx, "get", fake_get)
    client = TestClient(server.app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["handlers"].keys()) == {"analytics_sql", "docs_simple", "docs_complex"}
    assert all(v == "ok" for v in body["handlers"].values())


def test_health_handles_unreachable(monkeypatch):
    def fake_get(url, timeout=None):
        raise Exception("boom")

    monkeypatch.setattr(server.httpx, "get", fake_get)
    client = TestClient(server.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert all("unreachable" in v for v in r.json()["handlers"].values())
