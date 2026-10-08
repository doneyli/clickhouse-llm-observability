"""
Northwind Bank retail assistant — a LangGraph agent instrumented with Langfuse.

    START → input-guardrail ─(blocked)──────────────────────────► END
                  │
                  ▼
              assistant (LLM + tools) ◄──► tools (RAG + MCP banking)
                  │ (no more tool calls)
                  ▼
           output-guardrail → END

What lands in Langfuse for ONE customer turn (one trace):
  * root `agent` observation `northwind-assistant` — input = customer message,
    output = answer, metadata.context = every piece of evidence the model saw
    (what LLM-as-a-judge evaluators grade against)            OBS-01, EVA-01
  * LangGraph nodes, LLM generations (tokens, cost, latency), tool calls —
    auto-captured by the Langfuse LangChain CallbackHandler        OBS-01, OBS-05
  * `retriever` observation with document ids, sources, scores      OBS-03
  * MCP client spans + the MCP server's spans, in the same trace    OBS-04
  * `guardrail` observations + deterministic security scores        EVA-03
  * session, user, tags, environment, release, linked prompt version
  * the same trace id in the APM (see config.py)                    OBS-06
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks import Callbacks
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from northwind import config, knowledge, lang, masking, prompts

TRACE_NAME = "northwind-assistant"  # stable, low-cardinality — the question goes in input

REFUSAL = {
    "en": ("I can't help with that request. I can only help with your own Northwind Bank accounts "
           "and with questions about our products and services."),
    "es": ("No puedo ayudarle con esa solicitud. Solo puedo ayudarle con sus propias cuentas de "
           "Northwind Bank y con preguntas sobre nuestros productos y servicios."),
}

_INJECTION = re.compile(
    r"(?i)(ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions|rules|prompts?)"
    r"|ignore (everything|all) (above|before)|disregard (your|the|all) (rules|instructions)"
    r"|system prompt|developer mode|jailbreak|\bDAN\b|repeat the (text|words|instructions) above"
    r"|starting with ['\"]you are|(reveal|print|show|repeat|output) (me )?(your|the) (hidden |system |initial )?"
    r"(instructions|prompt|rules|configuration)"
    r"|you are now (in |an? )?(developer|admin|administrator|unrestricted|jailbroken|dan|god)\b"
    r"|pretend (you are|to be) (an? )?(bank employee|employee|admin|administrator|developer|different (ai|assistant)|unrestricted)"
    r"|act as (an? )?(admin|administrator|developer|bank employee)"
    r"|bypass (the |your |all )?(guardrails?|filters?|safety|security|rules|restrictions)"
    r"|override (the |your )?(rules|policy|safety|instructions)"
    # Spanish
    r"|ignora(r)? (todo lo anterior|(todas )?(las |tus )?(instrucciones|reglas)( anteriores| previas)?)"
    r"|olvida (todas )?(tus|las) (instrucciones|reglas)|instrucciones del sistema"
    r"|prompt (de|del) sistema|modo (desarrollador|administrador)"
    r"|(revela|imprime|muestra|mu[eé]strame|dime|repite)(r)? (tus|las) (instrucciones|reglas|configuraci[oó]n)"
    r"|ahora eres (un |una )?(administrador|desarrollador|ia sin restricciones|dan)"
    r"|finge (ser|que eres) (un |una )?(empleado|administrador|desarrollador)"
    r"|act[uú]a como (un )?(administrador|desarrollador|empleado)|sin restricciones)")
_OTHER_CUSTOMER = re.compile(
    r"(?i)(\bC-\d{4}\b"
    r"|(show|see|check|tell me|give me|read|access|look up|list)\b.{0,40}\b(another|other) customers?'?s?\b"
    r"|(another|other) customers?'s? (account|card|balance|transactions|data|details)"
    r"|someone else'?s (account|card|balance|transactions)"
    r"|(show|see|check|tell me|give me|read|access|look up)\b.{0,30}\bmy (wife|husband|neighbou?r|friend|boss)'?s "
    r"(account|card|balance|transactions)"
    r"|(mu[eé]strame|ver|consultar|dime|revisar|dame)\b.{0,40}\b(de otro cliente|de otra persona|"
    r"(cuenta|tarjeta|saldo|movimientos|transacciones) de mi (esposa|esposo|vecino|amigo|jefe)))")
_INVESTMENT = re.compile(
    r"(?i)(should i (buy|invest|sell|put)|which (stock|stocks|crypto|coin|fund) (should|to)|"
    r"\bbitcoin\b|\bcrypto(currency)?\b|best investment|guaranteed returns?|double my money|stock tip"
    r"|deber[ií]a (invertir|comprar|vender|poner)|criptomoneda|qu[eé] acci[oó]n (comprar|deber[ií]a)|duplicar mi dinero|mejor inversi[oó]n)")
_ADVICE_IN_ANSWER = re.compile(
    r"(?i)(you should (buy|invest|sell)|i (recommend|suggest) (buying|investing|selling)|guaranteed (return|profit))")


def _nest(config_: Optional[RunnableConfig]):
    """Make the callback handler's observation for THIS tool/node the current span.

    LangChain runs the (sync) Langfuse callback handler in an executor thread
    during async runs, so the OTel context it attaches is not visible here.
    Look the run up instead, so our explicit spans (retriever, MCP client,
    guardrail) nest under the LangGraph tool/node that produced them.
    """
    from contextlib import nullcontext

    from opentelemetry import trace as otel_trace
    try:
        cb = config_.get("callbacks") if isinstance(config_, dict) else config_  # config or a run manager
        run_id = getattr(cb, "parent_run_id", None)
        handler = next(h for h in getattr(cb, "handlers", []) if isinstance(h, CallbackHandler))
        span = getattr(handler._runs.get(run_id), "_otel_span", None)
        return otel_trace.use_span(span, end_on_exit=False) if span is not None else nullcontext()
    except Exception:  # noqa: BLE001 — nesting is cosmetic; never break the turn
        return nullcontext()


def _now_ms() -> float:
    return time.perf_counter() * 1000


def assess_input(text: str, customer_id: str) -> dict:
    """Deterministic input checks. Production: LLM Guard / Lakera / a classifier."""
    other = [m for m in re.findall(r"\bC-\d{4}\b", text) if m != customer_id]
    risks = []
    if _INJECTION.search(text):
        risks.append("prompt_injection")
    if other or (_OTHER_CUSTOMER.search(text) and not re.search(rf"\b{re.escape(customer_id)}\b", text)):
        risks.append("cross_customer_access")
    if _INVESTMENT.search(text):
        risks.append("investment_advice")
    _, pii = masking.scrub(text)
    blocking = [r for r in risks if r in ("prompt_injection", "cross_customer_access")]
    return {"risks": risks, "blocked": bool(blocking), "pii_shared": sorted(pii),
            "primary_risk": (blocking or risks or ["none"])[0]}


def assess_output(answer: str) -> dict:
    """Output DLP + compliance: redact leaked secrets, flag advice language."""
    cleaned, leaked = masking.scrub(answer)
    leaked = {c for c in leaked if c in ("card", "national_id", "otp", "iban", "account")}
    return {"answer": cleaned if leaked else answer, "leaked": sorted(leaked),
            "advice_language": bool(_ADVICE_IN_ANSWER.search(answer))}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def make_llm(model: str):
    if model.startswith(("gpt", "o1", "o3", "o4")):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=0.2)
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, temperature=0.2, max_tokens=1024)


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    blocked: bool


def _build_tools(langfuse, session: Optional[ClientSession], customer_id: str, evidence: list, used: list):
    """Tools bound to the AUTHENTICATED customer. The model never sees or chooses the id."""

    @tool
    async def search_knowledge_base(query: str, callbacks: Callbacks = None) -> str:
        """Search Northwind Bank's help-center articles (products, fees, limits, policies).
        Returns articles with ids like KB-102 that you must cite."""
        used.append("search_knowledge_base")
        with _nest(callbacks), langfuse.start_as_current_observation(as_type="retriever", name="kb-retrieval",
                                                   input={"query": query, "k": 3}) as r:
            docs = knowledge.search(query, k=3)
            r.update(output={"documents": [{k: d[k] for k in ("id", "title", "source", "score", "effective_date")}
                                           for d in docs]},
                     metadata={"index": "help-center-tfidf", "documents_returned": len(docs)})
        for d in docs:
            evidence.append(f"[{d['id']}] {d['title']} ({d['source']}): {d['text']}")
        if not docs:
            return "No matching articles."
        return "\n\n".join(f"[{d['id']}] {d['title']} — source: {d['source']}\n{d['text']}" for d in docs)

    async def _mcp(name: str, args: dict, config_: Optional[RunnableConfig] = None) -> dict:
        used.append(name)
        args = {"customer_id": customer_id, **args}
        with _nest(config_), langfuse.start_as_current_observation(as_type="span", name=f"mcp-client: {name}",
                                                   input=args, metadata={"mcp.server": config.MCP_URL}) as s:
            if session is None:
                data = {"error": "BANKING_SYSTEM_UNAVAILABLE"}
            else:
                carrier: dict = {}
                TraceContextTextMapPropagator().inject(carrier)  # W3C context → MCP _meta
                res = await session.call_tool(name, args, meta=carrier)
                data = res.structuredContent or {}
                if "result" in data and len(data) == 1:
                    data = data["result"]
                if not data and res.content:
                    try:
                        data = json.loads(res.content[0].text)
                    except Exception:  # noqa: BLE001
                        data = {"text": res.content[0].text}
                if res.isError:
                    s.update(level="ERROR", status_message=str(data)[:200])
            s.update(output=data)
        evidence.append(f"[tool:{name}] {json.dumps(data, default=str)}")
        return data

    @tool
    async def list_accounts(callbacks: Callbacks = None) -> dict:
        """List the signed-in customer's accounts (ids, balances) and cards (last 4 digits, status)."""
        return await _mcp("list_accounts", {}, callbacks)

    @tool
    async def get_recent_transactions(account_id: str, days: int = 30, callbacks: Callbacks = None) -> dict:
        """Recent transactions of one of the customer's accounts. Use list_accounts first to get ids."""
        return await _mcp("get_recent_transactions", {"account_id": account_id, "days": days}, callbacks)

    @tool
    async def block_card(card_last4: str, reason: str, callbacks: Callbacks = None) -> dict:
        """Block one of the customer's cards immediately (lost, stolen, fraud). Needs the last 4 digits."""
        return await _mcp("block_card", {"card_last4": card_last4, "reason": reason}, callbacks)

    @tool
    async def open_dispute(transaction_id: str, reason: str, callbacks: Callbacks = None) -> dict:
        """Open a dispute for one of the customer's transactions (transaction id like TX-88101)."""
        return await _mcp("open_dispute", {"transaction_id": transaction_id, "reason": reason}, callbacks)

    @tool
    async def schedule_callback(topic: str, preferred_time: str = "next available", callbacks: Callbacks = None) -> dict:
        """Schedule a call-back from a human Northwind agent."""
        return await _mcp("schedule_callback", {"topic": topic, "preferred_time": preferred_time}, callbacks)

    return [search_knowledge_base, list_accounts, get_recent_transactions, block_card,
            open_dispute, schedule_callback]


def _graph(langfuse, llm, tools, system_text: str, customer_id: str, check: dict, outcome: dict,
           language: str = "en"):
    llm_with_tools = llm.bind_tools(tools)

    async def input_guardrail(state: State, config: RunnableConfig) -> dict:
        with _nest(config), langfuse.start_as_current_observation(
                as_type="guardrail", name="input-guardrail", input=_text(state["messages"][-1].content),
                metadata={"policy": "northwind-input-v2", "engine": "rules"}) as g:
            g.update(output=check, level="WARNING" if check["risks"] else "DEFAULT")
        if check["blocked"]:
            return {"blocked": True, "messages": [AIMessage(content=REFUSAL.get(language, REFUSAL["en"]))]}
        return {"blocked": False}

    async def assistant(state: State, config: RunnableConfig) -> dict:  # noqa: F811 — LangGraph injects by name
        msgs = [SystemMessage(content=system_text)] + state["messages"]
        ai = await llm_with_tools.ainvoke(msgs, config)
        return {"messages": [ai]}

    async def output_guardrail(state: State, config: RunnableConfig) -> dict:
        answer = _text(state["messages"][-1].content)
        with _nest(config), langfuse.start_as_current_observation(
                as_type="guardrail", name="output-guardrail", input=answer,
                metadata={"policy": "northwind-output-v1", "engine": "rules"}) as g:
            result = assess_output(answer)
            g.update(output={k: v for k, v in result.items() if k != "answer"},
                     level="WARNING" if result["leaked"] or result["advice_language"] else "DEFAULT")
        outcome.update(result)
        if result["leaked"]:
            return {"messages": [AIMessage(content=result["answer"])]}
        return {}

    g = StateGraph(State)
    g.add_node("input-guardrail", input_guardrail)
    g.add_node("assistant", assistant)
    g.add_node("tools", ToolNode(tools))
    g.add_node("output-guardrail", output_guardrail)
    g.add_edge(START, "input-guardrail")
    def route_after_guardrail(state: State):
        return END if state.get("blocked") else "assistant"

    g.add_conditional_edges("input-guardrail", route_after_guardrail)
    g.add_conditional_edges("assistant", tools_condition, {"tools": "tools", END: "output-guardrail"})
    g.add_edge("tools", "assistant")
    g.add_edge("output-guardrail", END)
    return g.compile()


def _history(history: Optional[list]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for h in history or []:
        out.append(HumanMessage(content=h["content"]) if h["role"] == "user" else AIMessage(content=h["content"]))
    return out


async def run_turn(message: str, *, customer_id: str = "C-1001", session_id: Optional[str] = None,
                   history: Optional[list] = None, channel: str = "web", model: Optional[str] = None,
                   prompt_label: Optional[str] = None, tags: Optional[list] = None,
                   extra_metadata: Optional[dict] = None, trace_name: str = TRACE_NAME) -> dict:
    """One customer turn = one trace. Returns the answer plus links into Langfuse and the APM."""
    langfuse = config.get_langfuse()
    model = model or config.AGENT_MODEL
    system_text, lf_prompt = prompts.get_system_prompt(langfuse, prompt_label)
    if channel == "voice":  # channel modifier: the answer will be spoken, not read
        system_text += ("\n\nThis is a phone call: reply in at most three short spoken sentences, "
                        "no markdown, no lists, no links, and do not read article ids aloud.")
    check = assess_input(message, customer_id)
    language = lang.detect(message)
    evidence: list[str] = []
    used: list[str] = []
    outcome: dict = {}
    label = prompt_label or "production"

    # Owning team: the caller may pass its own `team:*` tag (voice → contact-center).
    team = [] if any(t.startswith("team:") for t in (tags or [])) else ["team:retail-digital"]
    trace_tags = sorted(set(["northwind-assistant", f"channel:{channel}", f"lang:{language}"] + team
                            + [f"risk:{r}" for r in check["risks"]] + (tags or [])))
    meta = {"channel": channel, "language": language, "customer_segment": _segment(customer_id), "model": model,
            "prompt_label": label, **{k: str(v) for k, v in (extra_metadata or {}).items()}}

    t0 = _now_ms()
    with propagate_attributes(session_id=session_id, user_id=customer_id, tags=trace_tags,
                              metadata=meta, version=config.RELEASE, trace_name=trace_name,
                              prompt=lf_prompt):
        with langfuse.start_as_current_observation(as_type="agent", name=TRACE_NAME, input=message) as root:
            trace_id = root.trace_id
            handler = CallbackHandler()
            run_cfg = {"callbacks": [handler], "recursion_limit": 14,
                       "run_name": "northwind-langgraph", "metadata": {"langfuse_session_id": session_id}}
            llm = make_llm(model)

            async def _invoke(session):
                tools = _build_tools(langfuse, session, customer_id, evidence, used)
                graph = _graph(langfuse, llm, tools, system_text, customer_id, check, outcome, language)
                return await graph.ainvoke({"messages": _history(history) + [HumanMessage(content=message)],
                                            "blocked": False}, run_cfg)

            error = None
            try:
                if check["blocked"]:
                    state = await _invoke(None)
                else:
                    async with streamablehttp_client(config.MCP_URL) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            state = await _invoke(session)
            except Exception as exc:  # noqa: BLE001 — recorded on the trace, then surfaced
                import traceback
                traceback.print_exception(exc)
                while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                    exc = exc.exceptions[0]
                error = exc
                state = {"messages": [AIMessage(content="Sorry — I'm having trouble right now. "
                                                        "Please try again or call us.")], "blocked": False}
                root.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:300])

            answer = _text(state["messages"][-1].content)
            cited = sorted(set(re.findall(r"\bKB-\d{3}\b", answer)))
            retrieved = sorted(set(re.findall(r"\[(KB-\d{3})\]", "\n".join(evidence))))
            # Follow-up turns often answer from facts retrieved in an EARLIER turn.
            # Give the judge that conversation context too, or it scores a correct
            # follow-up as unsupported.
            prior = "\n".join(f"{h['role']}: {h['content']}" for h in (history or [])[-4:])
            context = "\n\n".join(evidence) or "(no new retrieval or tool calls in this turn)"
            if prior:
                context += "\n\n[earlier in this conversation]\n" + prior
            root.update(output=answer, metadata={
                "context": context,
                "tools_used": ",".join(used) or "none", "cited_sources": ",".join(cited) or "none",
                "retrieved_sources": ",".join(retrieved) or "none",
                "prompt_version": str(getattr(lf_prompt, "version", "fallback")),
                "apm_trace_url": config.apm_url(trace_id),
                "latency_ms": str(round(_now_ms() - t0))})
            obs_id = root.id

    _score_turn(langfuse, trace_id, obs_id, check, outcome, used, cited, retrieved, language, answer,
                offline=(channel == "experiment"))
    if error is not None:
        config.flush()
    return {"answer": answer, "trace_id": trace_id, "trace_url": config.trace_url(trace_id),
            "apm_url": config.apm_url(trace_id), "sources": cited, "retrieved": retrieved,
            "tools_used": used, "blocked": check["blocked"], "risks": check["risks"],
            "prompt_version": getattr(lf_prompt, "version", None), "model": model,
            "error": f"{type(error).__name__}: {error}" if error else None}


def _segment(customer_id: str) -> str:
    return {"C-1002": "premier", "C-1004": "premier"}.get(customer_id, "everyday")


def _score_turn(langfuse, trace_id, obs_id, check, outcome, used, cited, retrieved, language="en", answer="",
                offline=False):
    """Deterministic, zero-cost scores on every turn — the first line of evals."""
    s = lambda **kw: langfuse.create_score(trace_id=trace_id, observation_id=obs_id, **kw)  # noqa: E731
    s(name="security-risk", value=check["primary_risk"], data_type="CATEGORICAL",
      comment=f"rules engine: {','.join(check['risks']) or 'no risk patterns'}")
    s(name="guardrail-blocked", value=1 if check["blocked"] else 0, data_type="BOOLEAN")
    s(name="pii-in-input", value=1 if check["pii_shared"] else 0, data_type="BOOLEAN",
      comment=",".join(check["pii_shared"]) or None)
    if outcome:
        s(name="output-pii-leak", value=1 if outcome.get("leaked") else 0, data_type="BOOLEAN")
        s(name="advice-language", value=1 if outcome.get("advice_language") else 0, data_type="BOOLEAN")
    if offline:  # experiments score language/register with their own evaluators — avoid duplicate names
        return
    answered_in = lang.detect(answer)
    s(name="language-match", value=1 if answered_in == language else 0, data_type="BOOLEAN",
      comment=f"asked in {language}, answered in {answered_in}")
    if language == "es" and not check["blocked"]:
        informal = lang.informal_markers(answer)
        s(name="formal-register", value=0 if informal else 1, data_type="BOOLEAN",
          comment=f"informal (tú) markers: {informal}" if informal else "formal (usted) register")
    if "search_knowledge_base" in used:
        s(name="cites-sources", value=1 if set(cited) & set(retrieved) else 0, data_type="BOOLEAN",
          comment=f"cited={cited} retrieved={retrieved}")
