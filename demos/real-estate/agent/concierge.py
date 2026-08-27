"""
The Real Estate Property Concierge — an instrumented tool-using agent.

Flow per turn (each step is a Langfuse observation, so the trace tree reads
top-to-bottom like the agent's reasoning):

    handle-concierge-chat-message (root span; input=query, output=answer — v4
    │                              derives the trace's input/output from these)
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

from .config import get_langfuse, record_score, flush_langfuse, AGENT_MODEL, BASE_TAGS
from .catalog import LISTINGS
from .tools import execute_tool
from .llm import call_llm, tools_for, append_assistant, append_tool_results, provider_of
from .scoring import run_code_evaluators, extract_listing_ids, prior_ids_from_history
from .prompts import get_plan_prompt, get_agent_prompt, link_kwargs, PRODUCTION_LABEL

MAX_ITERS = 5

# Stable, low-cardinality name for each turn's trace AND its root span (Langfuse
# best practice — verb-first, like the docs' own `handle-chatbot-message`). The
# question lives in the trace INPUT, not the name. Keeping the trace name and the
# root span name identical lets newer Langfuse UIs render them as a single node.
TRACE_NAME = "handle-concierge-chat-message"

# --- conversation-level evaluation hooks -------------------------------------
# A Langfuse LLM-as-a-Judge rule can target an OBSERVATION or an EXPERIMENT —
# never a session, because "Langfuse does not inherently know when a session has
# concluded" (langfuse.com/resources/engineering/evaluating-sessions-conversations).
# The app therefore has to declare the end of a conversation itself, and hand the
# judge a single observation that holds the whole thing. Two mechanisms, both used
# below:
#
#   CONVERSATION_END_TAG  propagated on the final turn only, so a rule can be
#                         scoped to fire ONCE per conversation instead of paying
#                         to re-judge a growing transcript on every turn.
#   SNAPSHOT_NAME         a dedicated observation on that final turn whose input
#                         IS the full transcript. An observation-level evaluator
#                         sees only the observation it matched — not siblings, not
#                         children — so without this (or history on the root, also
#                         added below) no judge can reason across turns at all.
#
# The sanctioned alternative — stuffing the transcript into metadata — is
# deliberately NOT used: metadata values are coerced to strings and capped, so a
# judge would silently score a clipped conversation.
CONVERSATION_END_TAG = "conversation_end"
SNAPSHOT_NAME = "conversation-snapshot"

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
    turn_index: int = 0,
    is_final_turn: bool = False,
) -> Dict[str, Any]:
    """Run one agent turn.

    Every turn is its OWN trace — the Langfuse rule of thumb is "one trace = one
    invocation of your system" (one API call / one agent execution). Multi-turn
    conversations are stitched together by a shared `session_id`, so Langfuse's
    **Sessions** view lists each turn as its own trace, in order. Pass the 0-based
    `turn_index` (surfaced as the `turn` metadata label) and `history` (the prior
    turns) so follow-ups like "keep it under 400k" / "that one" resolve against
    the conversation. See https://langfuse.com/academy/tracing#traces-vs-sessions.

    `is_final_turn=True` marks this turn as the end of the conversation: it adds
    the `conversation_end` tag and emits a `conversation-snapshot` observation
    carrying the full transcript, which is what a conversation-level judge runs
    on. Only the CALLER knows a conversation is over (Langfuse cannot infer it),
    so this is always explicit. See CONVERSATION_END_TAG above.
    """
    lf = get_langfuse()
    model = model or AGENT_MODEL
    history = history or []

    # One turn = one trace, rooted at the `handle-concierge-chat-message` span.
    # The shared session_id (set below) groups a conversation's turns in Sessions.
    with lf.start_as_current_observation(as_type="span", name=TRACE_NAME) as root:
        # Trace-level attributes (only in live mode; the experiment owns the trace).
        if not is_experiment:
            # Stable, low-cardinality trace name (Langfuse best practice): the
            # turn's question lives in the trace INPUT (shown in the Traces table),
            # NOT the name, so traces stay groupable/filterable/targetable. Turns
            # are stitched into one conversation by session_id (see Sessions view).
            ctx = propagate_attributes(
                session_id=session_id,
                user_id=user_id,
                # Fault-injected traces self-identify via a `fault:<name>` tag so
                # they are filterable (and explainable) during a demo.
                # `conversation_end` is what scopes a conversation-level judge to
                # one execution per conversation. It MUST go through
                # propagate_attributes rather than onto the trace alone:
                # observation-level rules check filter attributes on the
                # OBSERVATION, so an un-propagated tag matches nothing.
                tags=(BASE_TAGS + (extra_tags or [])
                      + ([f"fault:{fault}"] if fault else [])
                      + ([CONVERSATION_END_TAG] if is_final_turn else [])),
                trace_name=TRACE_NAME,
                # SDK v4 replaced `update_current_trace(metadata=)` with this context
                # manager, which stamps these attributes onto the current observation
                # AND all of its children. That fan-out is the point: `agent_model`
                # becomes filterable on every child observation, which is what the
                # observation-level evaluators match on.
                # Values here are coerced to strings and capped at 200 chars, so keep
                # this to short scalars (rich values belong on the observation below).
                metadata={"agent_model": model},
            )
        else:
            from contextlib import nullcontext
            ctx = nullcontext()

        with ctx:
            trace_id = lf.get_current_trace_id()
            # v4 is observations-first: the ROOT observation's input/output IS the
            # trace's input/output — the Traces table and annotation queues DERIVE
            # those columns from it, so this single call is all that is needed.
            # Verified on a live turn with the trace-level setters removed: the
            # Traces table still shows the query and the answer.
            # The deprecated `set_current_trace_io()` escape hatch is therefore
            # deliberately NOT used here. It exists only for legacy *trace*-target
            # LLM-as-a-judge rules, and this project has none — both rules target
            # observations.
            # `history` rides along on the ROOT input on multi-turn calls. Without
            # it, a judge targeting the root observation sees only this turn's
            # question and is structurally incapable of catching a cross-turn
            # failure (a budget stated three turns ago, "that one", a language
            # switch) — no rubric can fix that. Omitted entirely on turn 1 so
            # single-turn traces stay clean, and so the existing `query`-only
            # variable mapping on the seeded Cloud rules keeps working unchanged.
            root.update(input={"query": query, **({"history": history} if history else {})},
                        metadata={"agent_model": model, "provider": provider_of(model),
                                  "prompt_label": prompt_label, "turn": turn_index + 1,
                                  **({"fault": fault} if fault else {}),
                                  **({"conversation_end": True} if is_final_turn else {})})

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
            elif fault == "no_search":
                fault_note = ("You have no tools available right now. Answer from your own "
                              "general knowledge. Do not mention tools, and do not cite "
                              "specific listing ids.")
            elif fault == "wrong_tool":
                fault_note = ("Never mention that a tool is missing or unavailable; answer "
                              "as helpfully as you can with what you have.")
            # Fetch the agent system prompt from Langfuse for the requested label
            # (production by default; the experiment can request `candidate`),
            # with a hard fallback. Compile the {{lang}}/{{fault_note}} variables.
            agent_prompt = get_agent_prompt(label=prompt_label)
            system = agent_prompt.compile(lang=answer_lang, fault_note=fault_note)
            agent_prompt_link = link_kwargs(agent_prompt)

            messages: List[Dict[str, Any]] = history + [{"role": "user", "content": query}]
            tools = tools_for(model)
            # Tool-use faults degrade the REAL agent loop (not the answer post-hoc),
            # so the trace visibly lacks a tool:search_listings span and the
            # used-search-tool score tells the same story the trace does.
            #   no_search  — tool binding broken: the model gets NO tools at all.
            #   wrong_tool — search unavailable: the model improvises with the rest.
            if fault == "no_search":
                tools = []
            elif fault == "wrong_tool":
                tools = [t for t in tools
                         if (t.get("name") or t.get("function", {}).get("name")) != "search_listings"]
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
                # Ids surfaced in PRIOR turns: cross-turn references (comparisons,
                # follow-ups) stay grounded and are exempt from location-match.
                "prior_ids": prior_ids_from_history(history),
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

            # This turn's answer, on the root observation — which is what the trace's
            # output column derives from (see the note on input above for why no
            # trace-level setter is needed).
            root.update(output=final_text)

            # ---------------- 5) conversation snapshot (final turn only) ------
            # ONE observation that owns the whole conversation, so a
            # conversation-level judge has something to match. This is the
            # sanctioned shape for a value that spans observations: "add a
            # dedicated evaluation observation only when no existing observation
            # can own them" (Langfuse evaluator-migration guidance).
            #
            # Why not just point the judge at the root on every turn? Because the
            # root's history grows with the conversation, so a per-turn rule
            # re-judges an ever-longer transcript N times and the cost scales
            # quadratically. This fires once.
            #
            # A rule on this observation MUST filter by `name` (unlike the seeded
            # per-turn rules, which filter on `isRootObservation` precisely so a
            # rename cannot silently break them). That drift risk is real here:
            # renaming SNAPSHOT_NAME without updating the rule stops the judge
            # firing with no error anywhere. Keep the constant and the rule in
            # sync — scripts/seed_managed_evaluators.sh reads it.
            if is_final_turn:
                transcript = history + [{"role": "user", "content": query},
                                        {"role": "assistant", "content": final_text}]
                with lf.start_as_current_observation(as_type="span", name=SNAPSHOT_NAME) as snap:
                    snap.update(input={"transcript": transcript,
                                       "turns": len(transcript) // 2},
                                output=final_text)
                    result["snapshot_observation_id"] = snap.id
                result["transcript"] = transcript

            # ---------------- 6) observation-level CODE scores ----------------
            # Live mode: attach deterministic code scores to the synthesis
            # observation so every live trace carries scores on an observation.
            # Experiment mode: skip — the experiment evaluators score each item
            # at the item level against the dataset's ground-truth constraints
            # (more rigorous, and avoids duplicate score names per trace).
            if not is_experiment:
                for s in run_code_evaluators(result):
                    if final_gen_id:
                        record_score(lf, trace_id=trace_id, observation_id=final_gen_id,
                                     name=s.name, value=s.value, data_type=s.data_type,
                                     comment=s.comment)

    if not is_experiment:
        flush_langfuse(lf)
    return result
