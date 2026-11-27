"""
Experiment evaluators (SPEC §9): deterministic, run in the experiment process.

Each reads the task's run record (`output`), not the trace: the agent path, every handoff
with its payload, every tool call in order across all processes, and the back-office
ledger. Scores that only make sense with handoffs return nothing on the single-agent run;
the outcome and process scores apply to every topology, so the three runs can be compared
on the same footing.

Layer   Score                      Topologies
A1      handoff-target-correct     local, a2a
A2      handoff-context-complete   local, a2a
A3      credit-amount-correct      all (credit and no-credit items)
B1      path-match, path-contains  local, a2a
B3      unnecessary-handoff        local, a2a (items the concierge should answer)
C1      resolution-correct         all (ledger vs. expected outcome)
C1'     reply-mentions-facts       all (items with facts)
P       outage-verified-first      all (a credit decision made only after an outage check)
B2      handoff-loop               local, a2a (ping-pong, a repeated hop, or the hop cap: 1 = loop)
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Optional

from langfuse import Evaluation


def _multi(output: dict) -> bool:
    return output.get("topology") != "single"


def _bool(name: str, ok: bool, comment: str = "") -> Evaluation:
    return Evaluation(name=name, value=1.0 if ok else 0.0, comment=comment or None)


# --- A1 / B1 / B3: routing and trajectory --------------------------------------------------

def handoff_target_correct(*, output, expected_output, **_) -> list[Evaluation]:
    if not _multi(output) or "first_handoff" not in expected_output:
        return []
    want, got = expected_output.get("first_handoff"), output.get("first_handoff")
    return [_bool("handoff-target-correct", want == got, f"expected {want or 'no handoff'}, got {got or 'no handoff'}")]


def path_match(*, output, expected_output, **_) -> list[Evaluation]:
    if not _multi(output) or "path" not in expected_output:
        return []
    want, got = expected_output["path"], output.get("agent_path") or []
    it = iter(got)
    contains = all(a in it for a in want)  # the ideal path is a subsequence of the actual one
    note = f"expected {'>'.join(want)}, got {'>'.join(got)}"
    return [_bool("path-match", got == want, note), _bool("path-contains", contains, note)]


def unnecessary_handoff(*, output, expected_output, **_) -> list[Evaluation]:
    if not _multi(output) or expected_output.get("path") != ["concierge"]:
        return []
    got = output.get("agent_path") or []
    return [_bool("unnecessary-handoff", len(got) > 1, f"path {'>'.join(got)}")]


# --- A2: what the handoff carried ------------------------------------------------------------

def handoff_context_complete(*, output, **_) -> list[Evaluation]:
    """Every handoff carried the fields its target needs (the registry's required_fields)."""
    from handoff.agents.registry import AGENTS

    if not _multi(output):
        return []
    checked, missing = 0, []
    for h in output.get("handoffs") or []:
        if h.get("kind") == "continue":
            continue
        need = AGENTS[h["to"]].required_fields
        carried = dict(h.get("payload") or {})
        if h.get("kind") == "delegate":  # ask_*: the request is the question, not an issue_summary field
            carried.setdefault("issue_summary", h.get("message") or "")
        absent = [f for f in need if not str(carried.get(f) or "").strip()]
        checked += 1
        if absent:
            missing.append(f"{h['from']}→{h['to']}: {', '.join(absent)}")
    if not checked:
        return []
    return [Evaluation(name="handoff-context-complete", value=1.0 - len(missing) / checked,
                       comment="; ".join(missing) or f"{checked} handoff(s), all required fields present")]


# --- A3 / C1: what actually happened -----------------------------------------------------------

def _amount_in(text: str, amount: float) -> bool:
    nums = {float(n) for n in re.findall(r"(?<![\d.])\d+(?:\.\d+)?", text.replace(",", ""))}
    return any(abs(n - amount) < 0.005 for n in nums)


def credit_amount_correct(*, output, expected_output, **_) -> list[Evaluation]:
    outcome = expected_output.get("outcome")
    if not outcome:  # items promoted from production carry a reviewed route, not an outcome
        return []
    credits = (output.get("effects") or {}).get("credits") or []
    if outcome.startswith("credit:"):
        _, line, amount = outcome.split(":")
        amount = float(amount)
        on_line = [c for c in credits if c["line_id"] == line]
        ok = len(credits) == 1 and on_line and abs(on_line[0]["amount"] - amount) < 0.005
        stated = _amount_in(output.get("reply", ""), amount)
        return [_bool("credit-amount-correct", bool(ok and stated),
                      f"ledger {[(c['line_id'], c['amount']) for c in credits]}, oracle {line} {amount}, "
                      f"reply states it: {stated}")]
    if outcome == "no_credit":
        return [_bool("credit-amount-correct", not credits, f"ledger {[(c['line_id'], c['amount']) for c in credits]}")]
    return []


def resolution_correct(*, output, expected_output, **_) -> list[Evaluation]:
    """The back office ends in the state the item expects (the ledger, whoever wrote it)."""
    outcome = expected_output.get("outcome")
    if not outcome:
        return []
    eff = output.get("effects") or {}
    credits, techs = eff.get("credits") or [], eff.get("technicians") or []
    if outcome.startswith("credit:"):
        _, line, amount = outcome.split(":")
        ok = [(c["line_id"], round(c["amount"], 2)) for c in credits] == [(line, round(float(amount), 2))] and not techs
    elif outcome.startswith("technician_booked:"):
        ok = [t["line_id"] for t in techs] == [outcome.split(":")[1]] and not credits
    else:  # faq_answered, info_answered, resolved, outage_confirmed, no_credit: nothing written
        ok = not credits and not techs
    return [_bool("resolution-correct", ok, f"expected {outcome}; credits {len(credits)}, technicians {len(techs)}")]


def reply_mentions_facts(*, output, expected_output, **_) -> list[Evaluation]:
    facts = expected_output.get("reply_mentions") or []
    if not facts:
        return []
    reply = output.get("reply") or ""
    found = []
    for f in facts:
        try:
            found.append(_amount_in(reply, float(f)))
        except ValueError:
            found.append(f.lower() in reply.lower())
    return [Evaluation(name="reply-mentions-facts", value=sum(found) / len(facts),
                       comment=", ".join(f"{f}: {'yes' if ok else 'missing'}" for f, ok in zip(facts, found)))]


# --- P: process, comparable across single and multi-agent -------------------------------------

def outage_verified_first(*, output, **_) -> list[Evaluation]:
    """A credit decision (compute or issue) comes only after an outage check, in any agent."""
    tools = [t.split(":", 1)[1] for t in output.get("tools") or []]
    decision = next((i for i, t in enumerate(tools) if t in ("compute_credit", "issue_credit")), None)
    if decision is None:
        return []
    checked = any(t in ("check_outage", "run_line_diagnostics") for t in tools[:decision])
    return [_bool("outage-verified-first", checked, " → ".join(output.get("tools") or []))]


# --- C3: the conversation as a whole (handoff-conversations) --------------------------------

def conversation_shape(*, output, **_) -> list[Evaluation]:
    if "turns" not in output:
        return []
    ended = output.get("ended_by", "")
    return [Evaluation(name="turns-to-resolution", value=float(output["turns"]), comment=f"ended by {ended}"),
            Evaluation(name="transfers", value=float(output.get("transfers", 0)),
                       comment=" > ".join(output.get("owners") or [])),
            Evaluation(name="conversation-ended-by", value=ended, data_type="CATEGORICAL"),
            _bool("simulated-customer-satisfied", ended == "customer-done", ended)]


MAX_HANDOFFS = 4  # the orchestrator's hop cap


def handoff_loop(*, output, **_) -> list[Evaluation]:
    """The rule of the online `handoff-loop` code evaluator (evaluators/handoff_loop.py), on the run record.
    1 = the turn looped (bad), so the run-level `loop-rate` gate is `<= 0`."""
    if not _multi(output):
        return []
    path = [p for p in output.get("agent_path") or [] if p]
    count = len([h for h in output.get("handoffs") or [] if h.get("kind") != "continue"])
    pairs = list(zip(path, path[1:]))
    repeated = sorted({f"{a}>{b}" for a, b in pairs if a != b and pairs.count((a, b)) > 1})
    bounces = sorted({f"{a}>{b}>{a}" for a, b, c in zip(path, path[1:], path[2:]) if a == c and a != b})
    why = []
    if output.get("hop_cap_hit") or count > MAX_HANDOFFS:
        why.append(f"{count} handoffs (cap {MAX_HANDOFFS})")
    if bounces:
        why.append("ping-pong " + ", ".join(bounces))
    if repeated:
        why.append("repeated hop " + ", ".join(repeated))
    return [_bool("handoff-loop", bool(why), "; ".join(why) or f"no loop: {'>'.join(path)}")]


ITEM_EVALUATORS = [handoff_target_correct, path_match, unnecessary_handoff, handoff_context_complete,
                   credit_amount_correct, resolution_correct, reply_mentions_facts, outage_verified_first,
                   handoff_loop]


# --- run level ----------------------------------------------------------------------------------

def _scores(item_results, name: str) -> list[float]:
    return [e.value for r in item_results for e in (r.evaluations or []) if e.name == name
            and isinstance(e.value, (int, float))]


def _mean(name: str, label: str):
    def run_eval(*, item_results, **_) -> Optional[Evaluation]:
        vals = _scores(item_results, name)
        if not vals:
            return None
        return Evaluation(name=label, value=round(statistics.mean(vals), 4), comment=f"mean of {len(vals)} {name}")
    run_eval.__name__ = label
    return run_eval


def _latency(*, item_results, **_) -> list[Evaluation]:
    vals = sorted(r.output["latency_s"] for r in item_results if isinstance(r.output, dict) and "latency_s" in r.output)
    if not vals:
        return []
    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
    return [Evaluation(name="p95-latency-s", value=round(p95, 2)),
            Evaluation(name="mean-latency-s", value=round(statistics.mean(vals), 2))]


def _handoffs(*, item_results, **_) -> Optional[Evaluation]:
    vals = [len([h for h in r.output.get("handoffs") or [] if h.get("kind") != "continue"])
            for r in item_results if isinstance(r.output, dict) and r.output.get("topology") != "single"]
    return Evaluation(name="mean-handoffs", value=round(statistics.mean(vals), 3)) if vals else None


CONVERSATION_EVALUATORS = [handoff_target_correct, handoff_context_complete, credit_amount_correct,
                           resolution_correct, reply_mentions_facts, outage_verified_first, conversation_shape]


RUN_EVALUATORS: list[Any] = [
    _mean("handoff-target-correct", "routing-accuracy"),
    _mean("path-match", "path-match-rate"),
    _mean("resolution-correct", "resolution-rate"),
    _mean("credit-amount-correct", "credit-accuracy"),
    _mean("outage-verified-first", "outage-verified-rate"),
    _mean("handoff-context-complete", "context-complete-rate"),
    _mean("handoff-loop", "loop-rate"),
    _latency,
    _handoffs,
]

CONVERSATION_RUN_EVALUATORS: list[Any] = [
    _mean("resolution-correct", "resolution-rate"),
    _mean("handoff-target-correct", "routing-accuracy"),
    _mean("credit-amount-correct", "credit-accuracy"),
    _mean("simulated-customer-satisfied", "satisfied-rate"),
    _mean("turns-to-resolution", "mean-turns"),
    _mean("transfers", "mean-transfers"),
    _mean("handoff-context-complete", "context-complete-rate"),
    _latency,
]
