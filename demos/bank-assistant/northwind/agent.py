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
import os
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

from northwind import business, config, knowledge, lang, masking, prompts

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
# in my app? we want a joint account"). Exempting it is safe because authorization
# does not live in this regex: the MCP server scopes every lookup to the
# authenticated customer, whatever the wording.
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


def _build_tools(langfuse, session: Optional[ClientSession], customer_id: str, evidence: list, used: list):
    """Tools bound to the AUTHENTICATED customer. The model never sees or chooses the id."""

    @tool
    async def search_knowledge_base(query: str, callbacks: Callbacks = None) -> str:
        """Search Northwind Bank's help-center articles (products, fees, limits, policies).
        Returns articles with ids like KB-102 that you must cite."""
        used.append("search_knowledge_base")
        with _nest(callbacks), langfuse.start_as_current_observation(as_type="retriever", name="kb-retrieval",
                                                   input={"query": query, "k": RETRIEVAL_K,
                                                          "min_score": RETRIEVAL_MIN_SCORE}) as r:
            docs = knowledge.search(query, k=RETRIEVAL_K, min_score=RETRIEVAL_MIN_SCORE)
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
            g.update(output={k: v for k, v in result.items() if k != "answer"},
                     level="WARNING" if result["leaked"] or result["advice_language"] else "DEFAULT")
        # Must-always rule enforced in CODE (release 1.5.0): a prompt instruction to
        # warn customers who paste card data was followed only some of the time
        # (the v5 canary showed it), so the guardrail guarantees it.
        if (set(check["pii_shared"]) & {"card", "otp", "national_id", "iban", "account"}
                and not business.pii_warned(result["answer"])):
            result["answer"] = business.WARNING.get(language, business.WARNING["en"]) + "\n\n" + result["answer"]
            result["pii_warning_added"] = True
            g.update(metadata={"pii_warning_added": "true"})
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
    # Trace version = release + prompt version: the key that lets every quality
    # AND business metric be split by what was actually served (canary vs prod).
    served = f"prompt v{lf_prompt.version}" if lf_prompt is not None else "prompt fallback"
    with propagate_attributes(session_id=session_id, user_id=customer_id, tags=trace_tags,
                              metadata=meta, version=f"{config.RELEASE} · {served}", trace_name=trace_name,
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
    biz = None
    if channel != "experiment":
        tool_results = business.evidence_tool_results(evidence)
        answered_in = lang.detect(answer)
        biz = business.classify(
            used=used, actions_ok=[n for n, rs in tool_results.items() if any(business.tool_succeeded(r) for r in rs)],
            blocked=check["blocked"], risks=check["risks"], cited=cited,
            # intent from what the answer CITED, else the retriever's own ranking order
            retrieved_categories=[d.category for d in map(knowledge.get, list(dict.fromkeys(
                cited + re.findall(r"\[(KB-\d{3})\]", "\n".join(evidence))))) if d],
            error=error is not None, language_mismatch=answered_in != language,
            informal=language == "es" and not check["blocked"] and bool(lang.informal_markers(answer)),
            output_leak=bool(outcome.get("leaked")), advice_language=bool(outcome.get("advice_language")),
            tool_errors=sum(1 for rs in tool_results.values() for r in rs if not business.tool_succeeded(r)),
            callback_topics=[r.get("topic", "") for r in tool_results.get("schedule_callback", []) if isinstance(r, dict)],
            upsell=_unsolicited_upsell(message, answer), pii_shared=check["pii_shared"], answer=answer,
            dispute_requested=bool(business.DISPUTE_REQUEST.search(message)) and not check["blocked"])
        _score_business(langfuse, trace_id, obs_id, biz)
        _score_efficiency(langfuse, trace_id, obs_id, state["messages"], len(history or []) + 1, model,
                          (_now_ms() - t0) / 1000.0)
        _score_dispute(langfuse, trace_id, obs_id, message, used,
                       business.evidence_tool_results(evidence), check["blocked"], answer)
    if error is not None:
        config.flush()
    return {"answer": answer, "trace_id": trace_id, "trace_url": config.trace_url(trace_id),
            "apm_url": config.apm_url(trace_id), "sources": cited, "retrieved": retrieved,
            "tools_used": used, "blocked": check["blocked"], "risks": check["risks"],
            "prompt_version": getattr(lf_prompt, "version", None), "model": model, "business": biz,
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


def _unsolicited_upsell(question: str, answer: str) -> bool:
    from northwind import evals
    e = evals.no_unsolicited_upsell(input={"question": question}, output={"answer": answer})
    return isinstance(e, evals.Evaluation) and e.value == 0.0


def _score_business(langfuse, trace_id, obs_id, biz: dict):
    """Business-outcome scores (see northwind/business.py) on the same observation."""
    s = lambda **kw: langfuse.create_score(trace_id=trace_id, observation_id=obs_id, **kw)  # noqa: E731
    s(name="task-outcome", value=biz["task-outcome"], data_type="CATEGORICAL")
    s(name="intent", value=biz["intent"], data_type="CATEGORICAL")
    s(name="failure-mode", value=biz["failure-mode"], data_type="CATEGORICAL")
    s(name="contained", value=float(biz["contained"]), data_type="NUMERIC",
      comment="1 = handled without a human")
    s(name="unsolicited-upsell", value=float(biz["unsolicited-upsell"]), data_type="NUMERIC",
      comment="1 = recommended a product the customer did not ask about (conduct risk P3)")
    if "advisor-offered" in biz:
        s(name="advisor-offered", value=float(biz["advisor-offered"]), data_type="NUMERIC",
          comment="investment question: 1 = offered/booked a licensed advisor session")
    if "pii-education" in biz:
        s(name="pii-education", value=float(biz["pii-education"]), data_type="NUMERIC",
          comment="customer shared card/ID data: 1 = told them never to share it")
    s(name="value-usd", value=biz["value-usd"], data_type="NUMERIC",
      comment=(f"advisor lead (demo assumption: USD {business.ADVISOR_LEAD_VALUE} expected value)"
               if biz["task-outcome"] == "advisor-lead" else
               f"avoided contact cost (demo assumption: USD {business.COST_PER_CONTACT} per contact × "
               f"deflection credit for '{biz['task-outcome']}')"))


def _score_dispute(langfuse, trace_id, obs_id, message, used, tool_results, blocked, answer=""):
    """Dispute requests: 1 = a dispute was actually opened in this turn (self-service)."""
    if blocked or not (business.DISPUTE_REQUEST.search(message) or "open_dispute" in used):
        return
    opened = any(business.tool_succeeded(r) for r in tool_results.get("open_dispute", []))
    if not opened and business.dispute_awaiting_confirmation(answer):
        return  # found the charge and asked to confirm — judged on the turn that opens it
    windows = [r.get("window_days") for r in tool_results.get("get_recent_transactions", []) if isinstance(r, dict)]
    langfuse.create_score(trace_id=trace_id, observation_id=obs_id, name="dispute-resolved",
                          value=1.0 if opened else 0.0, data_type="NUMERIC",
                          comment=f"open_dispute succeeded={opened}; transaction lookback windows={windows}")


def _score_efficiency(langfuse, trace_id, obs_id, messages, first_new: int, model: str, seconds: float):
    """Per-turn cost / LLM calls / latency — chartable by trace version (cost regressions)."""
    new = [m for m in messages[first_new:] if isinstance(m, AIMessage) and getattr(m, "usage_metadata", None)]
    tin = sum(m.usage_metadata.get("input_tokens", 0) for m in new)
    tout = sum(m.usage_metadata.get("output_tokens", 0) for m in new)
    pin, pout = _PRICE.get(model, (3.0, 15.0))
    s = lambda **kw: langfuse.create_score(trace_id=trace_id, observation_id=obs_id, data_type="NUMERIC", **kw)  # noqa: E731
    s(name="turn-cost-usd", value=round((tin * pin + tout * pout) / 1e6, 6),
      comment=f"{tin} input + {tout} output tokens at list price ({model}); trace cost is authoritative")
    s(name="llm-calls", value=float(len(new)))
    s(name="turn-latency-s", value=round(seconds, 2))
