"""
PII redaction — scrub claimant data before a span leaves the process.

Claim files carry Social Security numbers, dates of birth, bank details for the
payout, phone numbers and emails. The agents still see the real values; the copy
exported to Langfuse carries `[REDACTED_SSN]` instead.

Wired in `claims.config.get_langfuse()` through the SDK's `mask_otel_spans=` hook.
It runs at export time over the final OpenTelemetry attributes of every span the
Langfuse client exports, including LangChain callback spans and Strands spans.

Layering in production:
  1. this client-side hook (per application, before data leaves the pod)
  2. server-side ingestion masking (Enterprise), one policy for every team and SDK
  3. data retention per project (Enterprise)

Known limits — say them out loud:
  * free-text health details (cause of death, medical history) and names have no
    reliable surface form; regex cannot catch them (use NER or an LLM classifier in
    the hook, or server-side masking)
  * policy and claim ids (NLA-…, CLM-…) are business identifiers, not PII, and are
    kept: the examiner, the evaluators and the dashboards join on them
  * dates of birth and bank account numbers are anchored on their keyword (within
    20 characters); the same value echoed later without it is not caught
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
    """Redact a 13-19 digit run only when it passes Luhn, as every real PAN does."""
    text = match.group(0)
    return "[REDACTED_CARD]" if _luhn(re.sub(r"\D", "", text)) else text


def _keep_label(replacement: str):
    """Keep the anchoring keyword ("date of birth 1956-03-12" → "date of birth [REDACTED_DOB]")."""
    return lambda m: f"{m.group(1)}{replacement}"


# ORDER MATTERS — most specific first.
_PATTERNS: "list[tuple[str, re.Pattern[str], object]]" = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"), "[REDACTED_EMAIL]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    ("ssn", re.compile(r"(?i)(\b(?:ssn|social security(?: number| no\.?)?|tin|tax id)\b[^0-9]{0,20}?)\d{9}\b"),
     _keep_label("[REDACTED_SSN]")),
    ("dob", re.compile(r"(?i)(\b(?:date[ _]of[ _]birth|dob|born(?: on)?)\b[^0-9]{0,20}?)"
                       r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})"),
     _keep_label("[REDACTED_DOB]")),
    ("bank_account", re.compile(r"(?i)(\b(?:routing(?: number)?|aba|bank account(?: number)?|account (?:no\.?|number)|"
                                r"acct(?: no\.?)?)\b[^0-9]{0,20}?)\d[\d -]{5,18}\d\b"),
     _keep_label("[REDACTED_BANK_ACCOUNT]")),
    ("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _redact_card),
    ("phone", re.compile(r"\+\d{1,3}[\s.\-()]*(?:\d[\s.\-()]*){6,14}\d"), "[REDACTED_PHONE]"),
    ("phone", re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b"), "[REDACTED_PHONE]"),
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
    return os.environ.get("LANGFUSE_MASK_PII", "true").strip().lower() not in ("false", "0", "no", "off")


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
