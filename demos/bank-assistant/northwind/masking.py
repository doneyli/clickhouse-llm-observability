"""
PII redaction — scrub customer data before a span leaves the process (ENT-03).

Customers type card numbers, account numbers, national IDs and one-time codes
into a banking assistant as a matter of course. The agent still sees the real
text; the copy exported to Langfuse carries `[REDACTED_CARD]` instead. The
guarantee therefore holds no matter who can read the project.

Wired in `northwind.config.get_langfuse()` through the SDK's `mask_otel_spans=`
hook (Python SDK >= 4.9). It runs at export time over the final OpenTelemetry
attributes of every span the Langfuse client exports — including spans from
third-party instrumentation such as the LangChain callback handler. The legacy
`mask=` hook only sees data set through Langfuse SDK calls.

Layering in production:
  1. this client-side hook (per application, before data leaves the pod)
  2. server-side ingestion masking (Enterprise: LANGFUSE_INGESTION_MASKING_CALLBACK_URL),
     one policy enforced for every team and every SDK
  3. data retention per project (Enterprise), so what is kept expires

Known limits — say them out loud:
  * names and street addresses have no reliable surface form; regex cannot catch
    them (use an NER model or an LLM classifier inside the hook)
  * `user_id` is deliberately left alone: it is a pseudonymous customer handle,
    and the Users view, cost attribution and session grouping depend on it
"""

from __future__ import annotations

import os
import re
from typing import Optional


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _redact_card(match: "re.Match[str]") -> str:
    """Redact a 13-19 digit run only when it passes Luhn, as every real PAN does.

    Shape alone is not enough: transaction ids and epoch-ms timestamps look the
    same, and redacting those would corrupt data nobody asked to protect.
    """
    text = match.group(0)
    return "[REDACTED_CARD]" if _luhn(re.sub(r"\D", "", text)) else text


def _keep_label(replacement: str):
    """Replacement that keeps the anchoring keyword ("account 12345678" →
    "account [REDACTED_ACCOUNT]"), so the redacted text still reads naturally
    for humans and for LLM judges grading it."""
    return lambda m: f"{m.group(1)}{replacement}"


# ORDER MATTERS — most specific first. The card pattern is a long digit run and
# would otherwise eat account numbers and phone numbers.
_PATTERNS: "list[tuple[str, re.Pattern[str], object]]" = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"), "[REDACTED_EMAIL]"),
    # One-time passcodes and PINs: always anchored on the keyword, never bare digits.
    ("otp", re.compile(r"(?i)(\b(?:otp|one[- ]time (?:code|password|passcode)|verification code|security code|"
                       r"pin|cvv|cvc|c[oó]digo(?: de (?:verificaci[oó]n|seguridad|un solo uso))?|clave(?: din[aá]mica)?)"
                       r"\b[^0-9]{0,20}?)\d{3,8}\b"),
     _keep_label("[REDACTED_SECRET]")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b"),
     "[REDACTED_IBAN]"),
    # US SSN
    ("national_id", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_NATIONAL_ID]"),
    # Any national id that follows its keyword (cédula, DNI, CPF, passport …). The
    # value must CONTAIN DIGITS — "national ID number is 1020304050" must redact the
    # number, not the word "number" (a case-insensitive letters-only match did that).
    ("national_id", re.compile(r"(?i)(\b(?:national id|id number|id no\.?|ssn|passport|c[ée]dula(?: de ciudadan[ií]a)?|"
                               r"documento(?: de identidad)?|dni|cpf|curp|rut|nit|tax id)\b[^0-9]{0,20}?)"
                               r"([A-Z]{0,3}\d[\d.\-]{4,17}[A-Z]?)\b"),
     _keep_label("[REDACTED_NATIONAL_ID]")),
    ("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _redact_card),
    # Account numbers: anchored on the keyword so balances, amounts and internal
    # ids (ACC-1001-01, TX-88101) survive. English and Spanish.
    ("account", re.compile(r"(?i)(\b(?:account|acct|a/c|n[uú]mero de cuenta|cuenta)\b[^0-9]{0,20}?)"
                           r"\d[\d -]{6,18}\d\b"),
     _keep_label("[REDACTED_ACCOUNT]")),
    ("phone", re.compile(r"\+\d{1,3}[\s.\-()]*(?:\d[\s.\-()]*){6,14}\d"), "[REDACTED_PHONE]"),
    ("phone", re.compile(r"\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b"), "[REDACTED_PHONE]"),
]

MARKER_ATTRIBUTE = "langfuse.observation.metadata.pii_redacted"

_PAYLOAD_ATTRIBUTES = (
    "langfuse.observation.input",
    "langfuse.observation.output",
    "langfuse.trace.input",
    "langfuse.trace.output",
)


def enabled() -> bool:
    """Default ON — a redaction control defaults closed."""
    return os.environ.get("LANGFUSE_MASK_PII", "true").strip().lower() not in (
        "false", "0", "no", "off")


def scrub(text: str) -> "tuple[str, set[str]]":
    """Redact known PII in `text`; return (masked_text, categories_that_fired)."""
    found: "set[str]" = set()
    for category, pattern, replacement in _PATTERNS:
        before = text
        text = pattern.sub(replacement, text)
        if text != before:
            found.add(category)
    return text, found


def mask_otel_spans(*, params) -> Optional[object]:
    """Export-stage hook: return sparse patches for spans whose strings changed.

    Never raises — an exception here makes the SDK drop the whole export batch.
    A per-span failure strips that span's payloads instead (fail closed).
    """
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        try:
            replacements: dict = {}
            hits: "set[str]" = set()
            for key, value in span.attributes.items():
                if isinstance(value, str):
                    masked, found = scrub(value)
                    if found:
                        replacements[key] = masked
                        hits |= found
                elif (isinstance(value, (list, tuple)) and value
                      and all(isinstance(item, str) for item in value)):
                    masked_items, changed = [], False
                    for item in value:
                        masked_item, found = scrub(item)
                        masked_items.append(masked_item)
                        hits |= found
                        changed = changed or bool(found)
                    if changed:
                        replacements[key] = masked_items
            if replacements:
                replacements[MARKER_ATTRIBUTE] = ",".join(sorted(hits))
                patches[identifier] = OtelSpanPatch(set_attributes=replacements)
        except Exception as exc:  # noqa: BLE001 — must not break the batch
            patches[identifier] = OtelSpanPatch(
                delete_attributes=_PAYLOAD_ATTRIBUTES,
                set_attributes={MARKER_ATTRIBUTE: f"error: {type(exc).__name__}"},
            )
    return MaskOtelSpansResult(span_patches=patches) if patches else None
