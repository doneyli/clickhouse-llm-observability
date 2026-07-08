"""
The Real Estate Property Concierge — an instrumented tool-using agent.

Flow per turn (each step is a Langfuse observation, so the trace tree reads
top-to-bottom like the agent's reasoning):

    property-concierge            (root span; trace input=query, output=answer)
    ├─ plan                       (generation) extract structured constraints
    ├─ agent-turn-1               (generation) Claude decides which tools to call
    ├─ tool:search_listings       (span)       catalog search
    ├─ agent-turn-2               (generation) synthesis / more tools
    └─ ...

Observation-level CODE scores are attached to the final generation on EVERY
trace (live and experiment), so "scores on individual observations" is always
demonstrable. LLM-as-a-Judge scores are attached at the trace level by the
callers (live-traffic script) or as experiment evaluators.
"""

import json
import re
from typing import Any, Dict, List, Optional

from langfuse import propagate_attributes

from .config import get_langfuse, AGENT_MODEL, BASE_TAGS
from .catalog import LISTINGS
from .tools import execute_tool
from .llm import call_llm, tools_for, append_assistant, append_tool_results, provider_of
from .scoring import run_code_evaluators, extract_listing_ids
from .prompts import get_plan_prompt, get_agent_prompt, link_kwargs, PRODUCTION_LABEL

MAX_ITERS = 5

# --- lightweight language detection (Spanish vs English) for language-match ---
# Only Spanish FUNCTION/verb words — strong language signals. Deliberately NOT
# real-estate nouns or place names (piso, terraza, barrio, familia, Malasaña…):
# those appear verbatim in English answers and caused correct English replies to
# be misclassified as Spanish.
_ES_MARKERS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "del", "al", "con",
    "para", "por", "sin", "sobre", "cerca", "que", "qué", "cómo", "cuánto",
    "dónde", "este", "esta", "ese", "esa", "aquí", "quiero", "busco", "buscando",
    "necesito", "tienes", "tiene", "hay", "está", "están", "es", "son", "más",
    "muy", "también", "pero", "porque", "tu", "su", "te", "ideal",
}
# Unambiguously-English function words (absent in Spanish). Language is decided by
# whichever set the answer uses MORE — robust to Spanish place/feature names
# (Malasaña, terraza, El Palo) that legitimately appear in English answers.
_EN_MARKERS = {
    "the", "is", "are", "you", "your", "and", "with", "of", "for", "this", "that",
    "we", "it", "have", "has", "will", "can", "would", "be", "been", "near", "here",
    "there", "within", "great", "which", "at", "as", "an", "to", "in", "on", "i'd",
}


def detect_language(text: str) -> str:
    if not text:
        return "en"
    words = re.findall(r"[a-záéíóúñü']+", text.lower())
    if not words:
        return "en"
    es = sum(1 for w in words if w in _ES_MARKERS)
    en = sum(1 for w in words if w in _EN_MARKERS)
    return "es" if (es > en and es >= 2) else "en"


def _extract_json(text: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _apply_fault(answer: str, fault: Optional[str], constraints: Dict[str, Any]):
    """Deterministically degrade an answer so the demo shows scores that VARY.

    Returns (answer, extra_listing_or_None). The extra listing (if any) should be
    registered as 'retrieved' by the caller so the degradation is isolated to the
    intended metric. Only used by the live-traffic script (never portal/experiment).
    """
    if not fault:
        return answer, None
    if fault == "hallucinate":
        # Recommend a listing id that does not exist -> grounded-listings fails.
        return answer + "\n\nYou may also love [MAD-999], a rare gem just added to the market.", None
    if fault == "over_budget":
        # Recommend a REAL listing in the RIGHT city that is over budget, so only
        # budget-adherence fails (grounding + location stay clean).
        mp = constraints.get("max_price")
        city = (constraints.get("location") or "").lower()
        op = constraints.get("operation")
        pool = [l for l in LISTINGS
                if (not city or city in l["city"].lower())
                and (op is None or l["operation"] == op)
                and (mp is None or l["price"] > mp)]
        if pool:
            l = min(pool, key=lambda x: x["price"])  # closest to budget
            unit = "/mo" if l["operation"] == "rent" else ""
            return (answer + f"\n\nIf you can stretch your budget, [{l['id']}] in {l['neighborhood']} "
                    f"(€{l['price']:,}{unit}) is also excellent value.", l)
    return answer, None


def run_turn(
    query: str,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    extra_tags: Optional[List[str]] = None,
    fault: Optional[str] = None,
    is_experiment: bool = False,
    model: Optional[str] = None,
    prompt_label: str = PRODUCTION_LABEL,
    history: Optional[List[Dict[str, Any]]] = None,
    conversation_trace_id: Optional[str] = None,
    turn_index: int = 0,
) -> Dict[str, Any]:
    """Run one agent turn.

    Multi-turn conversations: pass a stable `conversation_trace_id`
    (e.g. langfuse.create_trace_id(seed=session_id)) plus the 0-based
    `turn_index`. Every turn of the conversation then lands in the SAME trace as
    a `turn-N` observation, with its plan/tool/synthesis steps nested underneath —
    so one conversation = one traceId. `history` gives the agent the prior turns
    so follow-ups like "keep it under 400k" / "that one" resolve.
    """
    lf = get_langfuse()
    model = model or AGENT_MODEL
    history = history or []
    multiturn = conversation_trace_id is not None

    # Multi-turn: this turn is a `turn-N` span attached to the shared conversation
    # trace. Single-turn: the turn is its own trace root.
    if multiturn:
        root_cm = lf.start_as_current_observation(
            as_type="span", name=f"turn-{turn_index + 1}",
            trace_context={"trace_id": conversation_trace_id})
    else:
        root_cm = lf.start_as_current_observation(as_type="span", name="property-concierge")

    with root_cm as root:
        # Trace-level attributes (only in live mode; the experiment owns the trace).
        if not is_experiment:
            ctx = propagate_attributes(
                session_id=session_id,
                user_id=user_id,
                tags=BASE_TAGS + (extra_tags or []),
                trace_name="conversation" if multiturn else "property-concierge",
            )
        else:
            from contextlib import nullcontext
            ctx = nullcontext()

        with ctx:
            trace_id = lf.get_current_trace_id()
            root.update(input={"query": query},
                        metadata={"agent_model": model, "provider": provider_of(model),
                                  "prompt_label": prompt_label, "turn": turn_index + 1})
            # Trace-level input: set the opening question once (first turn).
            if not is_experiment and (not multiturn or turn_index == 0):
                lf.update_current_trace(input={"query": query},
                                        metadata={"agent_model": model})

            # ---------------- 1) plan: extract structured constraints ----------
            # Include prior turns so references like "keep it under 400k" or
            # "that one" resolve against the conversation.
            plan_messages = history + [{"role": "user", "content": query}]
            constraints: Dict[str, Any] = {}
            # Fetch the plan prompt from Langfuse (production label) with a hard
            # fallback; link it to the generation so per-version metrics accrue.
            plan_prompt = get_plan_prompt()
            plan_system = plan_prompt.compile()
            with lf.start_as_current_observation(
                as_type="generation", name="plan", model=model, **link_kwargs(plan_prompt)
            ) as gen:
                gen.update(input=plan_messages)
                res = call_llm(model, plan_system, plan_messages, max_tokens=400)
                constraints = _extract_json(res["text"])
                gen.update(output=constraints, usage_details=res["usage"],
                           **({"cost_details": res["cost_details"]} if res.get("cost_details") else {}))
            lang = constraints.get("query_language") or detect_language(query)
            constraints.setdefault("query_language", lang)

            # ---------------- 2) agentic tool-use loop -------------------------
            fault_note = ""
            answer_lang = lang
            if fault == "wrong_language":
                fault_note = "Regardless of the user's language, answer in English."
                answer_lang = "en"
            # Fetch the agent system prompt from Langfuse for the requested label
            # (production by default; the experiment can request `candidate`),
            # with a hard fallback. Compile the {{lang}}/{{fault_note}} variables.
            agent_prompt = get_agent_prompt(label=prompt_label)
            system = agent_prompt.compile(lang=answer_lang, fault_note=fault_note)
            agent_prompt_link = link_kwargs(agent_prompt)

            messages: List[Dict[str, Any]] = history + [{"role": "user", "content": query}]
            tools = tools_for(model)
            tools_called: List[str] = []
            retrieved_ids: List[str] = []
            retrieved_listings: List[Dict[str, Any]] = []
            evidence: List[Dict[str, Any]] = []   # ALL tool outputs (for groundedness judge)
            final_text = ""
            final_gen_id = None

            for i in range(MAX_ITERS):
                with lf.start_as_current_observation(
                    as_type="generation", name=f"agent-turn-{i+1}", model=model,
                    **agent_prompt_link
                ) as gen:
                    gen.update(input=messages)
                    res = call_llm(model, system, messages, tools=tools, max_tokens=1500)
                    gen.update(
                        output=res["text"] or "[tool calls]",
                        usage_details=res["usage"],
                        metadata={"stop_reason": res["stop_reason"]},
                        **({"cost_details": res["cost_details"]} if res.get("cost_details") else {}),
                    )
                    final_gen_id = gen.id

                if res["stop_reason"] != "tool_use":
                    final_text = res["text"]
                    break

                # Execute each requested tool as its own span (sibling of the gen).
                append_assistant(messages, res)
                tool_outputs = []
                for call in res["tool_calls"]:
                    tools_called.append(call["name"])
                    with lf.start_as_current_observation(
                        as_type="span", name=f"tool:{call['name']}"
                    ) as tspan:
                        tspan.update(input=call["input"])
                        result = execute_tool(call["name"], call["input"])
                        tspan.update(output=result)
                    evidence.append({"tool": call["name"], "input": call["input"], "output": result})
                    # A listing is "retrieved" if it came back from a search OR
                    # was fetched by id via get_listing_details — both are real
                    # grounding, so grounded-listings must credit either.
                    if call["name"] == "search_listings":
                        for l in result.get("listings", []):
                            if l["id"] not in retrieved_ids:
                                retrieved_ids.append(l["id"])
                                retrieved_listings.append(l)
                    elif call["name"] == "get_listing_details" and result.get("id"):
                        if result["id"] not in retrieved_ids:
                            retrieved_ids.append(result["id"])
                            retrieved_listings.append(result)
                    tool_outputs.append({"id": call["id"],
                                         "content": json.dumps(result, ensure_ascii=False)})
                append_tool_results(model, messages, tool_outputs)
            else:
                # Loop exhausted without a final text answer.
                final_text = final_text or "I need a little more detail to help — could you refine your search?"

            # ---------------- 3) optional demo fault injection -----------------
            final_text, extra = _apply_fault(final_text, fault, constraints)
            if extra:
                if extra["id"] not in retrieved_ids:
                    retrieved_ids.append(extra["id"])
                retrieved_listings.append(extra)
                evidence.append({"tool": "search_listings", "input": {"fault_demo": True},
                                 "output": {"listings": [extra]}})

            # ---------------- 4) assemble structured result -------------------
            response_language = "en" if fault == "wrong_language" else detect_language(final_text)
            result: Dict[str, Any] = {
                "query": query,
                "answer": final_text,
                "constraints": constraints,
                "response_language": response_language,
                "listings_shown": extract_listing_ids(final_text),
                "retrieved_ids": retrieved_ids,
                "retrieved_listings": retrieved_listings,
                "evidence": evidence,
                "tools_called": tools_called,
                "mortgage_estimated": "calculate_mortgage" in tools_called,
                "trace_id": trace_id,
                "final_generation_id": final_gen_id,
                "model": model,
                "prompt_label": prompt_label,
                "fault": fault,
            }

            root.update(output=final_text)
            # Keep the trace output pointed at the latest answer as turns accrue.
            if not is_experiment:
                lf.update_current_trace(output=final_text)

            # ---------------- 5) observation-level CODE scores ----------------
            # Live mode: attach deterministic code scores to the synthesis
            # observation so every live trace carries scores on an observation.
            # Experiment mode: skip — the experiment evaluators score each item
            # at the item level against the dataset's ground-truth constraints
            # (more rigorous, and avoids duplicate score names per trace).
            if not is_experiment:
                for s in run_code_evaluators(result):
                    if final_gen_id:
                        lf.create_score(trace_id=trace_id, observation_id=final_gen_id,
                                        name=s.name, value=s.value, data_type=s.data_type,
                                        comment=s.comment)

    if not is_experiment:
        lf.flush()
    return result
