"""Front-door router: classify a question, gate on confidence, dispatch.

The router decision is its OWN Langfuse generation (name='route-query') with
{route, confidence, rationale} as output — so it is filterable, chartable,
dataset-source-able (source_observation_id) and scorable independently of
whichever handler runs next.

Design notes:
- {route, confidence} lives in BOTH `output` (human-readable, judge-mappable)
  and `metadata` (`metadata.route` is the dashboard dimension).
- `router_confidence` is a RUNTIME score (known at execution time — allowed);
  `routing_correct` is POST-HOC only (see scripts/score_misroute.py).
- Registry drift (router emits a label with no registered handler) is handled
  explicitly -> `fallback` with reason=unknown_route, made visible not crashed.
- Determinism: routing wants temperature 0.0 (managed prompt config), a
  deliberate contrast with the demos' default TEMPERATURE=0.7.
"""

import json
import os

import langfuse_config as lf

# taxonomy == handler registry keys (see handlers.HANDLERS)
ROUTES = ("analytics_sql", "docs_simple", "docs_complex")
CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.70"))
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "claude-haiku-4-5")  # model tiering: cheap router
ROUTER_PROMPT_NAME = os.getenv("ROUTER_PROMPT_NAME", "query-router-classifier")
ROUTER_FAULT = os.getenv("ROUTER_FAULT", "")  # e.g. 'sql-blindness' — seeded misroute for the demo
ROUTER_TEMPERATURE = float(os.getenv("ROUTER_TEMPERATURE", "0.0"))

# Local fallback prompt so the demo runs without Langfuse (repo convention).
_FALLBACK_PROMPT = (
    "You are the front-door router for a data-and-docs assistant. "
    "Classify the user question into exactly ONE route:\n"
    "- analytics_sql : needs LIVE numbers from ClickHouse public datasets "
    "(taxi rides, github stars, stackoverflow, prices, ...)\n"
    "- docs_simple   : a single factual/definitional question answerable from docs\n"
    "- docs_complex  : multi-part, comparative, or accuracy-critical doc questions "
    "that merit retrieval verification\n"
    "- out_of_scope  : none of the above (small talk, unrelated domains, unsafe asks)\n\n"
    "Respond ONLY with JSON: "
    '{"route": "<analytics_sql|docs_simple|docs_complex|out_of_scope>", '
    '"confidence": <0.0-1.0>, "rationale": "<one sentence>"}\n'
    "Calibration: confidence reflects P(correct route). Mixed-intent or vague "
    "questions must score below 0.7.\n\n"
    "Question: {{question}}"
)


def _anthropic():
    """Lazy Anthropic client so the module imports without the SDK/keys present
    (unit tests patch this)."""
    from anthropic import Anthropic
    return Anthropic()


def classify(question: str, prompt_label: str = "production", model: str = None) -> dict:
    """One LLM call -> {route, confidence, rationale, fallback_triggered, fallback_reason}.

    `prompt_label` / `model` let the experiment runner vary ONLY the router
    (scripts/run-router-experiment.py) while keeping taxonomy/threshold/handlers
    byte-identical; both default to production behaviour.
    """
    model = model or ROUTER_MODEL
    prompt_obj = lf.get_prompt(ROUTER_PROMPT_NAME, label=prompt_label)
    text = (
        prompt_obj.compile(question=question)
        if prompt_obj
        else _FALLBACK_PROMPT.replace("{{question}}", question)
    )
    if ROUTER_FAULT == "sql-blindness":
        # Deterministic degradation (repo fault convention): blind the router to
        # the SQL route so analytics questions get confidently misrouted.
        text = text.replace("analytics_sql", "docs_simple")

    with lf.observe("route-query", as_type="generation", input={"question": question}) as obs:
        resp = _anthropic().messages.create(
            model=model,
            max_tokens=200,
            temperature=ROUTER_TEMPERATURE,
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        try:
            decision = json.loads(_strip_fences(raw))
            route = decision.get("route", "")
            confidence = float(decision.get("confidence", 0.0))
            rationale = decision.get("rationale", "")
            reason = None
        except (json.JSONDecodeError, TypeError, ValueError):
            route, confidence, rationale, reason = "fallback", 0.0, raw[:200], "malformed_output"

        if reason is None:
            if route not in ROUTES:  # includes out_of_scope + registry drift
                reason = "out_of_scope" if route == "out_of_scope" else "unknown_route"
                route = "fallback"
            elif confidence < CONFIDENCE_THRESHOLD:
                reason, route = "low_confidence", "fallback"

        result = {
            "route": route,
            "confidence": confidence,
            "rationale": rationale,
            "fallback_triggered": route == "fallback",
            "fallback_reason": reason,
        }
        if obs:
            obs.update(
                output={"route": route, "confidence": confidence, "rationale": rationale},
                metadata={
                    "route": route,
                    "threshold": CONFIDENCE_THRESHOLD,
                    "fallback_triggered": route == "fallback",
                    "router_model": model,
                },
                model=model,
                prompt=prompt_obj if prompt_obj else None,  # links prompt version -> trace
            )
            result["router_observation_id"] = obs.id  # for post-hoc scoring / dataset pinning
            result["router_trace_id"] = obs.trace_id
        # Runtime score on the router's own observation -> chartable, filterable.
        lf.score_current_span(
            "router_confidence", confidence, comment=f"threshold={CONFIDENCE_THRESHOLD}"
        )
    return result


def _strip_fences(raw: str) -> str:
    """Tolerate ```json fenced output from the model before json.loads."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.replace("```json", "").replace("```", "").strip()
    return s
