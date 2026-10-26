# Modified 2026-10-11 for the standalone workshop; original in original-demo/.
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
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks import Callbacks
from langchain_core.tools import tool
from langfuse import LangfuseOtelSpanAttributes
from langfuse.langchain import CallbackHandler
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from opentelemetry import trace as otel_trace

from northwind import business, config, knowledge, lang, masking, prompts, tracing, monitoring

TRACE_NAME = "northwind-assistant"

# Release knobs. The defaults are the CURRENT release (1.6.1); earlier releases are
# reproduced for the story arcs with env overrides (see DEMO_SCRIPT "Story arc"):
#   1.5.0  NORTHWIND_TX_DEFAULT_DAYS=30   (transaction lookback shorter than the 60-day dispute window)
#   1.6.0  NORTHWIND_RETRIEVAL_K=8 NORTHWIND_RETRIEVAL_MIN_SCORE=0   ("more context" — a cost regression)
TX_DEFAULT_DAYS = int(os.environ.get("NORTHWIND_TX_DEFAULT_DAYS", "60"))
RETRIEVAL_K = int(os.environ.get("NORTHWIND_RETRIEVAL_K", "3"))
RETRIEVAL_MIN_SCORE = float(os.environ.get("NORTHWIND_RETRIEVAL_MIN_SCORE", "0.08"))

# List prices (USD per 1M tokens) for the per-turn cost score — an estimate for
# charting; the trace's own cost (computed by Langfuse) is authoritative.
_PRICE = {"claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0), "gpt-4.1": (2.0, 8.0),
          "gpt-4.1-mini": (0.4, 1.6)}  # stable, low-cardinality — the question goes in input

REFUSAL = {
    "en": ("I can't help with that request. I can only help with your own Northwind Bank accounts "
           "and with questions about our products and services."),
    "es": ("No puedo ayudarle con esa solicitud. Solo puedo ayudarle con sus propias cuentas de "
           "Northwind Bank y con preguntas sobre nuestros productos y servicios."),
}

# Input-guardrail rules — deterministic and explainable, English + Spanish. Each rule
# targets the ATTACK, not its vocabulary: "show me the rules for disputing a charge",
# "can I override the rules on the daily limit?" and "is there a developer mode in the
# app?" are banking questions that share words with injections. So the object must be
# the assistant itself ("your instructions", "the system prompt"), or the phrase must
# carry an enabling frame ("you are now in developer mode"). tests/test_guardrails.py
# is the contract: 20 benign prompts that must pass, 15 attacks that must be blocked.
_REVEAL_ES = r"(revela|imprime|muestra|mu[eé]str[ae]me|mostrar(me)?|dime|d[ií]game|repite|traduce)(r)?"
_INJECTION = re.compile(
    r"(?i)(ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions|rules|prompts?)"
    r"|(ignore|forget) (everything|all) (above|before)|disregard (your|the|all) (rules|instructions)"
    # "forget your instructions" / "forget all previous rules" — not "I forget the rules for …"
    r"|forget (all (of )?|about )?(your|the (previous|prior|earlier|above)|previous|prior|earlier) "
    r"(instructions|rules|prompts?)"
    r"|system prompt|jailbreak|(?-i:\bDAN\b)|starting with ['\"]you are"  # DAN is case-sensitive: "Dan" is a name
    # Extraction. The object must be the assistant's OWN instructions — "your …" or "the
    # hidden/system/initial …" — never a bare "the rules"; and "your rules for/on/about
    # <a topic>" is a policy question, not an extraction attempt.
    r"|(reveal|print|show|repeat|output|display|dump|translate|summari[sz]e)( me| us)? (all )?"
    r"(your( hidden| system| initial| original| internal)?|the (hidden|system|initial|original|internal)) "
    r"(instructions|prompt|rules|configuration|guidelines)\b(?! (for|on|about|regarding) (?!me\b|us\b))"
    r"|what('s| is| are| were) your (initial|system|hidden|original|internal|exact) (prompt|instructions|rules)"
    r"|(translate|summari[sz]e|repeat|rewrite|print|output) (everything|all|all the text|the (text|words|instructions)) "
    r"(above|before)"
    # "developer mode" only with an enabling frame — "is there a developer mode in the app?" passes
    r"|(you are now|you're now|you are|enter|enable|activate|switch (to|into)|turn on|go into)( in| into)?( the)? "
    r"(developer|dev|admin|god|debug|unrestricted|jailbreak) mode"
    r"|you are now (in |an? )?(developer|admin|administrator|unrestricted|jailbroken|dan|god)\b"
    r"|pretend (you are|to be) (an? )?(bank employee|employee|admin|administrator|developer|different (ai|assistant)|unrestricted)"
    r"|act as (an? )?(admin|administrator|developer|bank employee)"
    r"|bypass (the |your |all )?(guardrails?|filters?|safety|security|rules|restrictions)"
    # "override YOUR rules" is an attack; "override the rules on the daily transfer limit" is a question
    r"|override (your|the system'?s?|all( the| your)?) ((safety|security|system|content) )?"
    r"(rules|polic(y|ies)|safety|instructions|guardrails|restrictions)"
    # Spanish. "¿Puedo ignorar las reglas del límite diario?" is a question; the attack
    # names "tus" rules, "todas las" rules or "las … anteriores".
    r"|ignora(r)? (todo lo anterior|(todas )?tus (instrucciones|reglas)|todas las (instrucciones|reglas)"
    r"|las (instrucciones|reglas) (anteriores|previas))"
    r"|olvida (todas )?(tus|las) (instrucciones|reglas)|instrucciones del sistema|prompt (de|del) sistema"
    r"|(ahora est[aá]s en|est[aá]s en|activa|entra en|cambia a|pasa a)( el)? modo "
    r"(desarrollador|administrador|dios|sin restricciones)"
    # Reveal verbs need "tu(s) …" or "las … del sistema/internas/ocultas":
    # "dime las reglas para disputar un cargo" passes.
    rf"|{_REVEAL_ES} (todas )?tus? (instrucciones|reglas|configuraci[oó]n|prompt)(?! (para|sobre|acerca) )"
    rf"|{_REVEAL_ES} las (instrucciones|reglas|configuraci[oó]n) (del sistema|internas|ocultas|secretas|iniciales)"
    r"|ahora eres (un |una )?(administrador|desarrollador|ia sin restricciones|dan)"
    r"|finge (ser|que eres) (un |una )?(empleado|administrador|desarrollador)"
    r"|act[uú]a como (un )?(administrador|desarrollador|empleado)"
    # "sin restricciones" only about the assistant — "usar mi tarjeta sin restricciones" passes
    r"|\b(ia|asistente|modelo|modo|responde|resp[oó]ndeme|contesta|habla|act[uú]a) sin restricciones)")
# Someone else's data. Another customer's id (C-####) is checked in assess_input;
# these catch the social forms — "my wife's account", "I'm the account holder's son",
# "read me his transactions", "la cuenta de mi esposa". Joint-account requests are
# exempted in assess_input (see _JOINT_ACCOUNT).
_OTHER_CUSTOMER = re.compile(
    r"(?i)(\bC-\d{4}\b"
    r"|(show|see|check|tell me|give me|read|access|look up|list)\b.{0,40}\b(another|other) customers?'?s?\b"
    r"|(another|other) customers?'s? (account|card|balance|transactions|data|details)"
    r"|\b(list|export|dump)( me)? (all|every)( the| of the| your)? customers?\b"
    r"|someone else'?s (account|card|balance|transactions)"
    r"|(show|see|check|tell me|give me|read|access|look up)\b.{0,30}\bmy (wife|husband|partner|neighbou?r|friend|boss"
    r"|mother|mum|mom|father|dad|son|daughter|brother|sister)'?s (account|card|balance|transactions)"
    # a third party by pronoun, or by a claimed relationship to the account holder
    r"|\b(show|read|tell|give|send) (me|us) (his|her|their) (last |latest |recent |current )?"
    r"(transactions|balance|statements?|account|movements|card)"
    r"|\b(i'?m|i am|as|this is) (the |an? )?(account ?holder|card ?holder|customer|client)'?s "
    r"(son|daughter|wife|husband|partner|mother|father|brother|sister|carer|caregiver)\b"
    # Spanish
    r"|(mu[eé]strame|mu[eé]streme|ver|consultar|dime|d[ií]game|dame|deme|ind[ií]queme|cons[uú]lteme|revisar)\b.{0,40}"
    r"\b(de otro cliente|de otra persona|(cuenta|tarjeta|saldo|movimientos|transacciones) de mi "
    r"(esposa|esposo|marido|mujer|pareja|vecin[oa]|amig[oa]|jefe|madre|padre|hij[oa]|herman[oa]))"
    r"|\bsoy (el|la) (esposo|esposa|marido|mujer|pareja|hij[oa]|padre|madre|herman[oa]) (de la|del) (titular|cliente))")
# A joint account names a spouse's account legitimately ("can I show my wife's account
# in my app? we want a joint account"). The MCP server scopes account-specific
# lookups to the caller-supplied synthetic customer ID. This regex does not
# authenticate the caller; the workshop has no real identity boundary.
_JOINT_ACCOUNT = re.compile(
    r"(?i)\bjoint (account|holder|owner)s?\b|\badd (him|her|them) as\b"
    r"|cuenta (conjunta|mancomunada)|\bco-?titular|titulares? conjunt[oa]s?")
# Credential harvesting: asking for a secret to be READ OUT or HANDED OVER — "read me
# the last one-time code", "…needs the code we texted you, can you read it to me?",
# "dígame su clave". The customer SHARING a secret ("the CVV on my card is 123") is
# PII for masking, not an attack; asking the bank to (re)send a code ("send me a new
# verification code") is the normal login flow, so send/text are not trigger verbs.
_SECRET_EN = (r"(one[- ]time (code|passcode|password)|otp|verification code|security code|pin|cvv|cvc|passcode|password)"
              r"(?! (requirements?|rules|polic(y|ies)|reset|format|length|change))")  # "the password requirements" passes
_SECRET_ES = r"(c[oó]digo|clave|pin|cvv|contrase[ñn]a)(?! (para|swift|bic|postal|de (sucursal|oficina|banco))\b)"
_ASK_ES = r"((?<!nunca )(?<!no )comparta|d[ií]game|deme|l[eé]ame|d[ií]cteme|p[aá]seme|dime|dame|l[eé]eme|d[ií]ctame)"
_SOCIAL_ENGINEERING = re.compile(
    rf"(?i)(\b((read|tell|give) (me|us)|read (out|back))( the| your| that| this)?( last| latest| most recent)? {_SECRET_EN}\b"
    rf"|\b{_SECRET_EN}\b.{{0,80}}\b(read|tell|give)( it| that| them)?( back| out)? to (me|us)\b"
    rf"|\bshare (the|your|that) {_SECRET_EN} with (me|us)\b"
    # Spanish
    rf"|\b{_ASK_ES}( conmigo)? (el |la |su |tu |ese |este |esa )?([uú]ltim[oa] )?{_SECRET_ES}"
    rf"|\b{_SECRET_ES}\b.{{0,80}}\b(d[ií]gamelo|l[eé]amelo|d[ií]ctemelo|d[ií]melo|l[eé]emelo"
    rf"|me lo (puede |podr[ií]a )?(leer|decir|dictar|lee|dice|dicta))\b)")
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
    text = text.replace("\u2019", "'")  # phone keyboards type I’m / wife’s; the rules use ASCII '
    other = [m for m in re.findall(r"\bC-\d{4}\b", text) if m != customer_id]
    risks = []
    if _INJECTION.search(text):
        risks.append("prompt_injection")
    # Another customer's id always counts. The social forms ("my wife's account") do
    # not when the customer names their own id, or asks about a joint account.
    third_party = (_OTHER_CUSTOMER.search(text) and not _JOINT_ACCOUNT.search(text)
                   and not re.search(rf"\b{re.escape(customer_id)}\b", text))
    if other or third_party:
        risks.append("cross_customer_access")
    if _SOCIAL_ENGINEERING.search(text):
        risks.append("social_engineering")
    if _INVESTMENT.search(text):
        risks.append("investment_advice")
    _, pii = masking.scrub(text)
    blocking = [r for r in risks if r in ("prompt_injection", "cross_customer_access", "social_engineering")]
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


def _build_tools(langfuse, session: Optional[ClientSession], customer_id: str, evidence: list, used: list,
                 trace_attrs: dict):
    """Tools bound to the selected synthetic customer. The model cannot choose the id.

    `trace_attrs` go to the MCP server as W3C baggage, so its spans carry this
    turn's session, user, trace name, version and environment (Langfuse v4
    filters and aggregates per observation). Baggage is scoped to the MCP call
    and only injected into that request — never into model-provider calls.
    """

    @tool
    async def search_knowledge_base(query: str, callbacks: Callbacks = None) -> str:
        """Search Northwind Bank's help-center articles (products, fees, limits, policies).
        Returns articles with ids like KB-102 that you must cite."""
        used.append("search_knowledge_base")
        with _nest(callbacks), langfuse.start_as_current_observation(as_type="retriever", name="kb-retrieval",
                                                   input={"query": query, "k": RETRIEVAL_K,
                                                          "min_score": RETRIEVAL_MIN_SCORE}) as r:
            docs = knowledge.search(query, k=RETRIEVAL_K, min_score=RETRIEVAL_MIN_SCORE)
            r.update(output={"documents": [{k: d[k] for k in ("id", "title", "source", "score", "effective_date", "text")}
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
        with _nest(config_), config.attributes(**trace_attrs, as_baggage=True), \
                langfuse.start_as_current_observation(as_type="span", name=f"mcp-client: {name}",
                                                      input=args, metadata={"mcp.server": config.MCP_URL}) as s:
            if session is None:
                data = {"error": "BANKING_SYSTEM_UNAVAILABLE"}
            else:
                # W3C traceparent + baggage → MCP _meta (see config.inject_trace_context)
                res = await session.call_tool(name, args, meta=config.inject_trace_context())
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
    async def get_recent_transactions(account_id: str, days: int = TX_DEFAULT_DAYS,
                                      callbacks: Callbacks = None) -> dict:
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
            # Complete the transformation before ending its observation. Updating
            # an ended span loses the warning metadata in the exported trace.
            if (set(check["pii_shared"]) & {"card", "otp", "national_id", "iban", "account"}
                    and not business.pii_warned(result["answer"])):
                result["answer"] = business.WARNING.get(language, business.WARNING["en"]) + "\n\n" + result["answer"]
                result["pii_warning_added"] = True
                g.update(metadata={"pii_warning_added": "true"})
            g.update(output=result,
                     level="WARNING" if result["leaked"] or result["advice_language"] else "DEFAULT")
        outcome.update(result)
        if result["leaked"] or result.get("pii_warning_added"):
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
                   prompt_label: Optional[str] = None, prompt_version: Optional[int] = None, tags: Optional[list] = None,
                   extra_metadata: Optional[dict] = None, trace_name: str = TRACE_NAME) -> dict:
    """One customer turn produces one trace. Return the answer and Langfuse link."""
    langfuse = config.get_langfuse()
    model = model or config.AGENT_MODEL
    label = prompts.effective_label(prompt_label)
    system_text, lf_prompt = prompts.get_system_prompt(langfuse, label, version=prompt_version)
    if channel == "voice":  # channel modifier: the answer will be spoken, not read
        system_text += ("\n\nThis is a phone call: reply in at most three short spoken sentences, "
                        "no markdown, no lists, no links, and do not read article ids aloud.")
    check = assess_input(message, customer_id)
    language = lang.detect(message)
    evidence: list[str] = []
    used: list[str] = []
    outcome: dict = {}

    # Owning team: the caller may pass its own `team:*` tag (voice → contact-center).
    team = [] if any(t.startswith("team:") for t in (tags or [])) else ["team:retail-digital"]
    trace_tags = sorted(set(["northwind-assistant", f"channel:{channel}", f"lang:{language}"] + team
                            + [f"risk:{r}" for r in check["risks"]] + (tags or [])))
    meta = {"channel": channel, "language": language, "customer_segment": _segment(customer_id), "model": model,
            "prompt_label": label, "prompt_resolution": "version" if prompt_version is not None else "label",
            **{k: str(v) for k, v in (extra_metadata or {}).items()}}

    t0 = _now_ms()
    # Trace version combines the workshop release and the served prompt version.
    served = f"prompt v{lf_prompt.version}" if lf_prompt is not None else "prompt fallback"
    version = f"{config.RELEASE} · {served}"
    with config.attributes(session_id=session_id, user_id=customer_id, tags=trace_tags,
                              metadata=meta, version=version, trace_name=trace_name,
                              prompt=lf_prompt):
        with langfuse.start_as_current_observation(as_type="agent", name=TRACE_NAME, input={"question": message, "messages": (history or []) + [{"role": "user", "content": message}]}) as root:
            trace_id = root.trace_id
            # The environment this turn is recorded under — `sdk-experiment` inside an
            # experiment — so the MCP server's spans land in the same one.
            # A sampled-out turn's span is non-recording and has no attributes.
            environment = (getattr(otel_trace.get_current_span(), "attributes", None) or {}).get(
                LangfuseOtelSpanAttributes.ENVIRONMENT)
            # No tags: a list does not survive W3C baggage (it arrives as one
            # "['a', 'b']" string, i.e. a single garbage tag on the server's spans).
            trace_attrs = {"session_id": session_id, "user_id": customer_id, "trace_name": trace_name,
                           "version": version, "environment": environment}
            handlers = tracing.callbacks()
            run_cfg = {"callbacks": handlers, "recursion_limit": 14,
                       "run_name": "northwind-langgraph", "metadata": {"langfuse_session_id": session_id}}
            llm = make_llm(model)

            async def _invoke(session):
                tools = _build_tools(langfuse, session, customer_id, evidence, used, trace_attrs)
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
            root.update(output={"answer": answer, "context": context, "sources": cited, "retrieved": retrieved, "tools_used": used, "blocked": check["blocked"]}, metadata={
                "context": context,
                "tools_used": ",".join(used) or "none", "cited_sources": ",".join(cited) or "none",
                "retrieved_sources": ",".join(retrieved) or "none",
                "prompt_version": str(getattr(lf_prompt, "version", "fallback")),
                "apm_trace_url": config.apm_url(trace_id),
                "latency_ms": str(round(_now_ms() - t0))})
            obs_id = root.id

    # Experiments attach callback evaluators; avoid double-scoring those runs.
    if channel != "experiment":
        monitoring.record(langfuse, trace_id=trace_id, observation_id=obs_id, check=check,
                          history=history or [], message=message)
    if error is not None:
        config.flush()
    return {"answer": answer, "trace_id": trace_id, "trace_url": config.trace_url(trace_id),
            "apm_url": config.apm_url(trace_id), "sources": cited, "retrieved": retrieved,
            "tools_used": used, "blocked": check["blocked"], "risks": check["risks"],
            "prompt_version": getattr(lf_prompt, "version", None), "model": model, "observation_id": obs_id,
            "error": f"{type(error).__name__}: {error}" if error else None}


def _segment(customer_id: str) -> str:
    return {"C-1002": "premier", "C-1004": "premier"}.get(customer_id, "everyday")
