"""
Northwind Life & Annuity death-claims orchestrator — LangGraph, instrumented with Langfuse.

    load-claim ─► intake ─► policy-coverage ─► screening ─► assemble ─► sop-rules ─► critical-guard
     (MCP)        (A2A →      (agent + MCP        (MCP)      (code)     (retriever     (code: the 8
                  Strands)     tools)                                   + LLM)          codified rules)
                                                                                            │
                     correspondence ◄── examiner-review (LangGraph interrupt) ◄── adjudication
                       (LLM letter)       (a human approves or overrides)         (LLM + calculator)

One claim = one Langfuse session. Each event is one trace:
  * `documents_received`  runs the graph up to the examiner interrupt (the recommendation)
  * `examiner_review`     resumes it with the examiner's decision, then drafts the letter
  * `examiner_question`   the examiner assistant answers a question about the claim

What lands in Langfuse for the recommendation trace:
  * root `claims-orchestrator` (agent) with the event's input and output; metadata.role = orchestrator
  * LangGraph nodes and LLM generations (CallbackHandler), each generation linked to its prompt version
  * MCP client spans + the MCP server's spans; the A2A call + the Strands service's spans — one trace,
    three processes, two frameworks
  * one observation per step with metadata.role (doc_intake, policy, screening, sop_rules, guard,
    adjudication, correspondence) — what managed judges select on
  * deterministic online scores on those observations (see claims/evals.py)

Regulated steps (screening, the critical-rule guard, the payment calculator) are code, not LLM routing.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TypedDict

from langchain_core.callbacks import Callbacks
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langfuse import LangfuseOtelSpanAttributes, propagate_attributes
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from opentelemetry import trace as otel_trace
from pydantic import BaseModel, Field

from claims import a2a_client, business, calc, claimsdb, evals, notices, oracle, prompts, sops
from claims import config as settings

TRACE_NAME = "claims-orchestrator"
GUARD_ENABLED = os.environ.get("CLAIMS_GUARD", "on").lower() not in ("off", "false", "0")
# List prices (USD / 1M tokens) for the per-claim cost score — an estimate for charting;
# the trace cost Langfuse computes is authoritative.
_PRICE = {"claude-sonnet-4-6": (3.0, 15.0), "claude-sonnet-5-5": (3.0, 15.0), "claude-haiku-5-5": (1.0, 5.0),
          "claude-haiku-4-5": (1.0, 5.0), "claude-opus-5-5": (5.0, 25.0)}


# ───────────────────────────── structured outputs ─────────────────────────────

class ExtractedRule(BaseModel):
    rule_id: str = Field(description="Rule id exactly as printed in the SOP, e.g. LIFE-CONT-001")
    version: Optional[int] = Field(default=None, description="The RULE version printed with the rule, or the older "
                                   "rule version named in the revision history when that is the one in effect. "
                                   "Never the SOP document's version.")
    sop_id: str = Field(description="SOP id, e.g. SOP-LIFE-01")
    section: str = Field(description="SOP section number")
    type: str = Field(description="BLOCK, PEND, DOC_REQUIRED, DENY, ROUTE, NOTIFY, PAY_CALC or COMMISSION")
    action: str = Field(description="What the rule requires the examiner to do")
    rationale: str = Field(description="One sentence naming the claim facts that meet the rule's conditions")


class RuleExtraction(BaseModel):
    rules: list[ExtractedRule]


class Recommendation(BaseModel):
    decision: Literal["PAY", "PEND", "DENY", "REFER_SIU"]
    examiner_note: str = Field(description="The decision, the rules that drive it, and the next actions")
    next_actions: list[str] = Field(default_factory=list)
    approvals_required: list[str] = Field(default_factory=list,
                                          description="e.g. senior examiner second approval, Claims Director")


class ClaimState(TypedDict, total=False):
    claim_id: str
    header: dict
    intake: dict
    policy: dict
    beneficiaries: list
    compensation: dict
    employment: dict
    screening: dict
    tools_called: list
    claim_file: dict
    observed: dict
    retrieved: list
    rules: list
    citation_validity: dict
    retrieval_coverage: dict
    guard: dict
    recommendation: dict
    calculation: dict
    rec_trace_id: str
    rec_root_id: str
    review: dict
    final_decision: str
    letter: str


@dataclass
class Runtime:
    """Per-trace dependencies, passed to the nodes through config['configurable']."""
    langfuse: Any
    handler: CallbackHandler
    trace_attrs: dict
    labels: dict = field(default_factory=dict)
    model: Optional[str] = None
    release: Optional[str] = None
    mode: str = "auto"                       # auto | workbench (interrupt for a human) | experiment
    session: Optional[ClientSession] = None
    examiner: Optional[dict] = None          # auto mode: a simulated examiner decision
    intake_override: Optional[dict] = None   # step-level experiments: a perfect intake
    usage: list = field(default_factory=list)
    obs: dict = field(default_factory=dict)  # role → observation id
    rules_prompt: str = "claims-sop-rules"   # the servicing graph reuses the rules step with its own prompt
    case_label: str = "CLAIM FILE"
    served: dict = field(default_factory=dict)  # prompt name → version


def _rt(cfg: RunnableConfig) -> Runtime:
    return cfg["configurable"]["runtime"]


def _nest(cfg):
    """Make the callback handler's observation for THIS node/tool the current span.

    LangChain runs the sync Langfuse callback handler in an executor thread during async
    runs, so the OTel context it attaches is not visible here. Look the run up instead,
    so our explicit spans nest under the LangGraph node or tool that produced them.
    """
    from contextlib import nullcontext

    try:
        cb = cfg.get("callbacks") if isinstance(cfg, dict) else cfg
        run_id = getattr(cb, "parent_run_id", None)
        handler = next(h for h in getattr(cb, "handlers", []) if isinstance(h, CallbackHandler))
        span = getattr(handler._runs.get(run_id), "_otel_span", None)
        return otel_trace.use_span(span, end_on_exit=False) if span is not None else nullcontext()
    except Exception:  # noqa: BLE001 — nesting is cosmetic; never break the claim
        return nullcontext()


def make_llm(model: str, max_tokens: int = 2000, name: Optional[str] = None):
    """`name` becomes the generation's name in Langfuse (one per LLM call site)."""
    if model.startswith(("gpt", "o1", "o3", "o4")):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, max_tokens=max_tokens, name=name)
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, max_tokens=max_tokens, name=name)


def _prompt(rt: Runtime, name: str):
    text, client, cfg = prompts.get(rt.langfuse, name, rt.labels.get(name))
    rt.served[name] = getattr(client, "version", "fallback")
    model = rt.model if (rt.model and name in ("claims-sop-rules", "claims-adjudication", "annuity-service-rules",
                                                "annuity-service-recommendation")) else cfg.get("model")
    return text, client, model or settings.AGENT_MODEL, int(cfg.get("max_tokens", 2000))


def _llm_cfg(cfg: RunnableConfig, client, run_name: str) -> dict:
    meta = {"langfuse_prompt": client} if client is not None else {}
    return {**cfg, "run_name": run_name, "metadata": {**(cfg.get("metadata") or {}), **meta}}


def _usage(rt: Runtime, model: str, msg) -> None:
    u = getattr(msg, "usage_metadata", None) or {}
    if u:
        rt.usage.append((model, u.get("input_tokens", 0), u.get("output_tokens", 0)))


def _score(rt: Runtime, role: str, name: str, value, data_type: str, comment: Optional[str] = None) -> None:
    obs_id = rt.obs.get(role)
    if obs_id is None:
        return
    rt.langfuse.create_score(trace_id=rt.trace_attrs["trace_id"], observation_id=obs_id, name=name,
                             value=value, data_type=data_type, comment=comment)


async def _mcp(rt: Runtime, cfg, name: str, args: dict) -> dict:
    """Call one MCP tool inside the caller's trace (traceparent + baggage in _meta)."""
    attrs = {k: v for k, v in rt.trace_attrs.items() if k != "trace_id" and v}
    with _nest(cfg), propagate_attributes(**attrs, as_baggage=True), \
            rt.langfuse.start_as_current_observation(as_type="tool", name=f"mcp-client: {name}", input=args,
                                                     metadata={"mcp.server": settings.MCP_URL}) as s:
        if rt.session is None:
            data = {"error": "POLICY_ADMIN_UNAVAILABLE"}
        else:
            res = await rt.session.call_tool(name, args, meta=settings.inject_trace_context())
            data = res.structuredContent or {}
            if "result" in data and len(data) == 1:
                data = data["result"]
            if not data and res.content:
                try:
                    data = json.loads(res.content[0].text)
                except Exception:  # noqa: BLE001
                    data = {"text": res.content[0].text}
        s.update(output=data, level="WARNING" if isinstance(data, dict) and "error" in data else "DEFAULT")
    return data


# ───────────────────────────── nodes ─────────────────────────────

async def load_claim(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    header = await _mcp(rt, cfg, "get_claim_file", {"claim_id": state["claim_id"]})
    if "error" in header:
        raise RuntimeError(f"claim {state['claim_id']}: {header}")
    return {"header": header}


async def intake(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    if rt.intake_override is not None:
        return {"intake": rt.intake_override}
    claim = claimsdb.get(state["claim_id"])  # the document store
    paths = claimsdb.document_paths(claim)
    attrs = {k: v for k, v in rt.trace_attrs.items() if k != "trace_id" and v}
    with _nest(cfg), propagate_attributes(**attrs, as_baggage=True), \
            rt.langfuse.start_as_current_observation(
                as_type="tool", name="a2a-client: doc-intake",
                input={"agent": settings.DOC_INTAKE_URL, "documents": [p.name for _, p in paths]},
                metadata={"protocol": "a2a", "a2a.method": "message/send", "a2a.agent": "doc-intake (Strands)"}) as s:
        res = await a2a_client.send_message(
            f"Extract the fields from the documents of death claim {state['claim_id']}.",
            [a2a_client.file_part(p) for _, p in paths], context_id=f"{state['claim_id']}-{uuid.uuid4().hex[:6]}",
            metadata={"claim_id": state["claim_id"], "prompt_label": rt.labels.get("doc-intake", "production")})
        data = a2a_client.parse_json(res["text"])
        s.update(output={"task_id": res["task_id"], "state": res["state"], "parsed": data is not None},
                 level="DEFAULT" if data is not None else "ERROR")
    if data is None:
        raise RuntimeError("doc-intake returned no JSON")
    return {"intake": data}


async def policy_coverage(state: ClaimState, config: RunnableConfig) -> dict:
    """A small tool-using agent: gathers policy facts through the policy-admin MCP tools.

    Trust but verify: required tools the agent did not call are fetched deterministically
    afterwards (so the facts are complete) and the miss is scored as tool-trajectory = 0.
    """
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    header = state["header"]
    pn = header["policy_number"]
    results: dict = {}

    def _tool(name: str, doc: str):
        async def _call(policy_number: str, callbacks: Callbacks = None) -> dict:
            data = await _mcp(rt, callbacks, name, {"policy_number": policy_number})
            results[name] = data
            return data
        _call.__name__ = name
        _call.__doc__ = doc
        return tool(_call)

    tools = [_tool("get_policy", "Policy record: line, governing state, issue date, status at death, amounts, insured."),
             _tool("get_beneficiaries", "Beneficiary designation on file."),
             _tool("get_agent_compensation", "Agent of record and whether first-year commission was paid."),
             _tool("get_employment_status", "Group life only: employment status and conversion election.")]
    by_name = {t.name: t for t in tools}
    system, client, model, max_tokens = _prompt(rt, "claims-policy-coverage")
    llm = make_llm(model, max_tokens, "policy-coverage-llm").bind_tools(tools)
    msgs = [SystemMessage(content=system), HumanMessage(content="Claim header:\n" + json.dumps(header, indent=1))]
    called: list[str] = []
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="agent", name="policy-coverage", input={"claim_header": header},
            metadata={"role": "policy", "model": model, "prompt_version": str(rt.served.get("claims-policy-coverage"))}) as obs:
        rt.obs["policy"] = obs.id
        summary = ""
        for _ in range(6):
            ai = await llm.ainvoke(msgs, _llm_cfg(cfg, client, "policy-coverage-llm"))
            _usage(rt, model, ai)
            msgs.append(ai)
            if not getattr(ai, "tool_calls", None):
                summary = ai.content if isinstance(ai.content, str) else " ".join(
                    b.get("text", "") for b in ai.content if isinstance(b, dict))
                break
            for tc in ai.tool_calls:
                t = by_name.get(tc["name"])
                called.append(tc["name"])
                out = await t.ainvoke(tc["args"], cfg) if t else {"error": "unknown tool"}
                msgs.append(ToolMessage(content=json.dumps(out), tool_call_id=tc["id"]))
        required = evals.required_tools(header["line"])
        missing = sorted(required - set(called))
        # Called with a wrong argument (e.g. the claim id instead of the policy number) counts as a miss too.
        failed = sorted(n for n in required & set(called)
                        if not isinstance(results.get(n), dict) or "error" in results[n])
        for name in missing + failed:  # verify: complete the facts with the header's policy number, record the miss
            results[name] = await _mcp(rt, cfg, name, {"policy_number": pn})
        obs.update(output={"summary": summary, "tools_called": called, "fetched_after_agent": missing,
                           "refetched_after_error": failed},
                   level="WARNING" if missing or failed else "DEFAULT")
    if any(not isinstance(results.get(n), dict) or "error" in results[n] for n in required):
        raise RuntimeError(f"policy admin has no usable record for {pn}: "
                           f"{ {n: results.get(n) for n in required} }"[:300])
    _score(rt, "policy", "tool-trajectory", 0.0 if missing or failed else 1.0, "NUMERIC",
           ((f"missing tool calls (fetched by the orchestrator): {', '.join(missing)}. " if missing else "")
            + (f"failed calls (refetched): {', '.join(failed)}" if failed else "")) or "all required tools called")
    emp = results.get("get_employment_status") or {"applicable": False}
    return {"policy": results["get_policy"], "beneficiaries": results["get_beneficiaries"]["beneficiaries"],
            "compensation": results["get_agent_compensation"], "employment": emp, "tools_called": called}


async def screening(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="guardrail", name="sanctions-screening", input={"claim_id": state["claim_id"]},
            metadata={"role": "screening", "engine": "OFAC SDN (synthetic)"}) as obs:
        rt.obs["screening"] = obs.id
        res = await _mcp(rt, cfg, "screen_parties", {"claim_id": state["claim_id"]})
        obs.update(output=res, level="WARNING" if res.get("sanctions") != "none" else "DEFAULT")
    return {"screening": res}


async def assemble(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="span", name="assemble-claim-file",
            input={"sources": ["claims-admin", "policy-admin", "agency-admin", "group-admin", "doc-intake",
                               "screening"]}, metadata={"role": "assemble"}) as s:
        claim_file, observed = claimsdb.assemble(state["header"], state["policy"], state["beneficiaries"],
                                                 state["compensation"], state["employment"], state["screening"],
                                                 state["intake"])
        s.update(output=claim_file)
    return {"claim_file": claim_file, "observed": observed}


async def sop_rules(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    cf, obs_claim = state["claim_file"], state["observed"]
    line, st = cf["product_line"], cf["governing_state"]
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="retriever", name="sop-retrieval",
            input={"product_line": line, "governing_state": st, "filter": "metadata: lines ∋ line AND states ∋ "
                   "(state OR ALL)", "release": rt.release or settings.RELEASE},
            metadata={"index": "sop-library (metadata filter)", "library_size": len(sops.library())}) as r:
        docs = sops.retrieve(line, st, rt.release)
        r.update(output={"sops": [{k: d[k] for k in ("sop_id", "title", "version", "effective_from")} for d in docs]},
                 metadata={"retrieved": len(docs), "release_bug_active": sops.buggy(rt.release)})
    retrieved_ids = [d["sop_id"] for d in docs]
    sop_text = "\n\n".join(f"===== {d['sop_id']} (version {d['version']}) =====\n{d['text']}" for d in docs)
    system, client, model, max_tokens = _prompt(rt, rt.rules_prompt)
    llm = make_llm(model, max_tokens, "sop-rules-llm").with_structured_output(RuleExtraction, method="json_schema", include_raw=True)
    user = f"{rt.case_label}:\n{json.dumps(cf, indent=1)}\n\nRETRIEVED SOPs:\n{sop_text}"
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="agent", name="sop-rules", input={"claim_file": cf, "sops": sop_text},
            metadata={"role": "sop_rules", "model": model, "retrieved_sops": ",".join(retrieved_ids),
                      "prompt_version": str(rt.served.get(rt.rules_prompt))}) as obs:
        rt.obs["sop_rules"] = obs.id
        out = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)],
                                _llm_cfg(cfg, client, "sop-rules-llm"))
        _usage(rt, model, out.get("raw"))
        parsed = out.get("parsed")
        rules = [_registry_fields(r.model_dump()) for r in parsed.rules] if parsed else []
        obs.update(output={"rules": rules}, level="DEFAULT" if parsed else "ERROR",
                   status_message=None if parsed else f"structured output failed: {out.get('parsing_error')}")
    cv, problems = evals.citation_validity(rules, obs_claim, retrieved_ids)
    rc, missing_sops = evals.retrieval_coverage(retrieved_ids, line, st)
    _score(rt, "sop_rules", "citation-validity", cv, "NUMERIC", "; ".join(problems) or "all citations valid")
    _score(rt, "sop_rules", "retrieval-coverage", rc, "NUMERIC",
           f"SOPs tagged for {line}/{st} but not retrieved: {', '.join(missing_sops)}" if missing_sops
           else "every tagged SOP retrieved")
    _score(rt, "sop_rules", "rules-found", float(len(rules)), "NUMERIC")
    return {"retrieved": retrieved_ids, "rules": rules,
            "citation_validity": {"score": cv, "problems": problems},
            "retrieval_coverage": {"score": rc, "missing": missing_sops}}


def _registry_fields(r: dict) -> dict:
    """The agent decides WHICH rules apply; a rule's type and severity come from the rule registry, so a
    mislabelled type cannot change the decision downstream. The agent's own label is kept when it differs."""
    v = oracle.rule_by_id(str(r.get("rule_id", "")).strip())
    if v is None:
        return r
    out = {**r, "type": v["type"], "severity": v["severity"]}
    if r.get("type") and r["type"] != v["type"]:
        out["agent_type"] = r["type"]
    return out


async def critical_guard(state: ClaimState, config: RunnableConfig) -> dict:
    """Deterministic backstop: the 8 critical rules are codified; any that applies but was not
    extracted is added before adjudication (and scored). The long tail is not guarded."""
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    covered, missing = evals.critical_rules_covered(state["rules"], state["observed"])
    rules = list(state["rules"])
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="guardrail", name="critical-rule-guard",
            input={"extracted": sorted(evals.cited_ids(rules)),
                   "codified_critical_applicable": sorted(r["rule_id"] for r in evals.codified_critical(state["observed"]))},
            metadata={"role": "guard", "enabled": str(GUARD_ENABLED)}) as g:
        rt.obs["guard"] = g.id
        if missing and GUARD_ENABLED:
            for rid in missing:
                v = oracle.rule_by_id(rid, oracle.as_of(state["observed"]))
                rules.append({"rule_id": rid, "version": v["version"], "sop_id": v["sop_id"], "section": v["section"],
                              "type": v["type"], "action": v["action"], "added_by": "critical-rule-guard",
                              "rationale": "Added by the critical-rule guard: codified conditions are met."})
        g.update(output={"missing": missing, "action": ("added to the rule set" if missing and GUARD_ENABLED
                                                        else "none" if not missing else "logged only (guard off)")},
                 level="WARNING" if missing else "DEFAULT")
    _score(rt, "guard", "critical-rules-covered", 1 if covered else 0, "BOOLEAN",
           f"agent missed critical rule(s): {', '.join(missing)}" if missing else "every codified critical rule found")
    return {"rules": rules, "guard": {"missing": missing, "triggered": bool(missing)}}


def _decision_for(rules: list[dict]) -> str:
    """Company decision precedence over rule effects: REFER_SIU > DENY > PEND > PAY."""
    level = max([{"refer_siu": 3, "deny": 2, "hold": 1}.get(r["effect"], 0) for r in rules] or [0])
    return {3: "REFER_SIU", 2: "DENY", 1: "PEND", 0: "PAY"}[level]


def _rule_lines(rules: list[dict]) -> str:
    return "\n".join(f"- {r['rule_id']} ({r.get('type')}): {r.get('action')} — {r.get('rationale')}" for r in rules)


async def adjudication(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    cf, obs_claim, rules = state["claim_file"], state["observed"], state["rules"]
    embedded = bool(cf["intake_findings"]["embedded_instructions"])
    system, client, model, max_tokens = _prompt(rt, "claims-adjudication")
    llm = make_llm(model, max_tokens, "adjudication-llm").with_structured_output(Recommendation, method="json_schema", include_raw=True)
    user = f"CLAIM FILE:\n{json.dumps(cf, indent=1)}\n\nAPPLICABLE RULES:\n{_rule_lines(rules) or '- none'}"
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="agent", name="adjudication", input={"claim_file": cf, "rules": rules},
            metadata={"role": "adjudication", "model": model, "embedded_instruction": "true" if embedded else "false",
                      "prompt_version": str(rt.served.get("claims-adjudication"))}) as obs:
        rt.obs["adjudication"] = obs.id
        out = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)],
                                _llm_cfg(cfg, client, "adjudication-llm"))
        _usage(rt, model, out.get("raw"))
        rec = out.get("parsed")
        rec = rec.model_dump() if rec else {"decision": "PEND", "examiner_note": "Adjudication failed; review manually.",
                                            "next_actions": [], "approvals_required": []}
        ok, open_ = evals.no_pay_with_open_block(rec["decision"], obs_claim)
        if not ok and GUARD_ENABLED and rt.mode != "experiment":
            # The guard ENFORCES, it does not only score: a codified critical hold/deny/SIU rule applies, so the
            # recommendation cannot be PAY. Experiments skip this — they measure the agent, not the backstop.
            forced = _decision_for(evals.codified_critical(obs_claim))
            rec = {**rec, "decision": forced, "guard_override": f"agent recommended PAY; {', '.join(open_)} applies",
                   "examiner_note": f"[Critical-rule guard: recommendation changed from PAY to {forced} because "
                                    f"{', '.join(open_)} applies.] " + rec["examiner_note"]}
        calculation = calc.calculate(obs_claim, rules, rec["decision"])
        obs.update(output={**rec, "calculation": calculation},
                   level="WARNING" if rec.get("guard_override") else "DEFAULT")
    _score(rt, "adjudication", "no-pay-with-open-block", 1 if ok else 0, "BOOLEAN",
           (f"agent recommended PAY while {', '.join(open_)} applies"
            + (" — the guard changed it" if rec.get("guard_override") else "")) if not ok else
           ("open critical rules respected: " + ", ".join(open_) if open_ else "no open critical rule"))
    _score(rt, "adjudication", "decision", rec["decision"], "CATEGORICAL")
    gross = oracle.gross_benefit(obs_claim)
    if gross >= 500000:
        flagged = any("approv" in a.lower() or "director" in a.lower() for a in rec["approvals_required"]) or \
            "ALL-AUTH-001" in evals.cited_ids(rules)
        _score(rt, "adjudication", "escalation-when-required", 1 if flagged else 0, "BOOLEAN",
               f"gross benefit {gross:,.0f} ≥ 500,000 requires a second approver")
    return {"recommendation": rec, "calculation": calculation}


async def examiner_review(state: ClaimState, config: RunnableConfig) -> dict:
    """Human in the loop. Workbench mode pauses here (LangGraph interrupt) until the examiner
    acts; the resume is the next trace in the claim's session."""
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    rec = state["recommendation"]
    if rt.mode == "workbench":
        review = interrupt({"claim_id": state["claim_id"], "recommendation": rec, "rules": state["rules"],
                            "calculation": state["calculation"], "guard": state.get("guard"),
                            "rec_trace_id": state.get("rec_trace_id"), "rec_root_id": state.get("rec_root_id")})
    else:
        review = rt.examiner or {"action": "approve", "examiner_id": "auto"}
    final = rec["decision"] if review.get("action") == "approve" else (review.get("decision") or rec["decision"])
    recalc = calc.calculate_service if oracle.is_service(state["observed"]) else calc.calculate
    calculation = state["calculation"] if final == rec["decision"] else \
        recalc(state["observed"], state["rules"], final)  # an override changes the amounts
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="span", name="examiner-review", input={"recommendation": rec["decision"]},
            output={**review, "final_decision": final, "calculation": calculation},
            metadata={"role": "examiner_review", "examiner_id": str(review.get("examiner_id", ""))}):
        pass
    return {"review": review, "final_decision": final, "calculation": calculation}


async def correspondence(state: ClaimState, config: RunnableConfig) -> dict:
    cfg = config  # LangGraph injects the run config by parameter NAME
    rt = _rt(cfg)
    decision = state["final_decision"]
    letter_rules = [r["rule_id"] for r in state["rules"]
                    if any(v["letter"] for v in oracle.rule_versions(r["rule_id"]))]
    needed = [rid for rid in letter_rules if decision in evals.NOTICE_DECISIONS.get(rid, {"PAY", "PEND", "DENY", "REFER_SIU"})]
    req = "\n".join(f"- {notices.NOTICES[r]}" for r in needed if r in notices.NOTICES) or "- none"
    calcn = state.get("calculation") or {}
    cf = state["claim_file"]
    why_types = {"PAY": set(), "PEND": {"BLOCK", "PEND", "DOC_REQUIRED"}, "DENY": {"DENY"}, "REFER_SIU": set()}[decision]
    review = state.get("review") or {}
    note = state["recommendation"]["examiner_note"]
    if review.get("note") and review.get("action") != "approve" or (review.get("note") or "").strip() not in (
            "", "Approved as recommended."):
        note = f"{note}\nExaminer: {review['note']}"
    facts = {"decision": decision, "claimant": cf["claimant"]["name"], "insured_policy": cf["policy_number"],
             "amount": calcn.get("gross_benefit"), "interest": calcn.get("interest_due"),
             "what_is_needed_or_why": [r.get("action") for r in state["rules"] if r.get("type") in why_types],
             "examiner_note": note}
    system, client, model, max_tokens = _prompt(rt, "claims-correspondence")
    llm = make_llm(model, max_tokens, "correspondence-llm")
    with _nest(cfg), rt.langfuse.start_as_current_observation(
            as_type="agent", name="correspondence", input={"letter_facts": facts, "required_notices": needed},
            metadata={"role": "correspondence", "model": model, "decision": decision,
                      "prompt_version": str(rt.served.get("claims-correspondence"))}) as obs:
        rt.obs["correspondence"] = obs.id
        ai = await llm.ainvoke([SystemMessage(content=system), HumanMessage(
            content=f"LETTER FACTS:\n{json.dumps(facts, indent=1)}\n\nREQUIRED NOTICES (include word for word):\n{req}")],
            _llm_cfg(cfg, client, "correspondence-llm"))
        _usage(rt, model, ai)
        letter = ai.content if isinstance(ai.content, str) else "".join(
            b.get("text", "") for b in ai.content if isinstance(b, dict))
        obs.update(output=letter)
    score, missing = evals.disclosures_present(letter, state["observed"], decision)
    _score(rt, "correspondence", "disclosures-present", score, "NUMERIC",
           f"missing required notices: {', '.join(missing)}" if missing else "all required notices present")
    pii = evals.pii_in_text(letter)
    _score(rt, "correspondence", "no-pii-in-letter", 0 if pii else 1, "BOOLEAN",
           f"letter contains: {', '.join(pii)}" if pii else "no PII patterns")
    _score(rt, "correspondence", "reading-level", evals.reading_grade(letter), "NUMERIC",
           "Flesch-Kincaid grade of the letter body; target ≤ 9")
    return {"letter": letter}


def _build():
    g = StateGraph(ClaimState)
    for name, fn in [("load-claim", load_claim), ("intake", intake), ("policy-coverage", policy_coverage),
                     ("screening", screening), ("assemble", assemble), ("sop-rules", sop_rules),
                     ("critical-guard", critical_guard), ("adjudication", adjudication),
                     ("examiner-review", examiner_review), ("correspondence", correspondence)]:
        g.add_node(name, fn)
    order = ["load-claim", "intake", "policy-coverage", "screening", "assemble", "sop-rules", "critical-guard",
             "adjudication", "examiner-review", "correspondence"]
    g.add_edge(START, order[0])
    for a, b in zip(order, order[1:]):
        g.add_edge(a, b)
    g.add_edge(order[-1], END)
    return g.compile(checkpointer=InMemorySaver())


GRAPH = _build()


# ───────────────────────────── entry points ─────────────────────────────

def _cost(usage: list) -> tuple[float, int, int]:
    tin = sum(u[1] for u in usage)
    tout = sum(u[2] for u in usage)
    usd = sum(u[1] * _PRICE.get(u[0], (3.0, 15.0))[0] + u[2] * _PRICE.get(u[0], (3.0, 15.0))[1] for u in usage) / 1e6
    return round(usd, 6), tin, tout


async def _traced_run(claim_id: str, event: str, graph_input, *, mode: str, labels: Optional[dict], model: Optional[str],
                      release: Optional[str], user_id: str, session_id: Optional[str], tags: Optional[list],
                      examiner: Optional[dict], intake_override: Optional[dict], thread_id: str,
                      extra_metadata: Optional[dict] = None) -> dict:
    lf = settings.get_langfuse()
    claim = claimsdb.get(claim_id)
    labels = labels or {}
    sop_label = labels.get("claims-sop-rules", "production")
    trace_tags = sorted(set(["claims", f"line:{claim['line']}", f"state:{claim['state']}", f"event:{event}",
                             f"mode:{mode}"] + (tags or [])))
    # Trace version = release + the hero prompt's version: what every quality and business metric splits on.
    _, sop_client, _ = prompts.get(lf, "claims-sop-rules", sop_label)
    version = f"{release or settings.RELEASE} · sop-rules v{getattr(sop_client, 'version', 'fallback')}"
    meta = {"claim_id": claim_id, "event": event, "line": claim["line"], "state": claim["state"],
            "sop_rules_label": sop_label, **{k: str(v) for k, v in (extra_metadata or {}).items()}}
    t0 = time.perf_counter()
    with propagate_attributes(session_id=session_id, user_id=user_id, tags=trace_tags, metadata=meta,
                              version=version, trace_name=TRACE_NAME):
        with lf.start_as_current_observation(as_type="agent", name=TRACE_NAME,
                                             input={"claim_id": claim_id, "event": event},
                                             metadata={"role": "orchestrator", "release": release or settings.RELEASE}) as root:
            environment = (getattr(otel_trace.get_current_span(), "attributes", None) or {}).get(
                LangfuseOtelSpanAttributes.ENVIRONMENT)
            rt = Runtime(langfuse=lf, handler=CallbackHandler(), labels=labels, model=model, release=release, mode=mode,
                         examiner=examiner, intake_override=intake_override,
                         trace_attrs={"trace_id": root.trace_id, "session_id": session_id, "user_id": user_id,
                                      "trace_name": TRACE_NAME, "version": version, "environment": environment})
            rt.obs["orchestrator"] = root.id
            run_cfg = {"callbacks": [rt.handler], "recursion_limit": 40, "run_name": "claims-graph",
                       "configurable": {"thread_id": thread_id, "runtime": rt}}
            if isinstance(graph_input, dict):
                graph_input = {**graph_input, "rec_trace_id": root.trace_id, "rec_root_id": root.id}
            error = None
            try:
                if event == "examiner_review":  # the resumed half needs no MCP session
                    state = await GRAPH.ainvoke(graph_input, run_cfg)
                else:
                    async with streamablehttp_client(settings.MCP_URL) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            rt.session = session
                            state = await GRAPH.ainvoke(graph_input, run_cfg)
            except Exception as exc:  # noqa: BLE001 — recorded on the trace, then re-raised
                while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                    exc = exc.exceptions[0]
                error = exc
                state = {}
                root.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:300])
            interrupted = bool(state.get("__interrupt__"))
            rec = state.get("recommendation") or {}
            out = {"recommendation": rec.get("decision"), "final_decision": state.get("final_decision"),
                   "rules": sorted(evals.cited_ids(state.get("rules") or [])),
                   "guard": state.get("guard"), "calculation": state.get("calculation"),
                   "awaiting_examiner": interrupted}
            root.update(output=out, metadata={
                "prompt_versions": json.dumps(rt.served), "apm_trace_url": settings.apm_url(root.trace_id),
                "awaiting_examiner": str(interrupted)})
            trace_id, root_id = root.trace_id, root.id
    usd, tin, tout = _cost(rt.usage)
    s = lambda **kw: lf.create_score(trace_id=trace_id, observation_id=root_id, **kw)  # noqa: E731
    if event != "examiner_review":
        s(name="claim-cost-usd", value=usd, data_type="NUMERIC",
          comment=f"{tin} input + {tout} output tokens at list price, orchestrator-side LLM calls; trace cost is authoritative")
        s(name="claim-latency-s", value=round(time.perf_counter() - t0, 2), data_type="NUMERIC")
        s(name="llm-calls", value=float(len(rt.usage)), data_type="NUMERIC")
        if state.get("guard") is not None:
            s(name="guard-triggered", value=1 if state["guard"]["triggered"] else 0, data_type="BOOLEAN")
    if error is not None:
        settings.flush()
        raise error
    return {**out, "claim_id": claim_id, "event": event, "trace_id": trace_id, "root_id": root_id,
            "trace_url": settings.trace_url(trace_id), "session_url": settings.session_url(session_id) if session_id else None,
            "apm_url": settings.apm_url(trace_id), "prompt_versions": rt.served, "state": state, "thread_id": thread_id,
            "cost_usd": usd}


async def run_claim(claim_id: str, *, mode: str = "auto", labels: Optional[dict] = None, model: Optional[str] = None,
                    release: Optional[str] = None, user_id: str = "claims-system", session_id: Optional[str] = "claim",
                    tags: Optional[list] = None, examiner: Optional[dict] = None, intake_override: Optional[dict] = None,
                    thread_id: Optional[str] = None, extra_metadata: Optional[dict] = None) -> dict:
    """`documents_received`: the recommendation. In workbench mode it stops at the examiner interrupt."""
    sid = claim_id if session_id == "claim" else session_id
    res = await _traced_run(claim_id, "documents_received", {"claim_id": claim_id}, mode=mode, labels=labels,
                            model=model, release=release, user_id=user_id, session_id=sid, tags=tags,
                            examiner=examiner, intake_override=intake_override,
                            thread_id=thread_id or f"{claim_id}:{uuid.uuid4().hex[:8]}", extra_metadata=extra_metadata)
    if mode == "auto" and examiner is not None:  # a simulated examiner reviewed inside the same trace
        record_review(res["trace_id"], res["root_id"], res["recommendation"], examiner)
    return res


async def resume_claim(claim_id: str, thread_id: str, review: dict, *, labels: Optional[dict] = None,
                       user_id: str = "examiner") -> dict:
    """`examiner_review`: resume the paused graph with the examiner's decision; score the recommendation."""
    snap = GRAPH.get_state({"configurable": {"thread_id": thread_id}})
    rec_trace, rec_root = snap.values.get("rec_trace_id"), snap.values.get("rec_root_id")
    rec = snap.values.get("recommendation") or {}
    res = await _traced_run(claim_id, "examiner_review", Command(resume=review), mode="workbench", labels=labels,
                            model=None, release=None, user_id=review.get("examiner_id") or user_id,
                            session_id=claim_id, tags=["examiner"], examiner=None, intake_override=None,
                            thread_id=thread_id)
    record_review(rec_trace, rec_root, rec.get("decision"), review)
    return res


def record_review(trace_id: Optional[str], root_id: Optional[str], recommended: Optional[str], review: dict) -> None:
    """The examiner's verdict, as scores on the RECOMMENDATION trace's root observation."""
    if not trace_id or not root_id:
        return
    lf = settings.get_langfuse()
    override = review.get("action") != "approve"
    lf.create_score(trace_id=trace_id, observation_id=root_id, name="examiner-override", value=1 if override else 0,
                    data_type="BOOLEAN", comment=(f"examiner changed {recommended} → {review.get('decision')}: "
                                                  f"{review.get('note', '')}" if override else "approved as recommended"))
    action = "override" if override else "approve"
    lf.create_score(trace_id=trace_id, observation_id=root_id, name="examiner-minutes-saved",
                    value=business.minutes_saved(action), data_type="NUMERIC", comment=business.NOTE)
    lf.create_score(trace_id=trace_id, observation_id=root_id, name="value-usd", value=business.value_usd(action),
                    data_type="NUMERIC", comment=business.NOTE)
    if override:
        lf.create_score(trace_id=trace_id, observation_id=root_id, name="override-reason",
                        value=review.get("reason", "other"), data_type="CATEGORICAL",
                        comment=("missed rules: " + ", ".join(review.get("missed_rule_ids") or [])) or None)
    settings.flush()


# ───────────────────────────── step-level experiments ─────────────────────────────

def _build_rules_step():
    """sop-rules → adjudication, no guard: experiments measure the AGENT, not the backstop."""
    g = StateGraph(ClaimState)
    g.add_node("sop-rules", sop_rules)
    g.add_node("adjudication", adjudication)
    g.add_edge(START, "sop-rules")
    g.add_edge("sop-rules", "adjudication")
    g.add_edge("adjudication", END)
    return g.compile()


RULES_STEP = _build_rules_step()


async def run_rules_step(claim_id: str, claim_file: dict, observed: dict, *, labels: Optional[dict] = None,
                         model: Optional[str] = None, release: Optional[str] = None) -> dict:
    """The rules step on a known claim file (perfect intake) — what the CI gate evaluates."""
    lf = settings.get_langfuse()
    labels = labels or {}
    with propagate_attributes(trace_name="claims-rules-step", tags=["experiment", "step:sop-rules"],
                              metadata={"claim_id": claim_id}):
        with lf.start_as_current_observation(as_type="agent", name="claims-rules-step",
                                             input={"claim_id": claim_id, "claim_file": claim_file},
                                             metadata={"role": "rules_step"}) as root:
            rt = Runtime(langfuse=lf, handler=CallbackHandler(), labels=labels, model=model, release=release,
                         mode="experiment", trace_attrs={"trace_id": root.trace_id})
            state = await RULES_STEP.ainvoke(
                {"claim_id": claim_id, "claim_file": claim_file, "observed": observed},
                {"callbacks": [rt.handler], "run_name": "claims-rules-step", "configurable": {"runtime": rt}})
            out = {"rules": state["rules"], "decision": state["recommendation"]["decision"],
                   "calculation": state["calculation"], "citation_validity": state["citation_validity"],
                   "retrieval_coverage": state["retrieval_coverage"], "prompt_versions": rt.served}
            root.update(output={k: out[k] for k in ("decision", "calculation")} |
                        {"rules": sorted(evals.cited_ids(out["rules"]))})
    return out


# ───────────────────────────── examiner assistant ─────────────────────────────

async def ask_assistant(claim_id: str, thread_id: Optional[str], question: str, *, examiner_id: str = "examiner",
                        labels: Optional[dict] = None) -> dict:
    """`examiner_question`: a tool-using assistant answers an examiner's question about one claim — a
    human-agent interaction recorded in the claim's session."""
    lf = settings.get_langfuse()
    claim = claimsdb.get(claim_id)
    snap = GRAPH.get_state({"configurable": {"thread_id": thread_id}}).values if thread_id else {}
    with propagate_attributes(session_id=claim_id, user_id=examiner_id, trace_name=TRACE_NAME,
                              tags=sorted(["claims", "event:examiner_question", f"line:{claim['line']}",
                                           f"state:{claim['state']}"]), metadata={"claim_id": claim_id}):
        with lf.start_as_current_observation(as_type="agent", name=TRACE_NAME,
                                             input={"claim_id": claim_id, "event": "examiner_question",
                                                    "question": question},
                                             metadata={"role": "orchestrator"}) as root:
            rt = Runtime(langfuse=lf, handler=CallbackHandler(), labels=labels or {},
                         trace_attrs={"trace_id": root.trace_id, "session_id": claim_id, "user_id": examiner_id,
                                      "trace_name": TRACE_NAME})

            @tool
            def get_claim_state() -> dict:
                """The claim's current recommendation, the rules found (with rationale), amounts and examiner review."""
                return {k: snap.get(k) for k in ("recommendation", "rules", "calculation", "review", "guard",
                                                  "claim_file")} if snap else {"note": "no recommendation yet"}

            @tool
            def lookup_sop(sop_id: str) -> str:
                """Full text of one SOP, e.g. SOP-LIFE-01."""
                d = sops.get(sop_id)
                return d["text"] if d else f"no SOP {sop_id}"

            tools = {t.name: t for t in (get_claim_state, lookup_sop)}
            system, client, model, max_tokens = _prompt(rt, "claims-examiner-assistant")
            llm = make_llm(model, max_tokens, "examiner-assistant-llm").bind_tools(list(tools.values()))
            msgs = [SystemMessage(content=system), HumanMessage(content=f"Claim {claim_id}. Examiner asks: {question}")]
            cfg = {"callbacks": [rt.handler], "run_name": "examiner-assistant",
                   "metadata": {"langfuse_prompt": client} if client else {}}
            answer = ""
            with lf.start_as_current_observation(as_type="agent", name="examiner-assistant",
                                                 input={"question": question},
                                                 metadata={"role": "examiner_assistant", "model": model}) as obs:
                for _ in range(5):
                    ai = await llm.ainvoke(msgs, cfg)
                    msgs.append(ai)
                    if not getattr(ai, "tool_calls", None):
                        answer = ai.content if isinstance(ai.content, str) else "".join(
                            b.get("text", "") for b in ai.content if isinstance(b, dict))
                        break
                    for tc in ai.tool_calls:
                        out = tools[tc["name"]].invoke(tc["args"], cfg) if tc["name"] in tools else "unknown tool"
                        msgs.append(ToolMessage(content=json.dumps(out, default=str)[:20000], tool_call_id=tc["id"]))
                obs.update(output=answer)
            root.update(output={"answer": answer})
            trace_id = root.trace_id
    return {"answer": answer, "trace_id": trace_id, "trace_url": settings.trace_url(trace_id)}
