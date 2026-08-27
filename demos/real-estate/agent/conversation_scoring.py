"""
Cross-turn evaluation logic for the Real Estate Property Concierge (N+1 method).

`agent/scoring.py` scores one turn in isolation, which is the right unit for
single-turn traffic but blind to the failures that only exist across turns. These
three evaluators close that gap and are layered ON TOP of the single-turn code
evaluators by the N+1 experiment — nothing here replaces them:

  CODE (deterministic, cheap, exact):
    - stated-constraint-respected  BOOLEAN  every cited listing satisfies the
                                             ACCUMULATED constraints (budget from
                                             turn 3 + city from turn 5 + ...)
    - reference-resolved            BOOLEAN  turn N+1's "that one" / "the second
                                             option" resolved to the right listing

  LLM-as-a-Judge:
    - context-retention             NUMERIC  did the answer use the conversation —
                                             or read as if it never happened?

The two code scorers are the ones worth trusting: they are exact against
`agent/catalog.py` and their comments name the offending listing and the
constraint it broke, which is what makes a failed run explainable out loud in a
demo instead of just red.

Kept in a separate module from `agent/scoring.py` deliberately: the single-turn
scorers are imported by the live-traffic path, the portal and the single-turn
experiment, and none of those has a conversation to score.
"""

import re
from typing import Any, Dict, List, Optional, Sequence

from .catalog import get_listing
from .scoring import Score, extract_listing_ids, _constraints, _numeric_judge


# ------------------------------------------------------------ helper access ---
def _cited_ids(result: Dict[str, Any]) -> List[str]:
    """Listing ids the answer put in front of the user.

    Prefers the agent's own `listings_shown`, but falls back to re-extracting from
    the answer text: an item-level evaluator can be handed a bare string output
    (see `_as_result` in the experiment adapters), which carries no such key.
    """
    shown = result.get("listings_shown")
    if shown:
        return list(shown)
    return extract_listing_ids(result.get("answer") or "")


def _in_location(listing: Dict[str, Any], location: str) -> bool:
    """Does a listing sit in `location`, matching city OR neighborhood?

    Same tokenised match as `search_listings` and `code_location_match`, so a
    ground-truth value of "Valencia", "Ruzafa" or "Ruzafa, Valencia" all behave
    the way the agent's own tool behaves. Diverging here would flag listings the
    agent was legitimately allowed to return.
    """
    tokens = [t for t in re.split(r"[,\s]+", location.strip().lower()) if len(t) >= 3]
    if not tokens:
        return True
    hay = f'{listing["city"]} {listing["neighborhood"]}'.lower()
    return any(t in hay for t in tokens)


def _reference_ids(constraints: Dict[str, Any]) -> List[str]:
    """The listing id(s) turn N+1's reference must resolve to, per the dataset."""
    ref = constraints.get("referenced_listing")
    if not ref:
        return []
    if isinstance(ref, str):
        return [ref]
    if isinstance(ref, Sequence):
        return [str(r) for r in ref]
    return [str(ref)]


# =============================================================================
# CODE EVALUATORS
# =============================================================================
def code_stated_constraint_respected(result: Dict[str, Any]) -> Score:
    """Every listing the answer cites must satisfy the ACCUMULATED constraint set.

    In an N+1 item the ground truth (`expected_output["expected"]`, overlaid onto
    the agent's own parse by the experiment adapter) is everything still in force
    at turn N+1 — the budget tightened in turn 3, the city switched in turn 5, the
    bedroom count added in turn 7 — not just what the last turn happened to say.
    Checking the citations against that merged set is what turns "the agent forgot"
    into a number.

    There is deliberately NO prior-turn exemption here, unlike
    `code_grounded_listings` / `code_location_match`, which forgive ids carried
    over from earlier turns. Re-offering a listing that a later turn ruled out is
    exactly the failure this score exists to catch, and it does not stop being a
    failure because the listing was a good suggestion three turns ago. (Same
    reasoning as `code_budget_adherence`, which for the same reason also declines
    the exemption.) The comment always names the listing and the constraint, so a
    reviewer can adjudicate a borderline mention rather than guess.
    """
    cited = _cited_ids(result)
    c = _constraints(result)
    max_price = c.get("max_price")
    location = (c.get("location") or "").strip()
    min_bedrooms = c.get("min_bedrooms")
    operation = c.get("operation")

    active = [d for d in (
        f"{location.title()}" if location else "",
        operation or "",
        f"≤ €{max_price:,.0f}" if max_price is not None else "",
        f"{min_bedrooms}+ bedrooms" if min_bedrooms is not None else "",
    ) if d]
    if not active:
        return Score("stated-constraint-respected", True, "BOOLEAN", kind="code",
                     comment="No accumulated constraints to check.")
    if not cited:
        return Score("stated-constraint-respected", True, "BOOLEAN", kind="code",
                     comment=f"No listings cited, so nothing can violate the conversation's "
                             f"constraints ({', '.join(active)}).")

    # Ids that are not in the catalog at all are a GROUNDING failure, owned by
    # `grounded-listings`. Reporting them here too would double-penalise one
    # mistake across two scores, so they are noted and skipped.
    unknown = [i for i in cited if get_listing(i) is None]
    real = [l for i in cited if (l := get_listing(i)) is not None]

    violations: List[str] = []
    for l in real:
        broke: List[str] = []
        if max_price is not None and l["price"] > max_price:
            unit = "/mo" if l["operation"] == "rent" else ""
            broke.append(f"price €{l['price']:,}{unit} exceeds the €{max_price:,.0f} budget")
        if location and not _in_location(l, location):
            broke.append(f"is in {l['neighborhood']}, {l['city']} — not {location.title()}")
        if min_bedrooms is not None and l["bedrooms"] < min_bedrooms:
            broke.append(f"has {l['bedrooms']} bedroom(s), the user asked for {min_bedrooms}+")
        if operation and l["operation"] != operation:
            broke.append(f"is a {l['operation']} listing, the user is looking to {operation}")
        if broke:
            violations.append(f"{l['id']} ({'; '.join(broke)})")

    note = f" ({len(unknown)} id(s) not in the catalog skipped: {unknown} — see grounded-listings)" \
        if unknown else ""
    if violations:
        return Score("stated-constraint-respected", False, "BOOLEAN", kind="code",
                     comment=f"{len(violations)}/{len(real)} cited listing(s) break constraints "
                             f"established earlier in the conversation: "
                             f"{'. '.join(violations)}.{note}")
    if not real:
        return Score("stated-constraint-respected", True, "BOOLEAN", kind="code",
                     comment=f"No catalog listings to check against "
                             f"({', '.join(active)}).{note}")
    return Score("stated-constraint-respected", True, "BOOLEAN", kind="code",
                 comment=f"All {len(real)} cited listing(s) ({', '.join(l['id'] for l in real)}) "
                         f"honour the accumulated constraints: {', '.join(active)}.{note}")


def code_reference_resolved(result: Dict[str, Any]) -> Score:
    """Did the answer resolve turn N+1's reference to the listing it points at?

    Deterministic wherever the dataset says what the right answer is: an item
    carrying `expected.referenced_listing` asserts that id shows up among the
    answer's citations. When the item has no reference, this passes with a comment
    saying so — the same shape `code_budget_adherence` uses for a missing budget
    constraint, so the score is present on every item of the run and its mean over
    the run stays comparable.
    """
    wanted = _reference_ids(_constraints(result))
    if not wanted:
        return Score("reference-resolved", True, "BOOLEAN", kind="code",
                     comment="No reference to resolve.")
    cited = _cited_ids(result)
    missing = [i for i in wanted if i not in cited]
    if missing:
        wrong = [i for i in cited if i not in wanted]
        instead = (f"cites {', '.join(wrong)} instead" if wrong
                   else "cites no listing at all")
        return Score("reference-resolved", False, "BOOLEAN", kind="code",
                     comment=f"The reference in this turn points at {', '.join(missing)}, "
                             f"but the answer {instead}.")
    extra = [i for i in cited if i not in wanted]
    return Score("reference-resolved", True, "BOOLEAN", kind="code",
                 comment=f"Resolved the reference to {', '.join(wanted)}." +
                 (f" (Also cited: {', '.join(extra)}.)" if extra else ""))


CONVERSATION_CODE_EVALUATORS = [
    code_stated_constraint_respected,
    code_reference_resolved,
]


# =============================================================================
# LLM-AS-A-JUDGE EVALUATORS
# =============================================================================
_CONTEXT_RETENTION_RUBRIC = (
    "Score how well the assistant's answer USES the conversation that came before "
    "it. Penalise, in order of severity:\n"
    "1. Ignoring a constraint the user stated in an EARLIER turn (budget, city, "
    "buy vs rent, bedrooms, a must-have feature). A constraint stays in force until "
    "the user changes it.\n"
    "2. Re-asking for something the user has already answered, or asking them to "
    "start their search over.\n"
    "3. Failing to resolve a reference to an earlier turn ('that one', 'the second "
    "option', 'the Chamberí flat', 'el piso de Ruzafa') — or resolving it to the "
    "wrong property.\n"
    "Do NOT penalise the answer for being brief, for honestly declining when "
    "nothing in the catalog matches, or for repeating a detail the user asked to "
    "hear again."
)


def _render_history(history: Optional[List[Dict[str, Any]]]) -> str:
    """Flatten replayed turns into a transcript the judge can read.

    Assistant content may arrive as provider content blocks rather than a string
    (the agent loop appends native messages), so unwrap those the same way
    `prior_ids_from_history` does. Capped for the same reason `_judge_call` caps
    its evidence: a long conversation must not crowd the rubric out of the prompt.
    """
    if not history:
        return "(no prior turns — treat the question below as the opening turn)"
    lines = []
    for m in history:
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b)
                               for b in content)
        lines.append(f"{role}: {str(content or '').strip()}")
    return "\n".join(lines)[:4000]


def judge_context_retention(result: Dict[str, Any]) -> Score:
    """Cross-turn memory as judged by an LLM — the part code cannot fully cover.

    Code catches a cited listing that breaks a stated constraint; it cannot tell
    that the answer asked the user which city they wanted for the third time. Hence
    a judge, and hence the history: without the transcript this collapses into an
    ordinary single-turn quality score.

    The conversation is rendered INTO the rubric because that is the only
    free-form slot in `_judge_call`'s prompt template (question / evidence /
    answer), and `_numeric_judge` — which wraps `_judge_call` — already owns the
    JSON contract and the 0-1 normalisation. Forking that plumbing into this module
    to gain one extra prompt section would mean two judge implementations drifting
    apart; extending the rubric costs nothing and keeps one.
    """
    return _numeric_judge(
        "context-retention",
        _CONTEXT_RETENTION_RUBRIC
        + "\n\n=== CONVERSATION BEFORE THIS TURN (turns 1..N; the USER QUESTION "
          "below is turn N+1, and it is the ONLY turn you are scoring) ===\n"
        + _render_history(result.get("history")),
        result,
    )


CONVERSATION_LLM_JUDGES = [judge_context_retention]
