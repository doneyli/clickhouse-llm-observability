"""Dispatch + fallback + escalation tests (HTTP + Anthropic stubbed)."""

import httpx

import handlers


def _fallback_client():
    class C:
        class messages:
            @staticmethod
            def create(**kw):
                return type("M", (), {"content": [type("B", (), {"text": "best-effort answer"})()]})()
    return C()


def test_dispatch_success_routes_to_handler(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return type("R", (), {"raise_for_status": lambda self: None,
                              "json": lambda self: {"answer": "42 rides", "session_id": json["session_id"]}})()

    monkeypatch.setattr(handlers.httpx, "post", fake_post)
    decision = {"route": "analytics_sql", "confidence": 0.95, "rationale": "numbers",
                "fallback_triggered": False, "fallback_reason": None}
    res = handlers.dispatch(decision, "how many taxi rides?", "sess-1")
    assert res["handled_by"] == "analytics_sql"
    assert res["answer"] == "42 rides"
    assert captured["url"].endswith("/query")
    assert captured["payload"]["question"] == "how many taxi rides?"
    # No trace_context added when Langfuse disabled (obs is None).
    assert "trace_context" not in captured["payload"]


def test_dispatch_handler_unreachable_degrades_to_fallback(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(handlers.httpx, "post", fake_post)
    monkeypatch.setattr(handlers, "_anthropic", _fallback_client)
    decision = {"route": "docs_simple", "confidence": 0.9, "rationale": "docs",
                "fallback_triggered": False, "fallback_reason": None}
    res = handlers.dispatch(decision, "what is a vector index?", "sess-2")
    assert res["handled_by"] == "fallback"
    assert res["escalation_reason"] == "handler_unreachable"
    assert res["answer"] == "best-effort answer"


def test_dispatch_http_status_error_degrades_to_fallback(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        def raise_for_status(self):
            raise httpx.HTTPStatusError("500", request=None, response=None)
        return type("R", (), {"raise_for_status": raise_for_status, "json": lambda self: {}})()

    monkeypatch.setattr(handlers.httpx, "post", fake_post)
    monkeypatch.setattr(handlers, "_anthropic", _fallback_client)
    decision = {"route": "docs_complex", "confidence": 0.9, "rationale": "hard",
                "fallback_triggered": False, "fallback_reason": None}
    res = handlers.dispatch(decision, "compare X and Y", "sess-3")
    assert res["handled_by"] == "fallback"
    assert res["escalation_reason"] == "handler_unreachable"


def test_fallback_route_never_hits_http(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dispatch must not call httpx for a fallback decision")

    monkeypatch.setattr(handlers.httpx, "post", boom)
    monkeypatch.setattr(handlers, "_anthropic", _fallback_client)
    decision = {"route": "fallback", "confidence": 0.4, "rationale": "vague",
                "fallback_triggered": True, "fallback_reason": "low_confidence"}
    res = handlers.dispatch(decision, "is clickhouse fast?", "sess-4")
    assert res["handled_by"] == "fallback"
    assert res["escalation_reason"] == "low_confidence"


def test_registry_keys_match_router_routes():
    import router
    assert tuple(sorted(handlers.HANDLERS.keys())) == tuple(sorted(router.ROUTES))
