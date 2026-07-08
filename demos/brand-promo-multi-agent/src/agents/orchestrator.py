"""LangGraph orchestrator - routes queries through research, strategy, and compliance."""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from src.agents.compliance_agent import run_compliance_check
from src.config import load_config
from src.prompts.orchestrator import PROMPTS


class OrchestratorState(TypedDict, total=False):
    query: str
    intent: str | None
    rationale: str | None
    brand: str | None
    region: str | None
    retail_partner: str | None
    research_summary: str | None
    strategy_summary: str | None
    compliance_status: str | None
    compliance_findings: list[dict[str, Any]]
    final_brief: str | None
    tools_called: list[str]
    error: str | None
    callbacks: list[Any] | None


def _build_system_prompt() -> str:
    cfg = load_config()
    return (
        PROMPTS["system"]
        .replace("{{customer_name}}", cfg.customer.display_name)
        .replace("{{brands}}", ", ".join(cfg.all_brand_names()))
        .replace("{{regions}}", ", ".join(cfg.regions))
        .replace("{{retail_partners}}", ", ".join(cfg.retail_partners))
        .replace("{{regulatory_bodies}}", ", ".join(cfg.compliance.regulatory_bodies))
    )


def _get_llm(callbacks: list[Any] | None = None) -> ChatAnthropic:
    cfg = load_config()
    kwargs: dict[str, Any] = {
        "model": cfg.llm.models.orchestrator,
        "max_tokens": cfg.llm.max_tokens_default,
    }
    if callbacks:
        kwargs["callbacks"] = callbacks
    return ChatAnthropic(**kwargs)


def _classify_intent_node(state: OrchestratorState) -> dict[str, Any]:
    llm = _get_llm(callbacks=state.get("callbacks"))
    system = _build_system_prompt()
    prompt = PROMPTS["classify_intent"].replace("{{query}}", state["query"])

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=prompt),
    ]
    response = llm.invoke(messages)
    content = response.content

    # Extract JSON from response
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            return {
                "intent": parsed.get("intent", "plan_promo"),
                "rationale": parsed.get("rationale", ""),
                "brand": parsed.get("brand"),
                "region": parsed.get("region"),
                "retail_partner": parsed.get("retail_partner"),
                "tools_called": [*state.get("tools_called", []), "classify_intent"],
            }
        except json.JSONDecodeError:
            pass

    return {
        "intent": "plan_promo",
        "rationale": "Could not parse intent, defaulting to plan_promo",
        "brand": None,
        "region": None,
        "retail_partner": None,
        "tools_called": [*state.get("tools_called", []), "classify_intent"],
    }


def _research_node(state: OrchestratorState) -> dict[str, Any]:
    from src.agents.research_crew import run_research_crew

    research_result = run_research_crew(
        query=state["query"],
        brand=state.get("brand"),
        region=state.get("region"),
        callbacks=state.get("callbacks"),
    )
    tools = [*state.get("tools_called", []), "query_sales", "query_inventory", "get_market_trends", "research_crew"]
    return {
        "research_summary": research_result,
        "tools_called": tools,
    }


def _strategy_node(state: OrchestratorState) -> dict[str, Any]:
    from src.agents.strategy_crew import run_strategy_crew

    strategy_result = run_strategy_crew(
        query=state["query"],
        brand=state.get("brand"),
        region=state.get("region"),
        research_summary=state.get("research_summary") or "",
        callbacks=state.get("callbacks"),
    )
    tools = [*state.get("tools_called", []), "strategy_crew"]
    return {
        "strategy_summary": strategy_result,
        "tools_called": tools,
    }


def _compliance_node(state: OrchestratorState) -> dict[str, Any]:
    # Build a brief to check from research + strategy context
    brief_for_check = state.get("query", "")
    if state.get("strategy_summary"):
        brief_for_check += "\n" + state["strategy_summary"][:500]

    result = run_compliance_check(
        brief=brief_for_check,
        callbacks=state.get("callbacks"),
    )
    tools = [*state.get("tools_called", []), "check_brand_guidelines", "check_regulatory"]
    return {
        "compliance_status": result["status"],
        "compliance_findings": result["all_findings"],
        "tools_called": tools,
    }


def _compose_brief_node(state: OrchestratorState) -> dict[str, Any]:
    cfg = load_config()
    llm = _get_llm(callbacks=state.get("callbacks"))
    system = _build_system_prompt()

    findings_text = ""
    if state.get("compliance_findings"):
        findings_text = "\n".join(
            f"- [{f['severity']}] {f['rule']}: {f['detail']}"
            for f in state["compliance_findings"]
        )

    prompt = (
        PROMPTS["compose_brief"]
        .replace("{{customer_name}}", cfg.customer.display_name)
        .replace("{{query}}", state["query"])
        .replace("{{intent}}", state.get("intent") or "plan_promo")
        .replace("{{brand}}", state.get("brand") or "unspecified")
        .replace("{{region}}", state.get("region") or "all regions")
        .replace("{{research_summary}}", (state.get("research_summary") or "No research data.")[:1000])
        .replace("{{strategy_summary}}", (state.get("strategy_summary") or "No strategy generated.")[:1000])
        .replace("{{compliance_status}}", state.get("compliance_status") or "APPROVED")
        .replace("{{compliance_findings}}", findings_text or "None")
    )

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=prompt),
    ]
    response = llm.invoke(messages)
    return {"final_brief": response.content}


def _out_of_scope_node(state: OrchestratorState) -> dict[str, Any]:
    cfg = load_config()
    refusal = PROMPTS["out_of_scope_response"].replace(
        "{{brands}}", ", ".join(cfg.all_brand_names())
    )
    return {"final_brief": refusal}


def _route_after_classify(state: OrchestratorState) -> str:
    intent = state.get("intent", "plan_promo")
    if intent == "out_of_scope":
        return "out_of_scope"
    if intent == "compliance_check_only":
        return "compliance_only"
    return "research"


def _route_after_compliance(state: OrchestratorState) -> str:
    return "compose"


def build_orchestrator() -> Any:
    """Build and compile the full LangGraph orchestrator."""
    graph = StateGraph(OrchestratorState)

    graph.add_node("classify_intent", _classify_intent_node)
    graph.add_node("research_node", _research_node)
    graph.add_node("strategy_node", _strategy_node)
    graph.add_node("compliance_node", _compliance_node)
    graph.add_node("compose_brief", _compose_brief_node)
    graph.add_node("out_of_scope", _out_of_scope_node)

    graph.set_entry_point("classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        _route_after_classify,
        {
            "out_of_scope": "out_of_scope",
            "compliance_only": "compliance_node",
            "research": "research_node",
        },
    )

    graph.add_edge("out_of_scope", END)
    graph.add_edge("research_node", "strategy_node")
    graph.add_edge("strategy_node", "compliance_node")
    graph.add_edge("compliance_node", "compose_brief")
    graph.add_edge("compose_brief", END)

    return graph.compile()


_orchestrator: Any | None = None


def run_orchestrator(
    query: str,
    callbacks: list[Any] | None = None,
    config: dict[str, Any] | None = None,
) -> OrchestratorState:
    """Run the full orchestrator for a user query.

    callbacks: legacy v3 path — threaded into state so each `_get_llm()`
        attaches them explicitly. Kept for backward compat.
    config: RunnableConfig-shaped dict (langfuse v4 path). When provided,
        passed to `_orchestrator.invoke(state, config=config)` so the
        LangChain runtime propagates `callbacks` + `metadata` + `tags` to
        every nested LLM call without state-threading. Build via
        `src.observability.make_observability_run_config(...)`.

    The Langfuse (callback-based) backend is driven from this callsite by
    passing the appropriate config.
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = build_orchestrator()

    initial_state: OrchestratorState = {
        "query": query,
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
        "callbacks": callbacks,
    }
    if config is not None:
        return _orchestrator.invoke(initial_state, config=config)
    return _orchestrator.invoke(initial_state)
