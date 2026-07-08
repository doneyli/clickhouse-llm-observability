"""LangGraph compliance mini-graph with parallel brand + regulatory checks."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from src.tools.compliance import check_brand_guidelines, check_regulatory


class ComplianceFinding(TypedDict):
    rule: str
    severity: str  # HIGH, MEDIUM, LOW
    detail: str
    source: str  # "brand_guidelines" or "regulatory"


class ComplianceState(TypedDict):
    brief: str
    jurisdictions: list[str] | None
    brand_findings: list[ComplianceFinding]
    regulatory_findings: list[ComplianceFinding]
    all_findings: list[ComplianceFinding]
    status: str  # APPROVED, CONDITIONAL, REJECTED
    summary: str


def _run_brand_check(state: ComplianceState) -> dict[str, Any]:
    result = check_brand_guidelines.invoke({"brief": state["brief"]})
    findings: list[ComplianceFinding] = []
    if result.get("status") == "ok":
        for f in result.get("findings", []):
            findings.append(
                ComplianceFinding(
                    rule=f["rule"],
                    severity=f["severity"],
                    detail=f["detail"],
                    source="brand_guidelines",
                )
            )
    return {"brand_findings": findings}


def _run_regulatory_check(state: ComplianceState) -> dict[str, Any]:
    result = check_regulatory.invoke({
        "brief": state["brief"],
        "jurisdictions": state.get("jurisdictions"),
    })
    findings: list[ComplianceFinding] = []
    if result.get("status") == "ok":
        for f in result.get("findings", []):
            findings.append(
                ComplianceFinding(
                    rule=f.get("rule", "Unknown"),
                    severity=f["severity"],
                    detail=f["detail"],
                    source="regulatory",
                )
            )
    return {"regulatory_findings": findings}


def _aggregate(state: ComplianceState) -> dict[str, Any]:
    all_findings = state.get("brand_findings", []) + state.get("regulatory_findings", [])
    severities = {f["severity"] for f in all_findings}

    if "HIGH" in severities:
        status = "REJECTED"
        high = [f for f in all_findings if f["severity"] == "HIGH"]
        summary = (
            f"REJECTED: {len(high)} HIGH severity finding(s) require resolution before launch. "
            + "; ".join(f["rule"] for f in high)
        )
    elif "MEDIUM" in severities:
        status = "CONDITIONAL"
        med = [f for f in all_findings if f["severity"] == "MEDIUM"]
        summary = (
            f"CONDITIONAL: {len(med)} MEDIUM severity finding(s) require revision. "
            + "; ".join(f["rule"] for f in med)
        )
    else:
        status = "APPROVED"
        summary = "APPROVED: No compliance violations found."

    return {
        "all_findings": all_findings,
        "status": status,
        "summary": summary,
    }


def build_compliance_graph() -> Any:
    """Build and compile the LangGraph compliance mini-graph."""
    graph = StateGraph(ComplianceState)

    graph.add_node("brand_check", _run_brand_check)
    graph.add_node("regulatory_check", _run_regulatory_check)
    graph.add_node("aggregate", _aggregate)

    graph.set_entry_point("brand_check")
    # Sequential (LangGraph parallel fan-out requires Send API; keeping simple for now)
    graph.add_edge("brand_check", "regulatory_check")
    graph.add_edge("regulatory_check", "aggregate")
    graph.add_edge("aggregate", END)

    return graph.compile()


# Module-level compiled graph (lazy init)
_compliance_graph: Any | None = None


def run_compliance_check(
    brief: str,
    jurisdictions: list[str] | None = None,
    callbacks: list[Any] | None = None,
) -> ComplianceState:
    """Run the compliance mini-graph and return the final state."""
    global _compliance_graph
    if _compliance_graph is None:
        _compliance_graph = build_compliance_graph()

    initial_state: ComplianceState = {
        "brief": brief,
        "jurisdictions": jurisdictions,
        "brand_findings": [],
        "regulatory_findings": [],
        "all_findings": [],
        "status": "APPROVED",
        "summary": "",
    }
    config: dict[str, Any] | None = {"callbacks": callbacks} if callbacks else None
    result = _compliance_graph.invoke(initial_state, config=config) if config else _compliance_graph.invoke(initial_state)
    return result
