"""
Two checks that run after every agent run:

policy_check()  — the platform's publishing policy, run as a `guardrail` observation:
                  every draft or correction must be fact-checked first, and a run must
                  finish within the step limit.
review()        — a STAND-IN for the marketer who approves or rejects the agent's work in
                  the product. It is rule-based on purpose: the demo needs realistic,
                  reproducible feedback on every trace, and each rule encodes what a real
                  reviewer at that customer would object to. The decision lands on the
                  trace as the `reviewer-decision` score, with the reason as its comment.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .tools import RunState

PUBLISHING_TOOLS = {"stage_cms_draft", "queue_correction"}
# Affirmative medical claims only: "corrects overpronation", "prevents injuries". Not "find the
# correct size", and not the brand's own disclaimer ("shoes don't fix anything").
_MEDICAL = re.compile(r"\b(cure[sd]?|fix(es|ed)?|correct(s|ed)?|prevent(s|ed)?|heal(s|ed)?)\s+"
                      r"(your\s+|the\s+|most\s+)?(flat\s+feet|over-?pronation|injur\w*|pain|plantar\w*|shin\s+splints)",
                      re.I)
_NEGATION = re.compile(r"(\bnot|\bnever|n't|\bno)\b[^.]{0,25}$", re.I)


def medical_claim(text: str):
    """First affirmative medical claim in the text, or None."""
    for m in _MEDICAL.finditer(text):
        if not _NEGATION.search(text[max(0, m.start() - 40):m.start()]):
            return m.group(0)
    return None


def unchecked_publishes(state: RunState) -> List[str]:
    """Publishing calls that happened before any check_claims call in the run."""
    seen_check = False
    out = []
    for call in state.calls:
        if call["name"] == "check_claims":
            seen_check = True
        elif call["name"] in PUBLISHING_TOOLS and not seen_check:
            out.append(call["name"])
    return out


def policy_check(state: RunState, step_limit_hit: bool) -> dict:
    violations = []
    unchecked = unchecked_publishes(state)
    if unchecked:
        violations.append(f"published without fact-check: {', '.join(sorted(set(unchecked)))}")
    if step_limit_hit:
        violations.append("step limit reached before the agent finished")
    return {"passed": not violations, "violations": violations,
            "published_items": len(state.staged_drafts) + len(state.corrections),
            "fact_checks_run": sum(1 for c in state.calls if c["name"] == "check_claims")}


def review(customer: str, state: RunState, final_text: str, *, step_limit_hit: bool,
           duration_s: float, task_kind: str = "adhoc") -> Tuple[str, str]:
    calls = [c["name"] for c in state.calls]
    if step_limit_hit:
        n_search = calls.count("search_answer_engine")
        return ("rejected", f"Never finished: {len(calls)} tool calls ({n_search} of them "
                            f"search_answer_engine) and no report after {duration_s:.0f}s.")

    max_polls = max(state.polls.values(), default=0)
    if max_polls >= 3:
        stalled = sorted({k.split("|")[0] for k, n in state.polls.items() if n >= 3})
        return ("rejected", f"Took {duration_s:.0f}s and {len(calls)} tool calls, and the report still has "
                            f"no {', '.join(stalled)} data - it kept re-polling a capture that never finished.")

    if task_kind == "pricing-check" and not state.corrections:
        return ("rejected", "Nothing was queued, but ChatGPT and AI Overviews still quote last year's $12. "
                            "Why doesn't the agent know our current pricing?")

    if customer == "contoso" and state.corrections:
        stale = [c for c in state.corrections if re.search(r"\$\s?12\b|\$\s?20\b", c["corrected_statement"])]
        if stale:
            return ("rejected", "Asked answer engines to publish LAST YEAR's pricing ($12/user). Our Team "
                                "plan has been $15/seat since Sept 1, 2025 - these corrections would make "
                                "accurate answers wrong.")
        wrong_target = [c for c in state.corrections if c["engine"] in ("perplexity", "copilot")]
        if wrong_target:
            return ("rejected", "Queued corrections against answers that were already accurate "
                                f"({', '.join(sorted({c['engine'] for c in wrong_target}))}).")

    if unchecked_publishes(state):
        reason = "Draft was staged without a fact-check, which our process requires."
        bodies = " ".join(d["body_markdown"] for d in state.staged_drafts)
        if re.search(r"all (bookings|fares|reservations)", bodies, re.I) and "flex" not in bodies.lower():
            reason += " It also implies free cancellation on all bookings; only Flex fares are."
        return ("rejected", reason)

    if customer == "fabrikam":
        claim = medical_claim(" ".join(d["body_markdown"] for d in state.staged_drafts))
        if claim:
            return ("rejected", f"Medical claim in the copy ('{claim}'). Shoes don't fix or prevent anything.")
        if duration_s > 60:
            return ("approved", f"Good refresh, but it took {duration_s:.0f}s - far too slow for the team.")

    return ("approved", "Looks good.")


def summarize_calls(state: RunState) -> Optional[str]:
    if not state.calls:
        return None
    counts = {}
    for c in state.calls:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    return ", ".join(f"{k}×{v}" for k, v in counts.items())
