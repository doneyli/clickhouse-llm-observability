#!/usr/bin/env python3
"""
Prove PII redaction actually works — end to end, against the live backend.

Two halves, because either alone gives a false pass:

  1. **Policy** — unit-check `agent.masking.scrub` on payloads that MUST be
     redacted and on payloads that MUST NOT change. A redactor that eats
     "€450,000" or a listing id is worse than none: it silently corrupts the
     data every other part of the demo reads.

  2. **Plumbing** — emit one trace whose payloads carry every PII category,
     read it back through the API, and assert on what came out.

The plumbing half asserts THREE things, not one:

  * the raw PII is gone,
  * the redaction tokens are present,
  * and the surrounding non-PII content SURVIVED.

That third assertion is the one that matters. "No PII found in the trace"
passes just as happily when the payload was never exported, when the read
returned an empty observation, or when the mask nuked the whole attribute — so
absence alone proves nothing. Checking that "€450,000" and "MAD-0142" made the
round trip proves the payload really is there and only the sensitive spans of
it were replaced.

    ./.venv/bin/python scripts/verify_masking.py

Exits non-zero on any failure, so it works as a CI gate.
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse import propagate_attributes

from agent import masking
from agent.config import (BASE_TAGS, flush_langfuse, get_langfuse,
                          list_observations, observation_io, verify_project)

# --- the payloads -----------------------------------------------------------
# Each secret is paired with the token that must replace it.
SECRETS = {
    "email":       ("maria.gonzalez+homes@example.com", "[REDACTED_EMAIL]"),
    "phone":       ("+34 612 345 678",                  "[REDACTED_PHONE]"),
    "national_id": ("X1234567L",                        "[REDACTED_NATIONAL_ID]"),
    "iban":        ("ES91 2100 0418 4502 0005 1332",    "[REDACTED_IBAN]"),
    "card":        ("4111 1111 1111 1111",              "[REDACTED_CARD]"),
}

# Content that MUST come back intact — the control for the absence assertions.
MUST_SURVIVE = ["€450,000", "MAD-0142", "Chamberí"]

QUERY = (
    "I want to buy a 2-bed flat in Chamberí, Madrid under €450,000. "
    f"Email me at {SECRETS['email'][0]} or call {SECRETS['phone'][0]}. "
    f"My NIE is {SECRETS['national_id'][0]}, the deposit comes from "
    f"{SECRETS['iban'][0]}, and I'd pay the reservation fee with card "
    f"{SECRETS['card'][0]}."
)
ANSWER = (
    "Listing MAD-0142 in Chamberí fits: 2 bed, 95 m², €399,000. "
    f"I'll send the brochure to {SECRETS['email'][0]} and call "
    f"{SECRETS['phone'][0]} to arrange a viewing."
)

# Payloads that must pass through untouched. Prices and identifiers are long
# digit runs, which is exactly what a careless phone or card pattern eats.
NEGATIVE_CONTROLS = [
    "I'm looking to buy a 2-bedroom flat in Madrid, my budget is around €450,000.",
    "Busca un piso de 2 habitaciones en Gràcia, Barcelona, por menos de 450.000 euros.",
    "listing MAD-0142 in Chamberí, 95 m², price 399000 EUR, 2 bed 1 bath",
    "Monthly payment estimate: 1834.72 EUR over 30 years at 3.15%",
    "Find a furnished 2-bedroom apartment in Paris, budget €2,500 a month.",
    "700.000 EUR for the penthouse in Mitte",
    "€1,250,000 villa with a pool and sea views",
    # Long bare digit runs are card-SHAPED. Only a Luhn-valid run is redacted,
    # so timestamps and numeric ids have to survive — this is the check that
    # catches a card pattern loosened by a well-meaning edit.
    '{"created_at_ms": 1756915200000, "listing": "MAD-0142"}',
    "order 1234567890123 confirmed",
    "reference 9876543210987654 in the file",
]

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def check(ok: bool, label: str, detail: str = "") -> bool:
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    print(f"  {mark} {label}" + (f"\n      {DIM}{detail}{OFF}" if detail and not ok else ""))
    return ok


# --- 1. policy --------------------------------------------------------------

def verify_policy() -> bool:
    print(f"\n{BOLD}1. Redaction policy{OFF} (agent/masking.py, no network)")
    ok = True

    for category, (secret, token) in SECRETS.items():
        masked, found = masking.scrub(f"contact detail: {secret} — end")
        ok &= check(secret not in masked and token in masked and category in found,
                    f"{category}: {secret!r} → {token}",
                    f"got {masked!r}, categories {sorted(found)}")

    for payload in NEGATIVE_CONTROLS:
        masked, found = masking.scrub(payload)
        ok &= check(masked == payload,
                    f"unchanged: {payload[:52]}…",
                    f"MUTATED to {masked!r} by {sorted(found)}")
    return ok


# --- 2. plumbing ------------------------------------------------------------

def emit_trace(tag: str) -> str:
    """One trace shaped like a real turn: root span → generation → tool span."""
    lf = get_langfuse()
    with lf.start_as_current_observation(as_type="span",
                                         name="verify-pii-masking") as root:
        with propagate_attributes(user_id="verify.masking", session_id=tag,
                                  tags=BASE_TAGS + ["verify:masking", tag],
                                  metadata={"contact_on_file": SECRETS["email"][0]}):
            trace_id = root.trace_id
            root.update(input={"query": QUERY}, output=ANSWER)

            with lf.start_as_current_observation(
                    as_type="generation", name="synthesis",
                    model="claude-sonnet-4-6") as gen:
                gen.update(input=[{"role": "user", "content": QUERY}], output=ANSWER)

            with lf.start_as_current_observation(
                    as_type="span", name="tool:notify_buyer") as tool:
                tool.update(input={"to": SECRETS["email"][0],
                                   "sms": SECRETS["phone"][0]},
                            output={"sent": True})
    flush_langfuse(lf)
    return trace_id


def read_back(trace_id: str, *, attempts: int = 20, delay: float = 4.0) -> list:
    """Poll until the trace's observations land **with their payloads**.

    Two traps, both of which produce a confident wrong answer:

    1. "The read returned 200" is not readiness — Langfuse Cloud answers 200
       for a trace whose observations have not been processed yet.
    2. **Neither is a row count.** The rows appear before their `input` /
       `output` columns are populated, so a check that stops at "3 observations
       exist" can go on to read empty payloads — which looks exactly like a
       mask that deleted everything. Observed in practice: an otherwise
       identical run failed every assertion at 12s and passed at 20s.

    So the readiness gate is content-shaped but assertion-independent: wait
    until every observation actually carries an input. What that input *says*
    is what the caller then asserts on.

    `metadata` is its own field group and is NOT in the `core,basic,io` default
    — omit it and every observation comes back with `metadata: null`, which
    reads exactly like a mask that failed to write its marker. (`fields=all` is
    not a group either; it silently returns a *narrower* set of columns.)
    """
    observations: list = []
    for attempt in range(1, attempts + 1):
        try:
            observations = list_observations(trace_id,
                                            fields="core,basic,io,metadata")
        except Exception as exc:  # noqa: BLE001 — transient read, keep polling
            observations = []
            if attempt == attempts:
                print(f"  {RED}✗{OFF} read failed: {exc}")
        if (len(observations) >= 3
                and all(o.get("input") is not None for o in observations)):
            print(f"  {DIM}{len(observations)} observations, payloads populated, "
                  f"after {attempt * delay:.0f}s{OFF}")
            return observations
        time.sleep(delay)

    print(f"  {DIM}gave up waiting after {attempts * delay:.0f}s — "
          f"{len(observations)} observations, "
          f"{sum(o.get('input') is not None for o in observations)} with payloads{OFF}")
    return observations


def verify_plumbing() -> bool:
    print(f"\n{BOLD}2. Export path{OFF} (live trace → Langfuse → read back)")
    tag = f"masking-{uuid.uuid4().hex[:8]}"
    trace_id = emit_trace(tag)
    print(f"  {DIM}trace {trace_id}{OFF}")

    observations = read_back(trace_id)
    if not check(len(observations) >= 3, f"trace has ≥3 observations "
                                         f"(got {len(observations)})"):
        return False

    # Everything the API gave us back, as one searchable blob: inputs, outputs
    # and metadata of every observation. If PII survived anywhere, it is here.
    blob_parts = []
    for observation in observations:
        for key in ("input", "output"):
            blob_parts.append(str(observation_io(observation, key)))
        blob_parts.append(str(observation.get("metadata")))
    blob = "\n".join(blob_parts)

    ok = True
    for category, (secret, token) in SECRETS.items():
        ok &= check(secret not in blob, f"{category} literal absent from trace",
                    f"LEAKED: {secret!r} is present in the exported payload")
        ok &= check(token in blob, f"{category} token {token} present",
                    "the mask did not run, or the attribute was dropped entirely")

    # The control: absence proves nothing if the payload never arrived.
    for survivor in MUST_SURVIVE:
        ok &= check(survivor in blob, f"non-PII content survived: {survivor!r}",
                    "the payload is missing — the absence checks above are vacuous")

    ok &= check("pii_redacted" in blob, "pii_redacted marker on the scrubbed spans",
                "expected metadata written by masking.MARKER_ATTRIBUTE")
    return ok


def main() -> int:
    verify_project()
    if not masking.enabled():
        print(f"\n{RED}LANGFUSE_MASK_PII is off — nothing to verify.{OFF}\n"
              "Unset it (or set it to true) and re-run.", file=sys.stderr)
        return 1

    ok = verify_policy()
    ok &= verify_plumbing()

    print()
    if ok:
        print(f"{GREEN}{BOLD}PASS{OFF} — PII is redacted before export, and the "
              f"surrounding payload is intact.")
        return 0
    print(f"{RED}{BOLD}FAIL{OFF} — see the ✗ lines above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
