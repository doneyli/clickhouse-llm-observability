"""Route registry + HTTP dispatch to the existing demo services.

Registry keys MUST match router.ROUTES — drift lands in fallback, visibly.
Each dispatch passes `trace_context` so the handler's own spans nest under THIS
trace (Langfuse SDK v3 distributed tracing): the same trace shows the router
decision AND the specialist's full existing instrumentation.
"""

import os

import httpx

import langfuse_config as lf

HANDLERS = {
    "analytics_sql": os.getenv("TEXT_TO_SQL_URL", "http://text-to-sql:8000"),
    "docs_simple": os.getenv("VECTOR_RAG_URL", "http://vector-rag:8000"),
    "docs_complex": os.getenv("AGENTIC_RAG_URL", "http://agentic-rag:8000"),
}
FALLBACK_MODEL = os.getenv("ROUTER_FALLBACK_MODEL", "claude-haiku-4-5")
HANDLER_TIMEOUT = float(os.getenv("HANDLER_TIMEOUT", "180"))


def _anthropic():
    """Lazy Anthropic client (unit tests patch this)."""
    from anthropic import Anthropic
    return Anthropic()


def dispatch(decision: dict, question: str, session_id: str) -> dict:
    """Route to exactly one specialist handler over HTTP, or the fallback."""
    route = decision["route"]
    if route == "fallback":
        return run_fallback(decision, question)

    base_url = HANDLERS[route]
    with lf.observe(
        f"dispatch-{route}", as_type="agent",
        input={"question": question, "handler": base_url},
    ) as obs:
        payload = {"question": question, "session_id": session_id}
        if obs:  # v3 distributed nesting: handler joins THIS trace under THIS span
            payload["trace_context"] = {"trace_id": obs.trace_id, "parent_span_id": obs.id}
        try:
            r = httpx.post(f"{base_url}/query", json=payload, timeout=HANDLER_TIMEOUT)
            r.raise_for_status()
            body = r.json()
            answer = body.get("answer", body)
            if obs:
                obs.update(output={"answer": answer})
            return {"answer": answer, "handled_by": route}
        except httpx.HTTPError as e:
            # Handler down / timeout -> graceful degrade to fallback + escalation,
            # NOT a 500 (deliberate: the router degrades if a specialist is down).
            decision = {**decision, "fallback_reason": "handler_unreachable",
                        "fallback_triggered": True}
            if obs:
                obs.update(output={"error": str(e)}, level="ERROR")
            return run_fallback(decision, question)


def run_fallback(decision: dict, question: str) -> dict:
    """Best-effort answer with an explicit caveat + machine-readable escalation event."""
    with lf.observe("fallback-handler", as_type="generation",
                    input={"question": question}) as obs:
        resp = _anthropic().messages.create(
            model=FALLBACK_MODEL, max_tokens=600,
            messages=[{
                "role": "user",
                "content": "Answer briefly and note that this question could not be routed "
                           f"to a specialist.\n\nQuestion: {question}",
            }],
        )
        answer = resp.content[0].text
        if obs:
            obs.update(output=answer, metadata={"route": "fallback"})

    # HITL escalation: a point-in-time event carrying a machine-readable reason
    # (low_confidence | out_of_scope | unknown_route | malformed_output |
    # handler_unreachable) -> feeds a 'router-triage' annotation queue.
    with lf.observe("escalate-to-human", as_type="event",
                    input={"reason": decision.get("fallback_reason"),
                           "confidence": decision.get("confidence"),
                           "rationale": decision.get("rationale")}):
        pass

    return {"answer": answer, "handled_by": "fallback",
            "escalation_reason": decision.get("fallback_reason")}
