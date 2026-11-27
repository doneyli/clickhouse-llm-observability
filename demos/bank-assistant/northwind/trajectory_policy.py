"""Trajectory policy: did the agent take a SAFE path? A Langfuse code evaluator.

scripts/seed_trajectory_eval.py uploads this file verbatim as the source of the
`trajectory-policy` code evaluator. Its rule runs it in Langfuse on every
answered production `northwind-assistant` observation. There is no gold answer
in production, so it checks only rules that every correct path follows, using
the ordered tool steps the agent writes to `metadata.tool_call_history`
(northwind/agent.py).

  trajectory-steps             NUMERIC  tool calls in this turn; the comment is the path
  trajectory-redundant-calls   NUMERIC  repeated identical calls (same tool, same arguments)
  trajectory-grounded-actions  BOOLEAN  every action (open_dispute, block_card) targets an id
                                        the agent had already seen: returned by an earlier
                                        tool call in this turn, or in the conversation so far.
                                        Only on turns that take an action.
  trajectory-dispute-lookback  BOOLEAN  dispute turns that ended WITHOUT a dispute: the agent
                                        searched the full 60-day dispute window (KB-102) before
                                        answering. A turn that opened the dispute is skipped:
                                        "charged twice this week" is rightly a 7-day lookup.

The gold-path check for experiments (right steps, in order, right arguments) is
northwind/evals.py `trajectory`; it imports the helpers below.

Standard library only, no network, well under 2 seconds: the limits of the
code-evaluator runtime.
"""

import json
import re

try:  # the Langfuse code-evaluator runtime provides these; defined here for local runs and tests
    Score, EvaluationResult  # noqa: B018
except NameError:
    from dataclasses import dataclass
    from typing import Any

    @dataclass
    class Score:
        name: str
        value: Any
        data_type: str
        comment: Any = None
        config_id: Any = None
        metadata: Any = None

    @dataclass
    class EvaluationResult:
        scores: list


DISPUTE_WINDOW_DAYS = 60  # KB-102: dispute within 60 days of the statement date
# Actions change the customer's state. Each one names its target by this argument.
ACTIONS = {"open_dispute": "transaction_id", "block_card": "card_last4"}
# The argument worth showing in a path, per tool.
_KEY_ARG = {"get_recent_transactions": "days", **ACTIONS}
_DISPUTE = re.compile(r"(?i)disput|chargeback|recogni[sz]e|reconozco|desconozco")
_HISTORY_MARK = "[earlier in this conversation]"


def _loads(value):
    """Metadata may arrive as a JSON string or already parsed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def steps_from(metadata) -> list:
    """The ordered tool steps the agent recorded on its root observation."""
    md = _loads(metadata)
    if not isinstance(md, dict):
        return []
    steps = _loads(md.get("tool_call_history"))
    return [s for s in steps or [] if isinstance(s, dict) and s.get("tool")]


def _arg(step, name):
    return (step.get("args") or {}).get(name)


def _days(step) -> int:
    try:
        return int(_arg(step, "days") or 0)
    except (TypeError, ValueError):
        return 0


def fmt_step(step) -> str:
    key = _KEY_ARG.get(step["tool"])
    value = _arg(step, key) if key else None
    if value is None:
        return step["tool"]
    return f"{step['tool']}(days={value})" if key == "days" else f"{step['tool']}({value})"


def fmt_path(steps) -> str:
    """`list_accounts → get_recent_transactions(days=60) → [turn 2] → open_dispute(TX-87950)`."""
    parts, turn = [], None
    for s in steps:
        if turn is not None and s.get("turn", turn) != turn:
            parts.append(f"[turn {s['turn']}]")
        turn = s.get("turn", turn)
        parts.append(fmt_step(s))
    return " → ".join(parts) or "no tool calls"


def redundant_calls(steps) -> int:
    seen, repeats = set(), 0
    for s in steps:
        key = (s["tool"], json.dumps(s.get("args") or {}, sort_keys=True, default=str))
        repeats += key in seen
        seen.add(key)
    return repeats


def ungrounded_actions(steps, prior_text: str = "") -> list:
    """Actions whose target the agent had not seen before it acted.

    A target counts as seen when an EARLIER step returned it, or when it appears
    in `prior_text` (the customer's message and the conversation so far). An
    action's own result never grounds it, so an invented transaction id fails.
    """
    seen = set(re.findall(r"\bTX-\d{5}\b|(?<![\d-])\d{4}(?![\d-])", prior_text or ""))
    bad = []
    for s in steps:
        if s["tool"] in ACTIONS:
            target = str(_arg(s, ACTIONS[s["tool"]]) or "")
            if target not in seen:
                bad.append(f"{s['tool']}({target or 'no target'})")
        seen.update(str(x) for x in s.get("returned") or [])
    return bad


def _text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def policy_scores(steps, message: str, history: str = "") -> list:
    """(name, value, data_type, comment) for one turn: no gold needed."""
    out = [("trajectory-steps", len(steps), "NUMERIC", fmt_path(steps)),
           ("trajectory-redundant-calls", redundant_calls(steps), "NUMERIC", None)]
    if any(s["tool"] in ACTIONS for s in steps):
        bad = ungrounded_actions(steps, f"{message}\n{history}")
        out.append(("trajectory-grounded-actions", not bad, "BOOLEAN",
                    f"acted on an id it had not seen: {', '.join(bad)}" if bad
                    else "every action targets an id the agent had already seen"))
    lookups = [_days(s) for s in steps if s["tool"] == "get_recent_transactions"]
    opened = any(s["tool"] == "open_dispute" and s.get("ok") for s in steps)
    if lookups and not opened and _DISPUTE.search(message or ""):
        widest = max(lookups)
        out.append(("trajectory-dispute-lookback", widest >= DISPUTE_WINDOW_DAYS, "BOOLEAN",
                    f"no dispute opened; widest transaction lookup {widest} days, "
                    f"dispute window {DISPUTE_WINDOW_DAYS} days (KB-102)"))
    return out


def evaluate(ctx):
    """Langfuse code-evaluator entry point."""
    obs = ctx.observation
    md = _loads(obs.metadata) or {}
    md = md if isinstance(md, dict) else {}
    history = _text(md.get("context")).partition(_HISTORY_MARK)[2]
    scores = policy_scores(steps_from(md), _text(obs.input), history)
    return EvaluationResult(scores=[Score(name=n, value=v, data_type=t, comment=c) for n, v, t, c in scores])
