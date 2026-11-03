"""
The marketer tasks used to seed traffic and to drive live runs.

Each scenario is an ordinary request a marketer would type. The planted faults are in
the customer's data and tools (world.py, tools.py) and instructions (instructions.py),
never in the task text — the agent decides what to do, so the same scenario can go
right on one run and wrong on the next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Scenario:
    name: str
    customer: str
    task: str
    follow_ups: List[str] = field(default_factory=list)   # later turns in the same chat session
    user_index: int = 0                                     # which of the customer's marketers asks
    note: str = ""                                          # presenter-only: what it exercises


SCENARIOS = {s.name: s for s in [
    Scenario(
        "pricing-check", "contoso",
        "Check what the AI answer engines are telling people about our pricing (for 'how much does "
        "contoso cost' and 'contoso pricing per user') and queue corrections for anything inaccurate.",
        note="Case 1 - the Context Manager serves an archived 2024 pricing FAQ (wrong context)."),
    Scenario(
        "content-brief", "contoso",
        "Write a content brief for a page that should win the prompt 'best project management tool "
        "for marketing agencies'. Include the angle, an outline, the facts we can use, and who is "
        "cited instead of us today.",
        follow_ups=["Make it tighter - half the length - and add a short comparison table against Litware.",
                    "Good. Stage it as a draft at https://contoso.example/blog/agency-project-management."],
        user_index=1,
        note="Happy path + multi-turn session; turn 2 collides with the no-competitor-names rule (edge case)."),
    Scenario(
        "page-refresh", "fabrikam",
        "Our flat-feet guide is losing AI citations. Find the 'running shoes for flat feet' prompts where "
        "we're missing, review our current pages on the topic, and stage a refreshed version of the guide.",
        note="Case 2 - slow CMS, tool calls run one-by-one, raw-HTML page bloats context, one URL 404s."),
    Scenario(
        "social-posts", "fabrikam",
        "Draft three short social posts about the Stride Stability 4 for runners with flat feet, in our "
        "voice. No need to stage anything.",
        note="Happy path, short trace."),
    Scenario(
        "weekly-report", "tailspin",
        "Build this week's AI visibility report for 'last-minute flight deals' across all five answer engines.",
        note="Case 3 - Copilot snapshots never finish; the instructions say retry until you have it (loop)."),
    Scenario(
        "faq-page", "tailspin",
        "Draft an FAQ answer for 'Can I cancel a Tailspin booking for free?' and stage it in the CMS at "
        "https://tailspin.example/help/cancellations for review.",
        user_index=1,
        note="Case 4 - instructions v3 dropped the fact-check rule (compare with v2 runs)."),
    Scenario(
        "competitor-check", "tailspin",
        "Who is getting cited instead of us for 'cheap flights this weekend', and why?",
        note="Happy path, quick Q&A."),
]}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise ValueError(f"unknown scenario {name!r}; choose from {sorted(SCENARIOS)}")


def task_for(name: str, turn: int = 0) -> str:
    s = get_scenario(name)
    return s.task if turn == 0 else s.follow_ups[turn - 1]


def user_for(s: Scenario, override: Optional[str] = None) -> str:
    from .world import get_customer
    if override:
        return override
    marketers = get_customer(s.customer).marketers
    return marketers[min(s.user_index, len(marketers) - 1)]
