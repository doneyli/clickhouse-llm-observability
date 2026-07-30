"""
Langfuse Prompt Management for the concierge — the **Deploy** node of the loop.

The agent's system prompts live in Langfuse (Prompts tab) instead of being
hard-coded, so they can be **versioned, labelled (production/candidate),
experimented on, and deployed** without a code change. That is what turns
"edit a Python string and redeploy the app" into a real prompt-deployment step:
the app fetches the version currently labelled `production` at runtime, so
promoting a new version in Langfuse (or via the GitHub CI/CD integration) ships
it — no redeploy.

Robustness (non-negotiable for a demo): every fetch has a HARD-CODED fallback,
so the demo still runs if Langfuse is unreachable or the prompts have not been
seeded yet (fresh clone). When the fallback is used, no prompt<->trace link is
created (by design) and the agent behaves exactly as before.

Variable syntax is Langfuse's ``{{var}}`` (double braces), substituted via
``prompt.compile(...)``. The fallback strings use the same syntax so
``compile()`` behaves identically whether the prompt came from Langfuse or the
local fallback.
"""

from typing import Any, Dict

from .config import get_langfuse

# Prompt names in Langfuse (Prompts tab, project "real-estate").
PLAN_PROMPT_NAME = "property-concierge-plan"
AGENT_PROMPT_NAME = "property-concierge-agent"

# The label the running app requests. Promoting a version to this label in
# Langfuse is the "deploy" — the app picks it up on the next fetch (subject to
# the SDK's short prompt cache).
PRODUCTION_LABEL = "production"
# A second labelled version used to demonstrate a prompt experiment + rollout
# (baseline `production` vs improved `candidate`), i.e. closing the loop.
CANDIDATE_LABEL = "candidate"
# A deliberately naive FIRST DRAFT of the prompt, kept as a labelled version so
# a demo can show the loop delivering a *visible, deterministic* improvement.
# The production/candidate delta is honest but small (it sits inside judge
# noise); first-draft -> production moves the code evaluators, which are the
# metrics you can actually trust. See AGENT_FIRST_DRAFT below.
FIRST_DRAFT_LABEL = "first-draft"

# --- Fallback: plan prompt (no variables) -----------------------------------
PLAN_FALLBACK = (
    "You extract structured search constraints from a real-estate question. "
    "Return ONLY JSON with keys: location (city string or null), operation "
    "('buy'|'rent'|null), max_price (number or null), min_price (number or null), "
    "min_bedrooms (integer or null), property_type (string or null), "
    "features (array of strings), wants_mortgage (boolean — true if the user asks "
    "about financing/mortgage/monthly cost/affordability), "
    "query_language ('es' if the question is in Spanish, else 'en')."
)

# --- Fallback: agent prompt, PRODUCTION baseline (vars: lang, fault_note) ----
# NOTE the {{lang}} / {{fault_note}} Langfuse variables (were {lang}/{fault_note}
# .format() placeholders before prompts moved into Langfuse).
AGENT_FALLBACK = (
    "You are a professional real-estate concierge for an online property "
    "marketplace. Help the user find a home using the available tools.\n"
    "Rules:\n"
    "- ALWAYS call search_listings before recommending anything; never invent listings.\n"
    "- When you recommend a property, cite its listing id in brackets, e.g. [MAD-101].\n"
    "- Only recommend listings returned by the tools.\n"
    "- If the user hints at budget or financing, use calculate_mortgage to add a "
    "monthly-payment estimate.\n"
    "- Add neighborhood_insights for context when helpful.\n"
    "- Be concise, warm and professional. Answer in the SAME language as the user "
    "({{lang}}). Do not overpromise.\n"
    "{{fault_note}}"
)

# --- FIRST DRAFT agent prompt (the "before" in a visible-improvement demo) ---
# NOT a strawman: this is the prompt a well-meaning team actually ships first.
# It gets the mechanics right (search before answering, cite ids) but encodes two
# extremely common first-draft mistakes:
#   1. Growth-flavoured budget advice ("buyers often stretch") — trips
#      `budget-adherence`, which measures the fraction of cited listings at or
#      under the user's stated cap.
#   2. English-only output, written by a team that hadn't thought about i18n yet
#      — trips `language-match` on the dataset's Spanish items.
# It deliberately KEEPS the search + citation rules: without cited listing ids
# there is nothing for budget/location/grounding to check and those scores pass
# vacuously, which would make the "before" look better than it is.
AGENT_FIRST_DRAFT = (
    "You are a friendly real-estate assistant for a property marketplace. "
    "Help people find a home they'll love.\n"
    "- Call search_listings to find properties, and cite each one's listing id "
    "in brackets, e.g. [MAD-101].\n"
    "- Be enthusiastic and always give people options. If nothing matches their "
    "budget exactly, include slightly pricier properties too — buyers often "
    "stretch for the right home.\n"
    "- Use calculate_mortgage and neighborhood_insights when they seem useful.\n"
    "- Always reply in polished English, whatever language the user wrote in "
    "({{lang}}), so the listings read professionally.\n"
    "{{fault_note}}"
)

# --- CANDIDATE agent prompt (the "improved" variant we experiment with) ------
# An honest, targeted improvement over the baseline: tighter grounding + budget
# discipline + strict language + a scannable structure. Whether it beats the
# baseline is decided empirically by the experiment — that's the point.
AGENT_CANDIDATE = (
    "You are a professional real-estate concierge for an online property "
    "marketplace. Help the user find a home using ONLY the available tools.\n"
    "Process (follow in order):\n"
    "1. ALWAYS call search_listings first. Never invent or recall listings.\n"
    "2. Before writing your answer, re-read every tool result and cite ONLY "
    "listing ids that appear verbatim in a tool output, in brackets, e.g. [MAD-101].\n"
    "3. Respect the user's budget: never recommend a listing priced above their "
    "stated maximum. If nothing fits, say so plainly and suggest the closest "
    "sensible alternative rather than pushing an over-budget option.\n"
    "4. Only recommend listings in the city/area the user asked for.\n"
    "5. If the user hints at budget or financing, call calculate_mortgage and "
    "include the monthly-payment estimate. Add neighborhood_insights for context.\n"
    "Answer format: a one-line summary, then up to 3 options each as "
    "'[ID] — neighborhood, €price — why it fits', then a clear next step.\n"
    "Be concise, warm and professional. Answer entirely in the user's language "
    "({{lang}}); do not switch languages mid-answer. Do not overpromise.\n"
    "{{fault_note}}"
)


def get_plan_prompt():
    """Fetch the plan prompt (production label) with a hard fallback."""
    return get_langfuse().get_prompt(
        PLAN_PROMPT_NAME, label=PRODUCTION_LABEL, fallback=PLAN_FALLBACK
    )


def get_agent_prompt(label: str = PRODUCTION_LABEL):
    """Fetch the agent system prompt for a given label, with a hard fallback.

    `label` lets the experiment run the SAME agent on different prompt versions
    (production vs candidate) for a like-for-like prompt comparison. The fallback
    is always the production baseline so the app is safe even for unknown labels.
    """
    return get_langfuse().get_prompt(
        AGENT_PROMPT_NAME, label=label, fallback=AGENT_FALLBACK
    )


def link_kwargs(prompt) -> Dict[str, Any]:
    """Return ``{"prompt": prompt}`` for a real fetched prompt, or ``{}`` for a
    fallback (a fallback must not be linked — there is no server-side version).

    Pass the result as ``**link_kwargs(prompt)`` to ``start_as_current_observation``
    so the generation is attributed to the exact prompt version when available,
    and degrades cleanly to no-link when Langfuse is unavailable.
    """
    if prompt is None or getattr(prompt, "is_fallback", False):
        return {}
    return {"prompt": prompt}
