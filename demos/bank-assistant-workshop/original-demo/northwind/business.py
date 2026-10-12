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
# An OFFER to set something up with an advisor — "arrange a session", "connect you
# with a licensed advisor", "agendarle una cita con un asesor". Pointing the customer
# away ("please speak with one of our licensed advisors") is not an offer, and the
# second arm needs the advisor NOUN: "I can't advise … please call us" is not one.
_ADVISOR_OFFER = re.compile(
    r"(?i)\b(book|schedule|arrange|set up|connect you|put you in touch|introduce you"
    r"|agend|program(ar|e|o)|reserv|coordin|conect|pon(er|erle|erlo|erla)? en contacto)\w*\b.{0,80}\b(advis|asesor)"
    r"|\b(advisor|adviser|asesor)(a|es|as)?\b.{0,80}\b(session|appointment|call|meeting|sesi[oó]n|cita|llamada|reuni[oó]n)")
# Telling the customer not to share card data, in any of the ways a model phrases it.
_PII_WARNING = re.compile(r"(?i)(never|don't|do not|please don't|avoid) shar|(will|would) never ask|never ask (you )?for"
                          r"|(recommend|suggest|advise|best) (not|against|avoiding) shar|refrain from shar"
                          r"|(no|don't|do not) need to (share|give|send|type|enter)"
                          r"|only (use|share|give|need) the last (4|four) digits"
                          r"|nunca (comparta|compart|le pediremos|pedimos|solicitamos)|no (comparta|debe compartir|compartir)"
                          r"|evite compartir|no (es necesario|hace falta) que (me )?(d[eé]|comparta|env[ií]e|escriba|proporcione)"
                          r"|(use|utilice|indique|basta con) (solo |solamente |[uú]nicamente )?los [uú]ltimos (4|cuatro) d[ií]gitos")
_SENSITIVE = {"card", "otp", "national_id", "iban", "account"}
# A REQUEST to dispute a charge — not a question that merely mentions a past dispute
# ("how long did my last dispute take?" is a complaint, not a dispute request), and not
# a policy question ("how long do I have to dispute a card transaction?"). The text is
# read one sentence at a time: a policy phrase before the dispute words in the SAME
# sentence makes it a question, so "I don't recognise this charge. How long will the
# dispute take?" is still a request.
_DISPUTE_POLICY_Q = (r"\b(how long|how many days|how much time|time limit|deadline"
                     r"|cu[aá]nto tiempo|cu[aá]ntos d[ií]as|qu[eé] plazo|plazo (para|de))\b")
DISPUTE_REQUEST = re.compile(
    rf"(?i)(^|[.?!¡¿\n])((?!{_DISPUTE_POLICY_Q})[^.?!])*?"
    r"(\b(please |want to |like to |need to |can you |could you )?dispute (the|this|that|a|an|my) "
    r"(\w+ ){0,4}(charge|transaction|payment|debit)|\bdispute it\b|open (a |the )?dispute|chargeback"
    r"|\b(please|kindly|go ahead and) dispute\b"   # "Please dispute the duplicate one."
    r"|don['\u2019]?t recogni[sz]e|didn['\u2019]?t (make|authori[sz]e)|unauthori[sz]ed (charge|transaction)"
    r"|disput(ar|e|a) (el|este|ese|un|una|la) (\w+ ){0,3}(cargo|transacci[oó]n|cobro|pago)"
    r"|abr(a|ir) (una |la )?disputa|no reconozco|desconozco (el|un|este) cargo|cargo que no (hice|reconozco)"
    r"|reclam(ar|o) (un|el) cargo)")
# The agent found the charge and asks the customer to confirm before opening it.
_AWAITING_CONFIRMATION = re.compile(
    r"(?i)(shall i|should i|would you like me to|do you want me to|can you confirm|please confirm|is this the"
    r"|¿desea que|¿quiere que|¿le gustar[ií]a que|¿confirma|¿me confirma|¿es (este|ese|esta)|por favor confirme"
    r"|¿le abro|¿procedo)")
# Evidence that the charge was FOUND: its transaction id, an amount in either currency
# order ("$412", "USD 412", "389,99 USD", "412 dólares"), or the answer naming the
# charge — unless it says it could not find it ("I couldn't find that charge. Can you
# confirm the merchant?" asks for details; nothing is awaiting confirmation).
_TX_ID = re.compile(r"\bTX-\d{5}\b")
_AMOUNT = re.compile(r"(?i)(\$|\bUSD|\bEUR|€)\s?\d|\d[\d.,]*\s?(USD|EUR|€|d[oó]lares|dollars)\b")
_CHARGE_WORD = re.compile(r"(?i)\b(charge|transaction|payment|debit|cargo|cobro|transacci[oó]n|pago|movimiento)s?\b")
_NOT_FOUND = re.compile(
    r"(?i)\b(do not|don't|can't|cannot|could not|couldn't|did not|didn't|unable to|was not able to|wasn't able to)"
    r" (see|find|locate|spot)\b"
    r"|\bno (veo|encuentro|encontr[eé]|aparece|figura|hay ning[uú]n)\b"
    r"|\bno (pude|puedo|logr[eé]|logro) (encontrar|ver|ubicar|localizar)\b")


def _plain(text: str) -> str:
    """Models write typographic apostrophes (don’t); the patterns use ASCII ones."""
    return (text or "").replace("\u2019", "'")


def advisor_offered(answer: str) -> bool:
    return bool(_ADVISOR_OFFER.search(_plain(answer)))


def pii_warned(answer: str) -> bool:
    return bool(_PII_WARNING.search(_plain(answer)))


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
    elif dispute_requested and dispute_awaiting_confirmation(answer):
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
    """The agent found the charge and asks before opening it — judged on the next turn.

    A transaction id is enough on its own: it can only come from the banking tool. An
    amount or the word "charge" also counts unless the answer says the charge was not
    found — the model often echoes the customer's amount back ("I couldn't find a
    USD 412 charge — can you confirm the date?"), and that turn is a miss, not a wait.
    """
    answer = _plain(answer)
    if not _AWAITING_CONFIRMATION.search(answer):
        return False
    if _TX_ID.search(answer):
        return True
    return not _NOT_FOUND.search(answer) and bool(_AMOUNT.search(answer) or _CHARGE_WORD.search(answer))
