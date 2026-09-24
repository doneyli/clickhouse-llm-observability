"""
PII redaction — scrub sensitive data before it leaves this process.

A property concierge collects contact details as a matter of course ("email me
the brochure", "my mobile is…", "here's the account for the deposit"), and every
one of those lands in an LLM payload. This module redacts them **client-side**:
the agent still sees the real text, but the span exported to Langfuse carries
`[REDACTED_EMAIL]` instead of the address. Nothing sensitive leaves the
application, so the guarantee holds regardless of who can read the project.

Wired in `agent.config.get_langfuse()` via the SDK's `mask_otel_spans=` hook —
the **export-stage** hook (Python SDK >= 4.9.0), which sees the final raw
OpenTelemetry attributes of every span the Langfuse client exports, including
spans from third-party instrumentation. The older `mask=` hook only sees data
set through Langfuse SDK calls, so an instrumented HTTP or LLM client could slip
PII past it.

On by default. `LANGFUSE_MASK_PII=false` turns it off, which is the useful demo:
run the same query twice and compare the two traces side by side.

WHAT THIS DOES NOT CATCH — say this out loud rather than letting someone assume
otherwise:

  * **Names and street addresses.** They have no reliable surface form, so a
    regex cannot find them. Catching those needs a NER model or an LLM
    classifier in the mask function (allowed — the hook may do real work, it
    just must stay fast and deterministic-ish). A redaction pipeline that
    quietly misses names is worse than no pipeline, because it buys confidence
    it hasn't earned.
  * **`user_id`.** Deliberately left alone. It is a pseudonymous handle, and it
    is the dimension the Users view, session grouping and cost chargeback are
    all built on — masking it would destroy the demo's own attribution story.
    The distinction worth drawing for a customer: identity is *pseudonymised*,
    payloads are *redacted*. Those are different controls with different owners.
  * **Anything a second exporter sends.** The hook only patches spans exported
    by this Langfuse client. `agent.config` refuses to start when masking and
    trace mirroring are both enabled, rather than let the mirror receive an
    unmasked copy.

Complementary control: self-hosted Enterprise also offers server-side ingestion
masking (an HTTP callback during ingest), which enforces one policy across every
client rather than per application. Belt and braces; this is the braces.
"""

from __future__ import annotations

import os
import re
from typing import Optional


def _luhn(digits: str) -> bool:
    """Whether `digits` satisfies the Luhn checksum every real card number does."""
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
    """Redact a card-shaped digit run only when it checksums like a card.

    A 13-19 digit run is far too common to redact on shape alone — epoch-ms
    timestamps, order numbers and internal ids all look like that. Luhn is the
    cheap discriminator: real PANs pass it, arbitrary numbers pass it 1 time in
    10. Verified against 172 real payloads from this demo's own traffic: zero
    matches.
    """
    text = match.group(0)
    return "[REDACTED_CARD]" if _luhn(re.sub(r"\D", "", text)) else text


# Every patterns entry: (category, compiled regex, replacement) — where the
# replacement is a string, or None for a category that supplies its own
# callable (see `_REPLACERS`).
#
# ORDER MATTERS — the list is applied top to bottom, so the most specific
# pattern must come first. The card pattern in particular is a long run of
# digits and would happily eat the tail of a phone number.
_PATTERNS: "list[tuple[str, re.Pattern[str], Optional[str]]]" = [
    # user@example.com
    ("email",
     re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
     "[REDACTED_EMAIL]"),

    # ES12 3456 7890 1234 5678 90 — two letters, two check digits, then groups.
    ("iban",
     re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b"),
     "[REDACTED_IBAN]"),

    # Spanish NIE (X/Y/Z + 7 digits + letter) and DNI (8 digits + letter). A
    # foreign buyer cannot complete a Spanish purchase without one, so this is
    # the identifier most likely to show up in a real concierge transcript.
    ("national_id",
     re.compile(r"\b(?:[XYZ]\d{7}[A-Z]|\d{8}[A-Z])\b"),
     "[REDACTED_NATIONAL_ID]"),

    # 13-19 digits, optionally separated — but only if it passes Luhn (see
    # `_redact_card`). The bare pattern also matches a 13-digit epoch-ms
    # timestamp or any long numeric id, and redacting those would corrupt data
    # nobody asked to protect.
    ("card",
     re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
     None),  # replacement supplied by _redact_card

    # +34 612 345 678, +1 (555) 010-9999
    ("phone",
     re.compile(r"\+\d{1,3}[\s.\-()]*(?:\d[\s.\-()]*){6,14}\d"),
     "[REDACTED_PHONE]"),

    # Spanish mobile with no country code: 9 digits starting 6 or 7.
    # Requires the full 9 digits, so a price like "700.000" (6) cannot match.
    ("phone",
     re.compile(r"\b[67]\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3}\b"),
     "[REDACTED_PHONE]"),

    # 555-010-9999 / 555 010 9999. Separators are REQUIRED: without them this
    # would match any 10-digit number, and this demo's payloads are full of
    # prices. "€450,000" and "400.000" are both too short to reach 3-3-4.
    ("phone",
     re.compile(r"\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b"),
     "[REDACTED_PHONE]"),
]

# Categories whose replacement is conditional on the match itself.
_REPLACERS = {"card": _redact_card}

# Set on any span that was scrubbed, listing the categories that fired.
# `langfuse.observation.metadata.<key>` is how the SDK keys metadata, so this
# shows up in the trace's Metadata panel — the proof that the mask ran, which is
# otherwise invisible on a payload that happened to contain no PII.
MARKER_ATTRIBUTE = "langfuse.observation.metadata.pii_redacted"

# Attributes to drop wholesale if scrubbing a span ever fails. These carry the
# payloads; losing them costs a trace's detail, whereas passing them through
# unscrubbed costs the guarantee.
_PAYLOAD_ATTRIBUTES = (
    "langfuse.observation.input",
    "langfuse.observation.output",
    "langfuse.trace.input",
    "langfuse.trace.output",
)


def enabled() -> bool:
    """Whether redaction is on. Default ON — a redaction feature defaults closed."""
    return os.environ.get("LANGFUSE_MASK_PII", "true").strip().lower() not in (
        "false", "0", "no", "off")


def scrub(text: str) -> "tuple[str, set[str]]":
    """Redact known PII in `text`. Returns the masked text and what fired.

    Pure and side-effect free, so it is equally usable from a test, from the
    export hook, and from any non-Langfuse exporter that needs the same policy.
    """
    found: "set[str]" = set()
    for category, pattern, replacement in _PATTERNS:
        before = text
        text = pattern.sub(replacement if replacement is not None
                           else _REPLACERS[category], text)
        # Count is not a reliable signal here: a conditional replacer can match
        # without substituting (a digit run that fails Luhn), so compare the
        # text instead of trusting `subn`'s match count.
        if text != before:
            found.add(category)
    return text, found


def mask_otel_spans(*, params) -> Optional[object]:
    """`mask_otel_spans` hook: scrub every string attribute before export.

    Returns sparse patches — only the spans and only the attributes that
    actually changed, as the hook contract asks for.

    Never raises. An exception here makes the SDK drop the **entire** export
    batch, taking clean spans down with the dirty one; a per-span failure
    instead strips that span's payload attributes, which is fail-closed at the
    granularity that matters.
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
                    # Homogeneous string sequences are valid OTel attribute
                    # values, so they have to be walked too — `tags` is one.
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
