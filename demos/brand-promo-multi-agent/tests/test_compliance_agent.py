"""Tests for the compliance mini-graph aggregation — must fail CLOSED on a tool error."""

from __future__ import annotations

from src.agents.compliance_agent import _aggregate


def _state(**overrides):
    base = {
        "brief": "some campaign brief",
        "jurisdictions": None,
        "brand_findings": [],
        "regulatory_findings": [],
        "all_findings": [],
        "brand_error": None,
        "regulatory_error": None,
        "status": "APPROVED",
        "summary": "",
    }
    base.update(overrides)
    return base


def _finding(severity: str, source: str = "brand_guidelines"):
    return {"rule": "R1", "severity": severity, "detail": "d", "source": source}


def test_aggregate_approved_when_clean():
    out = _aggregate(_state())
    assert out["status"] == "APPROVED"


def test_aggregate_fails_closed_on_brand_tool_error():
    # Regression: a TOOL_ERROR produces no findings; aggregation must NOT treat
    # "no findings" as APPROVED — it must surface ERROR (fail closed).
    out = _aggregate(_state(brand_error="tool_error"))
    assert out["status"] == "ERROR"
    assert "not approved" in out["summary"].lower()


def test_aggregate_fails_closed_on_regulatory_tool_error():
    out = _aggregate(_state(regulatory_error="tool_error"))
    assert out["status"] == "ERROR"


def test_aggregate_error_takes_precedence_over_findings():
    # Even with findings present, an errored check means we can't certify.
    out = _aggregate(_state(brand_error="tool_error", brand_findings=[_finding("MEDIUM")]))
    assert out["status"] == "ERROR"


def test_aggregate_rejected_on_high_severity():
    out = _aggregate(_state(brand_findings=[_finding("HIGH")]))
    assert out["status"] == "REJECTED"


def test_aggregate_conditional_on_medium_severity():
    out = _aggregate(_state(regulatory_findings=[_finding("MEDIUM", source="regulatory")]))
    assert out["status"] == "CONDITIONAL"
