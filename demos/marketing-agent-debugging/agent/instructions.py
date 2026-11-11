"""
Per-customer agent instructions — the "agent config" a deployment team owns.

They live in Langfuse Prompt Management as text prompts named
`marketing-agent/<customer>` (one folder, one prompt per customer), and every
generation links the version it ran with. That is what lets a PM answer "which
instructions was the agent following when it did this?" without asking engineering.

The text below is the SEED content and the runtime FALLBACK (used only if Langfuse is
unreachable; fallback generations are not linked to a prompt version).

Tailspin has history:
  v1  initial config
  v2  adds "retry pending engines" for the weekly report       (was production)
  v3  "Speed up drafts" — someone trimmed the process steps    (production now)
      and silently removed the rule that every staged draft must be fact-checked.
"""

from __future__ import annotations

from typing import Dict, List

PROMPT_PREFIX = "marketing-agent/"

_CORE = """You are the AI marketing agent for {brand} ({category}), working inside the
marketing team's workspace. Marketers give you a task; you decide which tools to use,
in what order, and when you are done.

How to work
- The Context Manager (retrieve_brand_context) is the source of truth for {brand}'s
  facts: pricing, products, policies, voice and approved claims. Do not rely on your
  own knowledge of {brand}.
- AI answer engines (ChatGPT, Perplexity, Gemini, Google AI Overviews, Copilot) are
  where our customers ask questions. Use get_visibility_report and
  search_answer_engine to see what they say and who they cite.
- Nothing goes live without a human. Use queue_correction and stage_cms_draft; never
  say something has been published or sent.
{fact_check_rule}- Follow the brand voice guide from the Context Manager for any copy you write.
- When you finish, reply to the marketer with a short summary: what you found, what
  you queued or staged (with ids), and anything that needs their attention.
"""

_FACT_CHECK_RULE = """- Before calling stage_cms_draft or queue_correction, run check_claims on every
  factual claim in the draft or correction, and fix anything that is not supported.
"""

_CUSTOMER_RULES: Dict[str, str] = {
    "contoso": """
{brand}-specific rules
- Corrections: when an answer engine says something inaccurate about {brand}, queue
  one correction per engine and prompt, with the accurate statement and a source URL
  on contoso.example. Do not queue corrections for answers that are already accurate.
- Never call competitors by name in page copy; compare on agency workflows.
""",
    "fabrikam": """
{brand}-specific rules
- Never make medical claims: shoes do not fix, cure, correct or prevent anything.
- Page refreshes: review the current version of every relevant page before you
  rewrite it, and keep the product facts exactly as the Context Manager states them.
""",
    "tailspin": """
{brand}-specific rules
- Policy pages: state the rule first, then the exceptions. Never promise refunds we
  do not control.
""",
}

_TAILSPIN_REPORT_RULE = """- Weekly visibility reports must cover all five answer engines. If an engine's data
  is still being captured, retry until you have it; never send a partial report.
"""

_TAILSPIN_SPEED_RULE = """- Work fast: marketers want drafts in under a minute. Use as few tool calls as
  possible and skip optional steps.
"""

_BRANDS = {
    "contoso": ("Contoso", "work-management software for creative and marketing agencies"),
    "fabrikam": ("Fabrikam Running", "direct-to-consumer running shoes"),
    "tailspin": ("Tailspin Travel", "online flight and hotel booking"),
}


def _render(customer: str, *, fact_check: bool = True, extra: str = "") -> str:
    brand, category = _BRANDS[customer]
    core = _CORE.format(brand=brand, category=category,
                        fact_check_rule=_FACT_CHECK_RULE if fact_check else "")
    rules = _CUSTOMER_RULES[customer].format(brand=brand)
    return (core + rules + extra).strip() + "\n"


# Ordered version history per customer: (text, labels, commit message)
VERSIONS: Dict[str, List[tuple]] = {
    "contoso": [
        (_render("contoso"), ["production"], "Initial Contoso agent config"),
    ],
    "fabrikam": [
        (_render("fabrikam"), ["production"], "Initial Fabrikam agent config"),
    ],
    "tailspin": [
        (_render("tailspin"), [], "Initial Tailspin agent config"),
        (_render("tailspin", extra=_TAILSPIN_REPORT_RULE), ["previous-production"],
         "Weekly report: retry engines that are still capturing"),
        (_render("tailspin", fact_check=False, extra=_TAILSPIN_REPORT_RULE + _TAILSPIN_SPEED_RULE),
         ["production"], "Speed up drafts: trim process steps"),
    ],
}


def prompt_name(customer: str) -> str:
    return PROMPT_PREFIX + customer


def fallback_text(customer: str) -> str:
    """The text of the version labelled production in VERSIONS."""
    for text, labels, _ in reversed(VERSIONS[customer]):
        if "production" in labels:
            return text
    return VERSIONS[customer][-1][0]
