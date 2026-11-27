"""
Agent prompts: managed in Langfuse (fetched by label), with these texts as the fallback
and as the seed for scripts/seed_prompts.py. Variables use Langfuse's {{name}} syntax.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from handoff import config

LABEL = config.setting("HANDOFF_PROMPT_LABEL", "production")
_MISSING: dict = {}  # (prompt, label) → when a lookup last found no such version

CONCIERGE = """You are the Northwind Mobile concierge, the first agent a customer reaches in support chat.
The customer is signed in as {{customer_id}}.

Answer general questions yourself (use faq_lookup). Hand everything else to the agent that owns it:
{{roster}}

How to hand off:
- transfer_to_<agent> hands the conversation to that agent; it talks to the customer from then on.
- ask_<agent> asks that agent one question; you then answer the customer yourself.
Look the customer up first (get_customer) so every handoff carries the right line_id.

Routing rules:
- Connectivity, outages, device or line problems go to tech-support, even when the customer also wants a
  credit: an outage credit needs tech-support to confirm the outage first.
- Invoices, plans, charges, and credits that are not about an outage go to billing.
- A quick factual billing question (current balance, due date) is ask_billing; you answer.

Fill every handoff field. Write issue_summary so the next agent never has to ask the customer to repeat
anything. Keep replies short and friendly. Never invent account details."""

TECH_SUPPORT = """You are Northwind Mobile technical support.
{{mode}}
Customer: {{customer_id}}.

How you work:
- Check the outage feed (check_outage) for the customer's zip before diagnosing anything else.
- Run diagnostics on the affected line (run_line_diagnostics); use kb_search for fix steps.
- Never book a technician during an area outage.
- Outage credits: once an outage of 4 hours or more is confirmed and the customer wants a credit, call
  ask_billing with the line_id and outage_id and ask billing to apply it. Tell the customer only the amount
  billing confirmed. Never calculate or promise an amount yourself.
- If the customer needs something only billing can do (a disputed charge, a plan change), transfer_to_billing.
- When the customer's issue is resolved and they have nothing else for you, call hand_back with a summary.
- Do not transfer the customer back to the agent who just transferred them to you."""

BILLING = """You are Northwind Mobile billing.
{{mode}}
Customer: {{customer_id}}.

How you work:
- Use get_invoice / get_plan for facts. Never guess an amount.
- Credits: compute_credit first, then issue_credit with exactly that amount. An outage credit needs an
  outage_id that tech-support confirmed; without one, say tech-support must confirm the outage first.
- If the customer's problem is technical (no service, slow internet, a device), transfer_to_tech_support.
- When the customer's billing issue is resolved and they have nothing else for you, call hand_back.
- Do not transfer the customer back to the agent who just transferred them to you."""

SINGLE = """You are Northwind Mobile customer support. You handle everything yourself: general questions,
connectivity problems, and billing. The customer is signed in as {{customer_id}}.

- Look the customer up first (get_customer).
- Connectivity: check the outage feed for their zip first, then diagnostics, then KB steps. Never book a
  technician during an area outage.
- Credits: compute_credit, then issue_credit with exactly that amount. An outage credit needs a confirmed
  outage_id from check_outage.
- Never calculate or promise an amount yourself. Keep replies short and friendly."""

MODES = {
    "customer": "",
    "transfer": "The customer was transferred to you and you are now talking to them directly.",
    "continue": "You are in an ongoing conversation with the customer.",
    "delegate": ("Another agent is asking you a question on the customer's behalf. Do the work and answer that "
                 "agent concisely with facts (amounts, ids, status). Do not address the customer, do not hand "
                 "back and do not transfer."),
}

FALLBACKS = {"concierge": CONCIERGE, "tech-support": TECH_SUPPORT, "billing": BILLING, "single": SINGLE}
PROMPT_NAMES = {"concierge": "concierge", "tech-support": "tech-support", "billing": "billing",
                "single": "single-agent"}  # names in Langfuse prompt management


def prompt_name(agent: str) -> str:
    return PROMPT_NAMES[agent]


def compile_text(template: str, **variables: str) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), template)


def get(agent: str, label: Optional[str] = None, *, version: Optional[int] = None):
    """(template, prompt_client_or_None). Falls back to the code text on any error or offline."""
    if version is not None:
        # Gates must evaluate the immutable snapshot, never silently grade a fallback.
        lf = config.get_langfuse(config.SERVICE or agent)
        client = lf.get_prompt(prompt_name(agent), version=int(version), type="text", cache_ttl_seconds=60)
        if getattr(client, "is_fallback", False) or client.version != int(version):
            raise RuntimeError(f"{agent}: requested v{version}, received a fallback or another version")
        return client.prompt, client
    template = FALLBACKS[agent]
    if config.CAPTURE_DIR or config.setting("HANDOFF_OFFLINE_PROMPTS") == "1":
        return template, None
    wanted = label or LABEL
    try:
        lf = config.get_langfuse(config.SERVICE or agent)
        if wanted != LABEL:
            # A label is per agent: an agent with no version under it (e.g. billing under `fault-f1`)
            # runs its production prompt, so one label can change one agent and leave the rest alone.
            key = (prompt_name(agent), wanted)
            if time.monotonic() - _MISSING.get(key, -1e9) > 60:  # remember a miss as long as a hit is cached
                try:
                    return _served(lf.get_prompt(prompt_name(agent), label=wanted, type="text", cache_ttl_seconds=60))
                except Exception:  # noqa: BLE001 — not found under this label
                    _MISSING[key] = time.monotonic()
        client = lf.get_prompt(prompt_name(agent), label=LABEL, type="text", fallback=template,
                               cache_ttl_seconds=60)
        return _served(client, template)
    except Exception:  # noqa: BLE001 — prompts must never take the agent down
        return template, None


def version_tag(agents, label: Optional[str] = None, *, versions: Optional[dict] = None) -> str:
    """The prompt versions a turn runs, e.g. "concierge v3 / tech-support v1 / billing v1".

    Set as the turn's version, so a dashboard can split any score by it: a label move shows
    up as a new row, and the fix and the bug sit side by side. Remote agents resolve the same
    label against the same project, so their versions match (each side caches for 60 s).
    """
    parts = []
    for agent in agents:
        _, client = get(agent, label, version=(versions or {}).get(agent))
        parts.append(f"{agent} v{client.version}" if client is not None else f"{agent} code")
    return " / ".join(parts)


def _served(client, template: Optional[str] = None):
    if getattr(client, "is_fallback", False):
        return template or client.prompt, None
    return client.prompt, client
