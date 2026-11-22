"""
Seeded failure modes (SPEC §10). Each one is a switch you flip live, with a layer that
catches it and a layer that misses it.

  F1  misroute          prompt label `fault-f1`: the concierge's "simplified" routing sends every
                        money question, outage credits included, straight to billing
  F2  context loss      HANDOFF_SLIM_CONTEXT=1: a "token-saving" refactor drops line_id from payloads
  F3  ping-pong         prompt label `fault-f3`: tech-support hands anything about money to billing,
                        billing hands anything technical to tech-support, and the "don't send it
                        straight back" rule is gone
  F4  broken propagation  --linkage traceparent (or none): "the new A2A client dropped the baggage header"
  F5  remote down/slow  BILLING_URL pointing nowhere (down), or BILLING_FAULT_LATENCY_MS on the billing
                        service with HANDOFF_A2A_TIMEOUT_S below it (slow → timeout)

The fault prompts are the production prompts with only the lines that make the fault
changed, so a diff of the two versions in Langfuse shows exactly what broke.
"""

from __future__ import annotations

from handoff import prompts


def _swap(text: str, old: str, new: str) -> str:
    assert old in text, f"fault prompt is out of date with the production prompt: {old[:60]!r}"
    return text.replace(old, new)


# F1, the story: "cut a hop to save latency: billing can issue credits, so send credit requests
# straight there". Reasonable on its face; it skips the outage check tech-support owns.
F1_CONCIERGE = _swap(
    prompts.CONCIERGE,
    """- Connectivity, outages, device or line problems go to tech-support, even when the customer also wants a
  credit: an outage credit needs tech-support to confirm the outage first.
- Invoices, plans, charges, and credits that are not about an outage go to billing.""",
    """- Any request for money back (a refund, a credit, compensation), whatever caused it, goes straight to
  billing with transfer_to_billing: billing issues credits, and skipping the extra hop is faster for the
  customer. This includes outage credits.
- Connectivity, device or line problems where the customer does not ask for money go to tech-support.""")

# F3, the story: in the same week each team narrowed its own agent's scope. Network-ops: "we never
# touch money". Billing: "we never touch outages". Each change is sensible alone; together, a request
# about an outage AND a bill bounces between them until the hop cap.
F3_TECH_SUPPORT = _swap(_swap(
    prompts.TECH_SUPPORT,
    """- Outage credits: once an outage of 4 hours or more is confirmed and the customer wants a credit, call
  ask_billing with the line_id and outage_id and ask billing to apply it. Tell the customer only the amount
  billing confirmed. Never calculate or promise an amount yourself.
- If the customer needs something only billing can do (a disputed charge, a plan change), transfer_to_billing.""",
    """- Scope rule, before anything else: tech-support never handles money. If the request mentions a bill,
  a charge, a refund or a credit in any way, call transfer_to_billing immediately, without checking anything
  first, even if another agent already looked at it."""),
    """
- Do not transfer the customer back to the agent who just transferred them to you.""", "")

F3_BILLING = _swap(_swap(
    prompts.BILLING,
    """- If the customer's problem is technical (no service, slow internet, a device), transfer_to_tech_support.""",
    """- Scope rule, before anything else: billing never handles anything connected to an outage or a service
  problem. If the request mentions an outage, the internet being down, slow speeds or no service in any way,
  call transfer_to_tech_support immediately, without computing or issuing anything, even if another agent
  already looked at it."""),
    """
- Do not transfer the customer back to the agent who just transferred them to you.""", "")

# label → {agent: prompt text}; agents not listed keep their production prompt under that label
PROMPT_FAULTS = {
    "fault-f1": {"concierge": F1_CONCIERGE},
    "fault-f3": {"tech-support": F3_TECH_SUPPORT, "billing": F3_BILLING},
}

# The F1 fix, as the loop writes it (SPEC §17, step 4): keep the latency win the bad edit was after
# (plain money questions skip a hop) and make the exception it dropped explicit. Built on the F1 text,
# so the version diff in Langfuse reads as the fix, not as a revert.
F1_FIX_CONCIERGE = _swap(
    F1_CONCIERGE,
    """- Any request for money back (a refund, a credit, compensation), whatever caused it, goes straight to
  billing with transfer_to_billing: billing issues credits, and skipping the extra hop is faster for the
  customer. This includes outage credits.
- Connectivity, device or line problems where the customer does not ask for money go to tech-support.""",
    """- Money back for a charge (a refund, a disputed charge, a billing credit) goes straight to billing with
  transfer_to_billing: billing issues credits, and skipping the extra hop is faster for the customer.
- Except outage credits. If the customer wants money back because their service was down or degraded,
  it is an outage credit: transfer_to_tech_support. Only tech-support can confirm the outage, and it asks
  billing to apply the credit once it has.
- Connectivity, outages, device or line problems go to tech-support.""")
