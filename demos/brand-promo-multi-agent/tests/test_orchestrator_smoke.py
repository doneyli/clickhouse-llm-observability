"""Smoke test for the orchestrator (mocks all LLM calls)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    return resp


MOCK_INTENT_RESPONSE = '{"intent": "plan_promo", "rationale": "User wants to plan a promo", "brand": "Brand A", "region": "Southeast", "retail_partner": null}'
MOCK_BRIEF_RESPONSE = "Campaign brief: Q3 promo for Brand A Classic in Southeast targeting back-to-school."

OOS_INTENT_RESPONSE = '{"intent": "out_of_scope", "rationale": "Not about promotions", "brand": null, "region": null, "retail_partner": null}'


def test_orchestrator_plan_promo_smoke():
    """Orchestrator runs end-to-end for a plan_promo intent without real LLM/crew calls."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = [
        _make_llm_response(MOCK_INTENT_RESPONSE),
        _make_llm_response(MOCK_BRIEF_RESPONSE),
    ]

    with (
        patch("src.agents.orchestrator.ChatAnthropic", return_value=mock_llm),
        patch("src.agents.orchestrator._research_node", return_value={"research_summary": "Research done.", "tools_called": ["query_sales"]}),
        patch("src.agents.orchestrator._strategy_node", return_value={"strategy_summary": "Strategy: 20% off.", "tools_called": ["strategy_crew"]}),
        patch("src.agents.orchestrator._compliance_node", return_value={"compliance_status": "APPROVED", "compliance_findings": [], "tools_called": []}),
    ):
        import src.agents.orchestrator as orch_module
        from src.agents.orchestrator import build_orchestrator
        orch_module._orchestrator = None

        orchestrator = build_orchestrator()
        result = orchestrator.invoke({
            "query": "Draft a Q3 promo for Brand A Classic in Southeast",
            "intent": None,
            "rationale": None,
            "brand": None,
            "region": None,
            "retail_partner": None,
            "research_summary": None,
            "strategy_summary": None,
            "compliance_status": None,
            "compliance_findings": [],
            "final_brief": None,
            "tools_called": [],
            "error": None,
        })

    assert result["final_brief"] is not None
    assert result["intent"] == "plan_promo"


def test_orchestrator_out_of_scope_smoke():
    """Orchestrator returns a polite refusal for out_of_scope intent."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = _make_llm_response(OOS_INTENT_RESPONSE)

    with patch("src.agents.orchestrator.ChatAnthropic", return_value=mock_llm):
        import src.agents.orchestrator as orch_module
        orch_module._orchestrator = None

        orchestrator = orch_module.build_orchestrator()
        result = orchestrator.invoke({
            "query": "What's the weather like today?",
            "intent": None,
            "rationale": None,
            "brand": None,
            "region": None,
            "retail_partner": None,
            "research_summary": None,
            "strategy_summary": None,
            "compliance_status": None,
            "compliance_findings": [],
            "final_brief": None,
            "tools_called": [],
            "error": None,
        })

    assert result["intent"] == "out_of_scope"
    assert result["final_brief"] is not None
    assert "PromoPlanner" in result["final_brief"]
