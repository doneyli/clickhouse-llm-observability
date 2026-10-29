"""
Retirement / annuity servicing — the second use case on the same pattern as death claims.

    load-request (MCP) ─► assemble ─► sop-rules ─► critical-guard ─► recommendation ─► case-manager review
                                        (reused nodes from claims/graph.py,                (LangGraph interrupt)
                                         own prompt: annuity-service-rules)

An in-force request from a living owner — a partial withdrawal, a full surrender or a replacement (1035
exchange) — is checked against the servicing SOPs: surrender charges, early-distribution tax, RMDs, rider
forfeiture, replacement disclosure and supervisory review, the consumer profile, senior consumers, agent
compensation and New York's best-interest standard. The ground truth comes from the same SME-codified rules and
oracle; the same evaluators score it; the critical-rule guard covers its three critical rules (replacement in
the surrender period, a contract under 36 months old, a time horizon shorter than the new surrender period).

One request = one Langfuse session; the trace name is `annuity-service`.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field

from claims import calc, claimsdb, evals, oracle, prompts
from claims import config as settings
from claims.graph import (GUARD_ENABLED, ClaimState, Runtime, _cost, _llm_cfg, _mcp, _nest, _prompt, _rt, _score,
                          critical_guard, examiner_review, make_llm, record_review, sop_rules)

TRACE_NAME = "annuity-service"
RULES_PROMPT = "annuity-service-rules"
REC_PROMPT = "annuity-service-recommendation"


class ServiceRecommendation(BaseModel):
    decision: Literal["APPROVE", "PEND", "REFER_REVIEW"]
    examiner_note: str = Field(description="The decision, the rules that drive it, and the next actions")
    next_actions: list[str] = Field(default_factory=list)
    approvals_required: list[str] = Field(default_factory=list,
                                          description="e.g. supervising principal, Annuity Compliance, senior desk")


def _service_decision(rules: list[dict]) -> str:
    level = max([{"refer_review": 2, "hold": 1}.get(r["effect"], 0) for r in rules] or [0])
    return {2: "REFER_REVIEW", 1: "PEND", 0: "APPROVE"}[level]


async def load_request(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    rec = await _mcp(rt, cfg, "get_service_request", {"request_id": state["claim_id"]})
    if "error" in rec:
        raise RuntimeError(f"service request {state['claim_id']}: {rec}")
    return {"header": rec}


async def assemble_request(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config
    rt = _rt(cfg)
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="span", name="assemble-request-file", input={"sources": ["annuity-admin"]},
            metadata={"role": "assemble"}) as s:
        request_file, observed = claimsdb.service_request_file(state["header"])
        s.update(output=request_file)
    return {"claim_file": request_file, "observed": observed}


async def recommendation(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config
    rt = _rt(cfg)
    rf, obs_req, rules = state["claim_file"], state["observed"], state["rules"]
    system, client, model, max_tokens = _prompt(rt, REC_PROMPT)
    llm = make_llm(model, max_tokens, "service-recommendation-llm").with_structured_output(
        ServiceRecommendation, method="json_schema", include_raw=True)
    lines = "\n".join(f"- {r['rule_id']} ({r.get('type')}): {r.get('action')} — {r.get('rationale')}" for r in rules)
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="agent", name="service-recommendation", input={"request_file": rf, "rules": rules},
            metadata={"role": "service_recommendation", "model": model,
                      "prompt_version": str(rt.served.get(REC_PROMPT))}) as obs:
        rt.obs["adjudication"] = obs.id
        out = await llm.ainvoke([SystemMessage(content=system), HumanMessage(
            content=f"REQUEST FILE:\n{json.dumps(rf, indent=1)}\n\nAPPLICABLE RULES:\n{lines or '- none'}")],
            _llm_cfg(cfg, client, "service-recommendation-llm"))
        rec = out.get("parsed")
        rec = rec.model_dump() if rec else {"decision": "PEND", "examiner_note": "Recommendation failed; review manually.",
                                            "next_actions": [], "approvals_required": []}
        ok, open_ = evals.no_pay_with_open_block(rec["decision"], obs_req)
        if not ok and GUARD_ENABLED and rt.mode != "experiment":
            forced = _service_decision(evals.codified_critical(obs_req))
            rec = {**rec, "decision": forced, "guard_override": f"agent recommended APPROVE; {', '.join(open_)} applies",
                   "examiner_note": f"[Critical-rule guard: changed APPROVE to {forced} because {', '.join(open_)} "
                                    f"applies.] " + rec["examiner_note"]}
        calculation = calc.calculate_service(obs_req, rules, rec["decision"])
        obs.update(output={**rec, "calculation": calculation},
                   level="WARNING" if rec.get("guard_override") else "DEFAULT")
    _score(rt, "adjudication", "no-pay-with-open-block", 1 if ok else 0, "BOOLEAN",
           f"agent recommended APPROVE while {', '.join(open_)} applies" if not ok else
           ("open critical rules respected: " + ", ".join(open_) if open_ else "no open critical rule"))
    _score(rt, "adjudication", "decision", rec["decision"], "CATEGORICAL")
    return {"recommendation": rec, "calculation": calculation}


def _build(full: bool):
    g = StateGraph(ClaimState)
    steps = ([("load-request", load_request), ("assemble", assemble_request)] if full else []) + \
        [("sop-rules", sop_rules)] + ([("critical-guard", critical_guard)] if full else []) + \
        [("recommendation", recommendation)] + ([("case-manager-review", examiner_review)] if full else [])
    for name, fn in steps:
        g.add_node(name, fn)
    g.add_edge(START, steps[0][0])
    for (a, _), (b, _) in zip(steps, steps[1:]):
        g.add_edge(a, b)
    g.add_edge(steps[-1][0], END)
    return g.compile(checkpointer=InMemorySaver()) if full else g.compile()


SERVICE_GRAPH = _build(full=True)
SERVICE_STEP = _build(full=False)


async def _traced(request_id: str, event: str, graph_input, *, mode: str, labels: Optional[dict], model: Optional[str],
                  user_id: str, session_id: Optional[str], tags: Optional[list], examiner: Optional[dict],
                  thread_id: str) -> dict:
    lf = settings.get_langfuse()
    req = claimsdb.get_service(request_id)
    labels = labels or {}
    trace_tags = sorted(set(["annuities", f"line:{req['line']}", f"state:{req['state']}", f"event:{event}",
                             f"request:{req['request_type']}", f"mode:{mode}"] + (tags or [])))
    _, rules_client, _ = prompts.get(lf, RULES_PROMPT, labels.get(RULES_PROMPT))
    version = f"{settings.RELEASE} · service-rules v{getattr(rules_client, 'version', 'fallback')}"
    t0 = time.perf_counter()
    with propagate_attributes(session_id=session_id, user_id=user_id, tags=trace_tags, version=version,
                              trace_name=TRACE_NAME, metadata={"request_id": request_id, "event": event,
                                                               "request_type": req["request_type"]}):
        with lf.start_as_current_observation(as_type="agent", name=TRACE_NAME,
                                             input={"request_id": request_id, "event": event},
                                             metadata={"role": "orchestrator"}) as root:
            rt = Runtime(langfuse=lf, handler=CallbackHandler(), labels=labels, model=model, mode=mode,
                         examiner=examiner, rules_prompt=RULES_PROMPT, case_label="REQUEST FILE",
                         trace_attrs={"trace_id": root.trace_id, "session_id": session_id, "user_id": user_id,
                                      "trace_name": TRACE_NAME, "version": version,
                                      "environment": settings.ENVIRONMENT})
            run_cfg = {"callbacks": [rt.handler], "recursion_limit": 30, "run_name": "annuity-service-graph",
                       "configurable": {"thread_id": thread_id, "runtime": rt}}
            if isinstance(graph_input, dict):
                graph_input = {**graph_input, "rec_trace_id": root.trace_id, "rec_root_id": root.id}
            if event == "case_manager_review":
                state = await SERVICE_GRAPH.ainvoke(graph_input, run_cfg)
            else:
                async with streamablehttp_client(settings.MCP_URL) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        rt.session = session
                        state = await SERVICE_GRAPH.ainvoke(graph_input, run_cfg)
            rec = state.get("recommendation") or {}
            out = {"recommendation": rec.get("decision"), "final_decision": state.get("final_decision"),
                   "rules": sorted(evals.cited_ids(state.get("rules") or [])), "guard": state.get("guard"),
                   "calculation": state.get("calculation"), "awaiting_case_manager": bool(state.get("__interrupt__"))}
            root.update(output=out)
            trace_id, root_id = root.trace_id, root.id
    usd, _, _ = _cost(rt.usage)
    if event != "case_manager_review":
        lf.create_score(trace_id=trace_id, observation_id=root_id, name="claim-latency-s",
                        value=round(time.perf_counter() - t0, 2), data_type="NUMERIC")
        if state.get("guard") is not None:
            lf.create_score(trace_id=trace_id, observation_id=root_id, name="guard-triggered",
                            value=1 if state["guard"]["triggered"] else 0, data_type="BOOLEAN")
    return {**out, "request_id": request_id, "trace_id": trace_id, "root_id": root_id, "thread_id": thread_id,
            "trace_url": settings.trace_url(trace_id),
            "session_url": settings.session_url(session_id) if session_id else None, "state": state,
            "prompt_versions": rt.served}


async def run_service(request_id: str, *, mode: str = "workbench", labels: Optional[dict] = None,
                      model: Optional[str] = None, tags: Optional[list] = None, session_id: Optional[str] = "request",
                      examiner: Optional[dict] = None) -> dict:
    sid = request_id if session_id == "request" else session_id
    res = await _traced(request_id, "request_received", {"claim_id": request_id}, mode=mode, labels=labels,
                        model=model, user_id="annuity-service-system", session_id=sid, tags=tags, examiner=examiner,
                        thread_id=f"{request_id}:{uuid.uuid4().hex[:8]}")
    if mode == "auto" and examiner is not None:
        record_review(res["trace_id"], res["root_id"], res["recommendation"], examiner)
    return res


async def resume_service(request_id: str, thread_id: str, review: dict, *, labels: Optional[dict] = None) -> dict:
    snap = SERVICE_GRAPH.get_state({"configurable": {"thread_id": thread_id}}).values
    res = await _traced(request_id, "case_manager_review", Command(resume=review), mode="workbench", labels=labels,
                        model=None, user_id=review.get("examiner_id") or "case-manager", session_id=request_id,
                        tags=["case-manager"], examiner=None, thread_id=thread_id)
    record_review(snap.get("rec_trace_id"), snap.get("rec_root_id"), (snap.get("recommendation") or {}).get("decision"),
                  review)
    return res


async def run_service_step(request_id: str, request_file: dict, observed: dict, *, labels: Optional[dict] = None,
                           model: Optional[str] = None) -> dict:
    """Rules + recommendation on a known request file, no guard — what the servicing experiments evaluate."""
    lf = settings.get_langfuse()
    with propagate_attributes(trace_name="annuity-service-step", tags=["experiment", "step:service-rules"],
                              metadata={"request_id": request_id}):
        with lf.start_as_current_observation(as_type="agent", name="annuity-service-step",
                                             input={"request_id": request_id, "request_file": request_file},
                                             metadata={"role": "rules_step"}) as root:
            rt = Runtime(langfuse=lf, handler=CallbackHandler(), labels=labels or {}, model=model, mode="experiment",
                         rules_prompt=RULES_PROMPT, case_label="REQUEST FILE", trace_attrs={"trace_id": root.trace_id})
            state = await SERVICE_STEP.ainvoke(
                {"claim_id": request_id, "claim_file": request_file, "observed": observed},
                {"callbacks": [rt.handler], "run_name": "annuity-service-step", "configurable": {"runtime": rt}})
            out = {"rules": state["rules"], "decision": state["recommendation"]["decision"],
                   "calculation": state["calculation"], "citation_validity": state["citation_validity"],
                   "retrieval_coverage": state["retrieval_coverage"], "prompt_versions": rt.served}
            root.update(output={"decision": out["decision"], "rules": sorted(evals.cited_ids(out["rules"]))})
    return out
