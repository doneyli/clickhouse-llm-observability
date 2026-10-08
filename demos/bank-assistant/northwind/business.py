"""Business outcome of a turn — the bridge from traces to value (GATE-05, EVA-07).

Every assistant turn gets deterministic, zero-cost scores that a business owner
reads, not an engineer:

  task-outcome   what happened for the customer (categorical)
  contained      1 = handled without a human, 0 = needed one / failed  → avg = containment rate
  value-usd      estimated contact-centre cost avoided by this turn    → sum = value delivered
  intent         what the customer came for (categorical)
  failure-mode   the primary thing that went wrong, or "none" (categorical)

They live next to the engineering scores (judges, guardrails, cost, latency) on
the same observation, so a dashboard can put value, quality and spend side by
side, and every number drills down to the traces behind it.

VALUE ASSUMPTIONS ARE DEMO PLACEHOLDERS — replace them with the bank's own
figures (cost per assisted contact, deflection credit per outcome). They are read
from the environment so a presenter can change them without editing code.
"""

from __future__ import annotations

import json
import os
import re

# Fully-loaded cost of one human-assisted contact (phone / chat agent), USD.
COST_PER_CONTACT = float(os.environ.get("NORTHWIND_COST_PER_CONTACT_USD", "6.50"))

# Expected value of one booked session with a licensed advisor (a sales lead). Placeholder.
ADVISOR_LEAD_VALUE = float(os.environ.get("NORTHWIND_ADVISOR_LEAD_USD", "40.00"))

# Share of that cost avoided per outcome (deflection credit). Placeholders.
DEFLECTION = {
    "resolved-self-service": 1.00,   # card blocked, dispute opened — the task is done
    "answered-account-info": 0.60,   # balance / transactions looked up
    "answered-policy": 0.40,         # fee / policy question answered with a source
    "escalated-to-human": 0.00,      # call-back booked — a human still works it
    "advisor-lead": 0.00,            # valued separately as ADVISOR_LEAD_VALUE (revenue, not cost)
    "dispute-unresolved": 0.00,      # customer asked to dispute, no dispute opened → they will call
    "dispute-awaiting-confirmation": 0.00,  # valued on the turn that opens it
    "declined-advice": 0.00,         # referred to a licensed advisor
    "refused": 0.00,                 # guardrail refusal (risk avoided, not cost)
    "out-of-scope": 0.00,
    "failed": 0.00,
}

ACTIONS = {"block_card", "open_dispute"}

_ADVISOR_TOPIC = re.compile(r"(?i)advis|invest|asesor|inversi")
_ADVISOR_OFFER = re.compile(r"(?i)(book|schedule|arrange|set up|agendar|programar|reservar|coordinar)\b.{0,80}\b(advis|asesor)"
                            r"|(advis|asesor)\w*\b.{0,80}\b(session|appointment|call|sesi[oó]n|cita|llamada)")
_PII_WARNING = re.compile(r"(?i)(never|don't|do not|please don't|avoid) shar|(will|would) never ask|never ask (you )?for"
                          r"|only (use|share|give) the last (4|four) digits"
                          r"|nunca (comparta|compart|le pediremos|pedimos|solicitamos)|no (comparta|debe compartir|compartir)"
                          r"|evite compartir|utilice solo los [uú]ltimos (4|cuatro) d[ií]gitos")
_SENSITIVE = {"card", "otp", "national_id", "iban", "account"}
# A REQUEST to dispute a charge — not a question that merely mentions a past dispute
# ("how long did my last dispute take?" is a complaint, not a dispute request).
DISPUTE_REQUEST = re.compile(
    r"(?i)(\b(please |want to |like to |need to |can you |could you )?dispute (the|this|that|a|an|my) "
    r"(\w+ ){0,4}(charge|transaction|payment|debit)|\bdispute it\b|open (a |the )?dispute|chargeback"
    r"|don'?t recogni[sz]e|didn'?t (make|authori[sz]e)|unauthori[sz]ed (charge|transaction)"
    r"|disput(ar|e|a) (el|este|ese|un|una|la) (\w+ ){0,3}(cargo|transacci[oó]n|cobro|pago)"
    r"|abr(a|ir) (una |la )?disputa|no reconozco|desconozco (el|un|este) cargo|cargo que no (hice|reconozco)"
    r"|reclam(ar|o) (un|el) cargo)")
# The agent found the charge and asks the customer to confirm before opening it.
_AWAITING_CONFIRMATION = re.compile(
    r"(?i)(shall i|should i|would you like me to|do you want me to|can you confirm|please confirm|is this the"
    r"|¿desea que|¿quiere que|¿confirma|¿es (este|ese|esta)|por favor confirme|¿le abro|¿procedo)")


def advisor_offered(answer: str) -> bool:
    return bool(_ADVISOR_OFFER.search(answer or ""))


def pii_warned(answer: str) -> bool:
    return bool(_PII_WARNING.search(answer or ""))
LOOKUPS = {"list_accounts", "get_recent_transactions"}


def tool_succeeded(result) -> bool:
    return isinstance(result, dict) and not result.get("error")


def classify(*, used: list[str], actions_ok: list[str], blocked: bool, risks: list[str],
             cited: list[str], retrieved_categories: list[str], error: bool,
             language_mismatch: bool, informal: bool, output_leak: bool, advice_language: bool,
             tool_errors: int, callback_topics: list[str] = (), upsell: bool = False,
             pii_shared: list[str] = (), answer: str = "", dispute_requested: bool = False) -> dict:
    """Map one turn to outcome, containment, value, intent and primary failure mode."""
    if error:
        outcome = "failed"
    elif blocked:
        outcome = "refused"
    elif any(a in ACTIONS for a in actions_ok):
        outcome = "resolved-self-service"
    elif dispute_requested and _AWAITING_CONFIRMATION.search(answer or "") and re.search(r"\bTX-\d{5}\b|\$\s?\d|USD\s?\d", answer or ""):
        outcome = "dispute-awaiting-confirmation"   # found the charge, asks before acting — not a failure
    elif dispute_requested:
        outcome = "dispute-unresolved"   # asked to dispute, nothing opened → will become a contact
    elif any(_ADVISOR_TOPIC.search(t or "") for t in callback_topics):
        outcome = "advisor-lead"
    elif "schedule_callback" in used:
        outcome = "escalated-to-human"
    elif "investment_advice" in risks:
        outcome = "declined-advice"
    elif any(t in LOOKUPS for t in used):
        outcome = "answered-account-info"
    elif "search_knowledge_base" in used:
        outcome = "answered-policy"
    else:
        outcome = "out-of-scope"

    if blocked:
        intent = "attack"
    elif "block_card" in used:
        intent = "card-lost-stolen"
    elif "open_dispute" in used or dispute_requested:
        intent = "dispute"
    elif "investment_advice" in risks or any(_ADVISOR_TOPIC.search(t or "") for t in callback_topics):
        intent = "investment-advice"
    elif "schedule_callback" in used:
        intent = "talk-to-human"
    elif "investment_advice" in risks:
        intent = "investment-advice"
    elif any(t in LOOKUPS for t in used):
        intent = "account-info"
    elif retrieved_categories:
        intent = f"product-{retrieved_categories[0]}"
    else:
        intent = "other"

    sensitive = bool(set(pii_shared) & _SENSITIVE)
    warned = pii_warned(answer)
    offered = advisor_offered(answer)

    # Primary failure mode, most severe first (one per turn keeps the chart honest).
    if error:
        failure = "system-error"
    elif output_leak:
        failure = "pii-in-answer"
    elif tool_errors:
        failure = "tool-error"
    elif outcome == "dispute-unresolved":
        failure = "dispute-not-resolved"
    elif language_mismatch:
        failure = "wrong-language"
    elif advice_language:
        failure = "advice-language"
    elif upsell:
        failure = "unsolicited-upsell"
    elif sensitive and not warned:
        failure = "pii-not-addressed"
    elif outcome == "declined-advice" and not offered:
        failure = "advice-turned-away"
    elif informal:
        failure = "informal-register"
    elif outcome == "answered-policy" and not cited:
        failure = "uncited-answer"
    elif blocked:
        failure = "attack-blocked"
    elif outcome == "out-of-scope":
        failure = "out-of-scope"
    else:
        failure = "none"

    contained = outcome in ("resolved-self-service", "answered-account-info", "answered-policy",
                            "dispute-awaiting-confirmation")
    value = round(ADVISOR_LEAD_VALUE if outcome == "advisor-lead"
                  else COST_PER_CONTACT * DEFLECTION.get(outcome, 0.0), 2)
    out = {"task-outcome": outcome, "contained": 1 if contained else 0, "value-usd": value,
           "intent": intent, "failure-mode": failure, "unsolicited-upsell": 1 if upsell else 0}
    if "investment_advice" in risks:
        out["advisor-offered"] = 1 if (offered or outcome == "advisor-lead") else 0
    if sensitive:
        out["pii-education"] = 1 if warned else 0
    return out


def evidence_tool_results(evidence: list[str]) -> dict:
    """Parse `[tool:name] {json}` evidence lines → {name: [results]}."""
    out: dict = {}
    for line in evidence:
        if line.startswith("[tool:"):
            name, _, payload = line[6:].partition("] ")
            try:
                out.setdefault(name, []).append(json.loads(payload))
            except Exception:  # noqa: BLE001
                out.setdefault(name, []).append({"error": "unparseable"})
    return out


WARNING = {
    "en": ("For your security, please never share your full card number, CVV, PIN or one-time codes in chat — "
           "Northwind will never ask for them."),
    "es": ("Por su seguridad, nunca comparta el número completo de su tarjeta, el CVV, el PIN ni códigos por chat — "
           "Northwind nunca se los pedirá."),
}


def dispute_awaiting_confirmation(answer: str) -> bool:
    return bool(_AWAITING_CONFIRMATION.search(answer or "")
                and re.search(r"\bTX-\d{5}\b|\$\s?\d|USD\s?\d", answer or ""))
