"""Classifier + confidence-threshold gating tests (Anthropic call stubbed)."""

import json

import router


def _client(text):
    class C:
        class messages:
            @staticmethod
            def create(**kw):
                return type("M", (), {"content": [type("B", (), {"text": text})()]})()
    return C()


def test_clear_route_high_confidence(monkeypatch):
    monkeypatch.setattr(router, "CONFIDENCE_THRESHOLD", 0.70)
    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    monkeypatch.setattr(router, "_anthropic",
                        lambda: _client(json.dumps({"route": "analytics_sql",
                                                    "confidence": 0.95, "rationale": "live numbers"})))
    res = router.classify("how many taxi rides in 2015?")
    assert res["route"] == "analytics_sql"
    assert res["confidence"] == 0.95
    assert res["fallback_triggered"] is False
    assert res["fallback_reason"] is None


def test_low_confidence_falls_back(monkeypatch):
    monkeypatch.setattr(router, "CONFIDENCE_THRESHOLD", 0.70)
    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    monkeypatch.setattr(router, "_anthropic",
                        lambda: _client(json.dumps({"route": "docs_simple",
                                                    "confidence": 0.55, "rationale": "unsure"})))
    res = router.classify("is clickhouse fast?")
    assert res["route"] == "fallback"
    assert res["fallback_reason"] == "low_confidence"
    assert res["fallback_triggered"] is True


def test_out_of_scope_falls_back(monkeypatch):
    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    monkeypatch.setattr(router, "_anthropic",
                        lambda: _client(json.dumps({"route": "out_of_scope",
                                                    "confidence": 0.9, "rationale": "poem"})))
    res = router.classify("write me a poem")
    assert res["route"] == "fallback"
    assert res["fallback_reason"] == "out_of_scope"


def test_unknown_route_is_registry_drift(monkeypatch):
    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    monkeypatch.setattr(router, "_anthropic",
                        lambda: _client(json.dumps({"route": "billing",
                                                    "confidence": 0.99, "rationale": "drift"})))
    res = router.classify("charge dispute")
    assert res["route"] == "fallback"
    assert res["fallback_reason"] == "unknown_route"


def test_malformed_output_falls_back(monkeypatch):
    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    monkeypatch.setattr(router, "_anthropic", lambda: _client("not json at all"))
    res = router.classify("anything")
    assert res["route"] == "fallback"
    assert res["fallback_reason"] == "malformed_output"
    assert res["confidence"] == 0.0


def test_fenced_json_is_tolerated(monkeypatch):
    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    fenced = "```json\n" + json.dumps({"route": "docs_complex", "confidence": 0.88,
                                       "rationale": "comparative"}) + "\n```"
    monkeypatch.setattr(router, "_anthropic", lambda: _client(fenced))
    res = router.classify("compare X and Y")
    assert res["route"] == "docs_complex"
    assert res["confidence"] == 0.88


def test_sql_blindness_fault_rewrites_prompt(monkeypatch):
    """With ROUTER_FAULT=sql-blindness the analytics route is removed from the
    prompt; the (stubbed) model then confidently returns docs_simple -> a
    silent misroute, which is the demo's Act 3 signature failure."""
    captured = {}

    class C:
        class messages:
            @staticmethod
            def create(**kw):
                captured["prompt"] = kw["messages"][0]["content"]
                return type("M", (), {"content": [type("B", (), {"text": json.dumps(
                    {"route": "docs_simple", "confidence": 0.93, "rationale": "docs"})})()]})()

    monkeypatch.setattr(router, "ROUTER_FAULT", "sql-blindness")
    monkeypatch.setattr(router, "_anthropic", lambda: C())
    res = router.classify("how many taxi rides in July 2015?")
    assert "analytics_sql" not in captured["prompt"]  # route blinded out of the prompt
    assert res["route"] == "docs_simple"  # confidently wrong


def test_model_override_is_used(monkeypatch):
    """The experiment runner varies model without touching handlers/threshold."""
    captured = {}

    class C:
        class messages:
            @staticmethod
            def create(**kw):
                captured["model"] = kw["model"]
                return type("M", (), {"content": [type("B", (), {"text": json.dumps(
                    {"route": "docs_simple", "confidence": 0.9, "rationale": "x"})})()]})()

    monkeypatch.setattr(router, "ROUTER_FAULT", "")
    monkeypatch.setattr(router, "_anthropic", lambda: C())
    router.classify("what is a vector index?", model="claude-sonnet-4-6")
    assert captured["model"] == "claude-sonnet-4-6"
