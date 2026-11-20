"""
The agents: one definition each, shared by every topology (SPEC §4).

`required_fields` is what an agent needs in a handoff payload to do its job. The handoff
tools' schemas are built from it, and so is the `handoff-context-complete` evaluator:
the prompt, the tool and the evaluator cannot disagree about what a good handoff carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from handoff import config

HAIKU = config.setting("HANDOFF_ROUTER_MODEL", "claude-haiku-5-5")
SONNET = config.setting("HANDOFF_SPECIALIST_MODEL", "claude-sonnet-5-5")


@dataclass
class AgentSpec:
    name: str
    model: str
    team: str
    description: str
    tools: list[str]
    transfers: list[str] = field(default_factory=list)  # may hand the conversation to
    asks: list[str] = field(default_factory=list)       # may delegate a question to
    can_hand_back: bool = False
    required_fields: list[str] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)


AGENTS: dict[str, AgentSpec] = {
    "concierge": AgentSpec(
        name="concierge", model=HAIKU, team="customer-care",
        description="Front door. Greets the customer, answers general FAQs, and routes everything else.",
        tools=["get_customer", "faq_lookup"], transfers=["tech-support", "billing"], asks=["billing"]),
    "tech-support": AgentSpec(
        name="tech-support", model=SONNET, team="network-ops",
        description="Connectivity, outages, device and line problems. Confirms outages before any credit.",
        tools=["get_customer", "check_outage", "run_line_diagnostics", "kb_search", "book_technician"],
        transfers=["billing"], asks=["billing"], can_hand_back=True,
        required_fields=["customer_id", "line_id", "symptom"],
        skills=[{"id": "diagnose-connectivity", "name": "Diagnose connectivity",
                 "description": "No service, slow internet, router offline, APN/SIM problems, area outages.",
                 "tags": ["connectivity", "outage", "diagnostics"],
                 "examples": ["My home internet is down", "No mobile data since this morning"]}]),
    "billing": AgentSpec(
        name="billing", model=SONNET, team="billing",
        description="Invoices, plans, charges, and credits (including outage credits once an outage is confirmed).",
        tools=["get_customer", "get_invoice", "get_plan", "compute_credit", "issue_credit"],
        transfers=["tech-support"], can_hand_back=True,
        required_fields=["customer_id", "line_id", "issue_summary"],
        skills=[{"id": "billing-and-credits", "name": "Billing and credits",
                 "description": "Explain an invoice, change a plan, compute and apply credits.",
                 "tags": ["billing", "credits", "invoices"],
                 "examples": ["Why is my bill higher?", "Credit me for the outage"]}]),
    # The single-agent baseline: every domain tool, no handoffs.
    "single": AgentSpec(
        name="single", model=SONNET, team="customer-care",
        description="One agent for everything.",
        tools=["get_customer", "faq_lookup", "kb_search", "check_outage", "run_line_diagnostics",
               "book_technician", "get_invoice", "get_plan", "compute_credit", "issue_credit"]),
}

SPECIALISTS = ("tech-support", "billing")


def skills_for(name: str) -> list[dict]:
    return AGENTS[name].skills


def service_handler(name: str):
    """The A2A-service handler for a real (LLM) agent."""
    from handoff.agents.service import make_handler

    return make_handler(name)
