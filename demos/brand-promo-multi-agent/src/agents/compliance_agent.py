"""LangGraph compliance mini-graph: sequential brand + regulatory checks."""

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
    # Per-check errors (e.g. an injected TOOL_ERROR). Tracked so a failed check
    # NEVER silently reports APPROVED — compliance fails CLOSED.
    brand_error: str | None
    regulatory_error: str | None
    status: str  # APPROVED, CONDITIONAL, REJECTED, ERROR
    summary: str


def _run_brand_check(state: ComplianceState) -> dict[str, Any]:
    result = check_brand_guidelines.invoke({"brief": state["brief"]})
    findings: list[ComplianceFinding] = []
    error: str | None = None
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
    else:
        # Tool failed (e.g. injected TOOL_ERROR) — record it so aggregation can
        # fail closed instead of treating "no findings" as "approved".
        error = result.get("error") or result.get("status") or "brand check failed"
    return {"brand_findings": findings, "brand_error": error}


def _run_regulatory_check(state: ComplianceState) -> dict[str, Any]:
    result = check_regulatory.invoke({
        "brief": state["brief"],
        "jurisdictions": state.get("jurisdictions"),
    })
    findings: list[ComplianceFinding] = []
    error: str | None = None
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
    else:
        error = result.get("error") or result.get("status") or "regulatory check failed"
    return {"regulatory_findings": findings, "regulatory_error": error}


def _aggregate(state: ComplianceState) -> dict[str, Any]:
    all_findings = state.get("brand_findings", []) + state.get("regulatory_findings", [])
    severities = {f["severity"] for f in all_findings}

    # Fail CLOSED: if a check errored, we can't certify compliance — never let a
    # tool failure fall through to APPROVED just because it produced no findings.
    errors = [e for e in (state.get("brand_error"), state.get("regulatory_error")) if e]
    if errors:
        return {
            "all_findings": all_findings,
            "status": "ERROR",
            "summary": (
                "ERROR: compliance could not be fully verified (" + "; ".join(errors)
                + "). Treating as NOT approved pending a re-run."
            ),
        }

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
        "brand_error": None,
        "regulatory_error": None,
        "status": "APPROVED",
        "summary": "",
    }
    config: dict[str, Any] | None = {"callbacks": callbacks} if callbacks else None
    result = _compliance_graph.invoke(initial_state, config=config) if config else _compliance_graph.invoke(initial_state)
    return result
