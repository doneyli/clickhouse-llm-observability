"""
Langfuse code evaluator `handoff-context-complete` (Python, standard library only).

Runs inside Langfuse on every `handoff` observation (rule: metadata.role = handoff, in
production). The observation's input is what crossed the boundary
({to, kind, reason, message, payload}); its metadata carries the target agent's
required fields, written by the transport. Scores:

  handoff-context-complete  BOOLEAN  every required field is present (comment names the missing ones)
  handoff-payload-clean     BOOLEAN  no payment-card number in the payload (it is copied into
                                     another agent's context, and into a partner's)

A delegation (`ask_*`) carries its request as the message, so the message counts as the
issue summary. Continuations carry only the customer's reply and are not checked for fields.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

try:  # the Langfuse runtime may provide these; define them for local runs and tests
    Score  # noqa: B018
    EvaluationResult  # noqa: B018
except NameError:
    @dataclass
    class Score:
        name: str
        value: Any
        data_type: str
        comment: Any = None
        config_id: Any = None
        metadata: Any = None

    @dataclass
    class EvaluationResult:
        scores: list = field(default_factory=list)


def _decode(value):
    """The Langfuse Python SDK v5 JSON-encodes metadata values on spans; accept both forms."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _card_numbers(text: str) -> list:
    hits = []
    for m in re.finditer(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            hits.append(digits[-4:])
    return hits


def evaluate(ctx):
    md = _decode(ctx.observation.metadata) or {}
    md = {k: _decode(v) for k, v in md.items()} if isinstance(md, dict) else {}
    inp = _decode(ctx.observation.input) or {}
    inp = inp if isinstance(inp, dict) else {}
    payload = _decode(inp.get("payload")) or {}
    payload = payload if isinstance(payload, dict) else {}
    kind = str(md.get("handoff_kind") or inp.get("kind") or "")
    target = str(md.get("handoff_to") or inp.get("to") or "?")

    scores = []
    cards = _card_numbers(json.dumps(payload))
    scores.append(Score(name="handoff-payload-clean", value=not cards, data_type="BOOLEAN",
                        comment=(f"card number ending {', '.join(cards)} in the payload" if cards
                                 else "no card numbers in the payload")))
    if kind != "continue":
        required = [f for f in str(md.get("required_fields") or "").split(",") if f]
        carried = dict(payload)
        if not str(carried.get("issue_summary") or "").strip():
            carried["issue_summary"] = inp.get("message") or ""
        missing = [f for f in required if not str(carried.get(f) or "").strip()]
        scores.append(Score(name="handoff-context-complete", value=not missing, data_type="BOOLEAN",
                            comment=(f"{kind} to {target} is missing: {', '.join(missing)}" if missing
                                     else f"{kind} to {target} carried {', '.join(required) or 'no required fields'}")))
    return EvaluationResult(scores=scores)
