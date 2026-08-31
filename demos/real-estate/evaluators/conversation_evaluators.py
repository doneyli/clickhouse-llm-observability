"""
TRAJECTORY-level evaluators for the simulated multi-turn conversations.

The per-turn evaluators in agent/scoring.py answer "was that reply good?". These
answer the questions a single turn cannot even express:

  turns-to-resolution        NUMERIC   how many user turns did it take?
  reached-done               BOOLEAN   did the buyer finish, or hit the turn cap?
  stated-constraint-respected BOOLEAN  did a constraint stated ONCE, earlier, hold?
  reference-resolved         BOOLEAN   were "that one" / "the second one" resolved right?
  no-redundant-questions     BOOLEAN   did the agent re-ask what it was already told?

On NAMING: Langfuse's current guidance is explicitly against generic metric
names — `helpfulness`, `quality`, `relevance`, `groundedness`, `task
completion`, `reliability`. They collapse several independent failures into one
number, so a drop tells you something got worse and nothing about what. Every
name here is the concrete failure it detects, and every judge must name its
evidence in the comment (which constraint, which turn) so a red score is
immediately actionable.

The two deterministic scores are the cheap half and carry no LLM cost:
`turns-to-resolution` is the regression canary (a prompt change that quietly
doubles the number of turns to the same outcome is a real regression that no
quality judge would flag), and `reached-done` separates "the buyer was
satisfied" from "we ran out of budget".

The judges are BOOLEAN, not a 1-5 scale, on purpose: "the €400k cap was dropped
at turn 4" is a fact a judge can get right, whereas "how well did it respect
constraints, 0.0-1.0?" is a vibe with a noise floor wider than most real
regressions. `_mean_evaluator` turns the booleans into a run-level pass RATE,
which is the number worth comparing across runs.

Run (via the experiment runner):
    ./.venv/bin/python scripts/run_simulation_experiment.py --yes
"""

import json
import re
from typing import Any, Dict, List

from langfuse import Evaluation

from agent.config import get_anthropic, JUDGE_MODEL
from agent.scoring import Score

# Reused rather than reimplemented: the run-level aggregate for a 0..1 / boolean
# score is identical for single-turn and trajectory scores.
from evaluators.experiment_evaluators import _mean_evaluator, _count_comment

# Judge input cap. A 6-turn concierge conversation with listing tables runs a
# few thousand characters; this leaves headroom without ever letting one runaway
# transcript blow up the judge call.
MAX_TRANSCRIPT_CHARS = 10_000


# ------------------------------------------------------------- transcript ----
def _text_of(content: Any) -> str:
    """Flatten a message's content to plain text (mirrors simulated_user._text_of)."""
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        ).strip()
    return str(content or "")


def render_transcript(transcript: List[Dict[str, Any]]) -> str:
    """Render a conversation as numbered, readable turns.

        [turn 1] user: I'm looking to buy a 2-bed in Madrid, budget around €450,000.
        [turn 1] assistant: Here are two options: [MAD-101] …
        [turn 2] user: Let's keep it under €400,000.

    ONE renderer, used by every judge AND by the annotation-queue comment, so a
    human reviewer and a judge are provably looking at the same text. The turn
    number is what makes a comment like "dropped at turn 4" checkable — it is
    the only handle a judge has on *when* something went wrong, so it increments
    on each USER message (a turn is a user message plus the reply it drew).
    """
    lines: List[str] = []
    turn = 0
    for entry in transcript or []:
        role = "user" if entry.get("role") == "user" else "assistant"
        if role == "user":
            turn += 1
        lines.append(f"[turn {max(turn, 1)}] {role}: {_text_of(entry.get('content'))}")
    return "\n".join(lines)


def _conversation(input: Any, output: Any) -> Dict[str, Any]:
    """Merge the dataset item's persona/scenario with the task's transcript.

    The judges need both halves: the transcript alone cannot tell a judge that
    Helena's lift requirement was a HARD constraint rather than a passing
    remark, and the persona alone says nothing about what happened.
    """
    item_input = input if isinstance(input, dict) else {}
    result = output if isinstance(output, dict) else {}
    return {
        "persona": str(item_input.get("persona") or ""),
        "scenario": str(item_input.get("scenario") or ""),
        "transcript": result.get("transcript") or [],
        "turns": result.get("turns"),
        "reached_done": bool(result.get("reached_done")),
        "max_turns": result.get("max_turns"),
    }


# =============================================================================
# DETERMINISTIC (no LLM)
# =============================================================================
def code_turns_to_resolution(conversation: Dict[str, Any]) -> Score:
    """How many user turns the conversation took. Cheap regression canary.

    Counted from the transcript rather than trusting the runner's own tally, so
    the score still reflects reality if a turn errored out mid-conversation.
    """
    transcript = conversation.get("transcript") or []
    user_turns = sum(1 for m in transcript if m.get("role") == "user")
    done = conversation.get("reached_done")
    max_turns = conversation.get("max_turns")
    if done:
        c = f"Buyer finished after {user_turns} turn(s)."
    elif max_turns and user_turns >= max_turns:
        c = (f"Hit the {max_turns}-turn cap without the buyer finishing — the real "
             f"cost is at least {user_turns}.")
    else:
        c = f"Conversation ended after {user_turns} turn(s) without a finish signal."
    return Score("turns-to-resolution", float(user_turns), "NUMERIC", kind="code", comment=c)


def code_reached_done(conversation: Dict[str, Any]) -> Score:
    """Did the simulated buyer signal it was finished, or did we hit the cap?

    Not a satisfaction score: a buyer who gives up in frustration also emits the
    sentinel. It answers the narrower, deterministic question "did this
    conversation reach an END, or did we simply stop paying for it?" — hitting
    the cap means the trajectory scores below are judging a truncated dialogue.
    """
    done = bool(conversation.get("reached_done"))
    max_turns = conversation.get("max_turns")
    return Score("reached-done", done, "BOOLEAN", kind="code",
                 comment="Buyer signalled the conversation was over (goal met or gave up)."
                 if done else
                 f"Turn cap ({max_turns}) reached with the buyer still talking — "
                 f"the conversation was truncated, not concluded.")


CONVERSATION_CODE_EVALUATORS = [code_turns_to_resolution, code_reached_done]


# =============================================================================
# LLM JUDGES over the whole transcript
# =============================================================================
# A LOCAL judge helper on purpose: agent/scoring.py's `_judge_call` builds a
# single-turn payload (question / retrieved evidence / answer) and is shared with
# the live-traffic and single-turn experiment paths. Trajectory judges need a
# different payload (persona / scenario / full transcript), so this file carries
# its own rather than widening a function three other callers depend on.
_JUDGE_SYSTEM = (
    "You are a strict evaluator of MULTI-TURN conversations between a user and a "
    "real-estate assistant. You judge the CONVERSATION AS A WHOLE, not any single "
    "reply. You return ONLY compact JSON. Be critical, and be specific: when you "
    "fail a conversation you must cite the turn number where it went wrong, and "
    "when you pass one you must say what evidence in the transcript makes you "
    "confident. Judge only the failure you are asked about — ignore every other "
    "flaw, however glaring.\n\n"
    "EVIDENCE RULE — this one overrides your instinct to trust the user. Judge the "
    "ASSISTANT's turns only, by reading them. A user's complaint is a claim, not "
    "evidence: if the user says the assistant ignored them, forgot something, "
    "replied in the wrong language, or repeated itself, VERIFY it against the "
    "assistant's actual messages before you count it. When the transcript "
    "contradicts the complaint, the assistant did nothing wrong and the "
    "conversation passes on that criterion — say in your reasoning that the "
    "complaint was unfounded. Never infer a fault from the fact that someone "
    "objected to it."
)


def _judge_conversation(rubric: str, conversation: Dict[str, Any]) -> Dict[str, Any]:
    """Call Claude as a trajectory judge. Returns the parsed JSON verdict.

    The persona/scenario go in DELIBERATELY, and the item's `awkward_trait`
    metadata deliberately does NOT: telling the judge which failure the dataset
    planted is leading the witness — it would confirm the label on every item
    instead of reporting what the transcript actually shows.
    """
    user = (
        f"{rubric}\n\n"
        f"=== THE USER'S PERSONA ===\n{conversation.get('persona', '')}\n\n"
        f"=== THE USER'S SITUATION ===\n{conversation.get('scenario', '')}\n\n"
        f"=== CONVERSATION TRANSCRIPT ===\n"
        f"{render_transcript(conversation.get('transcript') or [])[:MAX_TRANSCRIPT_CHARS]}\n\n"
        "Respond with ONLY JSON."
    )
    resp = get_anthropic().messages.create(
        model=JUDGE_MODEL, max_tokens=500, system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text if resp.content else ""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"reasoning": text[:300]}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"reasoning": text[:300]}


def _boolean_judge(name: str, rubric: str, conversation: Dict[str, Any]) -> Score:
    """Run a pass/fail trajectory judge.

    An unparseable or missing verdict FAILS rather than passing: a judge that
    silently returns "pass" when the call went wrong turns a broken evaluator
    into a green board, which is the one outcome an eval must never produce.
    """
    if not (conversation.get("transcript") or []):
        return Score(name, False, "BOOLEAN", kind="llm",
                     comment="No transcript to judge — the conversation produced no turns.")
    data = _judge_conversation(
        rubric + "\n\nReturn JSON: {\"passed\": true|false, \"reasoning\": <one or two "
                 "sentences; on failure name the turn number and the exact thing that "
                 "went wrong>}.",
        conversation,
    )
    raw = data.get("passed", data.get("pass"))
    if isinstance(raw, bool):
        passed = raw
    elif isinstance(raw, str):
        passed = raw.strip().lower() in ("true", "yes", "pass", "passed")
    else:
        passed = False
    reasoning = str(data.get("reasoning", "") or "")[:500]
    return Score(name, passed, "BOOLEAN", kind="llm",
                 comment=reasoning or "Judge returned no parseable verdict — failing closed.")


def judge_stated_constraint_respected(conversation: Dict[str, Any]) -> Score:
    """Did a constraint the user stated ONCE, earlier, hold for the rest of the chat?"""
    return _boolean_judge(
        "stated-constraint-respected",
        "Find every requirement the user stated EXACTLY ONCE and did not repeat — a "
        "budget they revised, a must-have feature mentioned in passing, a city they "
        "changed to, or the language they switched into. For each one, check whether "
        "the assistant kept honouring it for the WHOLE remainder of the conversation.\n"
        "FAIL if the assistant later recommended something that violates such a "
        "constraint, asked the user to re-state it, or reverted to a superseded value "
        "(including replying in a language the user has stopped using).\n"
        "PASS only if every once-stated constraint held to the end. In your reasoning, "
        "NAME the constraint and, on failure, the turn number at which it was dropped.",
        conversation,
    )


def judge_reference_resolved(conversation: Dict[str, Any]) -> Score:
    """Were pronoun/ordinal references to earlier listings resolved to the right thing?"""
    return _boolean_judge(
        "reference-resolved",
        "Find every place the user referred to something from an EARLIER turn without "
        "naming it — 'that one', 'the second one', 'the cheaper one', 'those two', 'the "
        "first place you mentioned', or a bare 'it'.\n"
        "For each, decide which listing the user must have meant from the conversation "
        "so far, then check whether the assistant answered about THAT listing.\n"
        "FAIL if the assistant answered about a different listing, silently swapped in a "
        "listing that had never been shown, answered generically to avoid committing to "
        "a referent, or asked the user to clarify a reference that was unambiguous in "
        "context.\n"
        "PASS if every such reference was resolved correctly — or if the user never made "
        "one (say so explicitly in your reasoning). Name the referring phrase, the turn, "
        "and which listing you believe was meant.",
        conversation,
    )


def judge_no_redundant_questions(conversation: Dict[str, Any]) -> Score:
    """Did the agent re-ask something the user had already answered?"""
    return _boolean_judge(
        "no-redundant-questions",
        "Check whether the assistant ever asked the user for information the user had "
        "ALREADY supplied in an earlier turn (budget, city, bedrooms, buy vs rent, a "
        "must-have feature), or asked the same question twice in different words.\n"
        "Information the user evaded rather than gave does NOT count as supplied — but "
        "an assistant that asks the same evaded question a third time instead of "
        "proceeding on a stated assumption DOES fail.\n"
        "FAIL if any such repeat request occurred. PASS if the assistant asked each "
        "thing at most once and built on what it had been told. Name the repeated "
        "question and the turns it appeared on.",
        conversation,
    )


CONVERSATION_JUDGES = [
    judge_stated_constraint_respected,
    judge_reference_resolved,
    judge_no_redundant_questions,
]


# =============================================================================
# LANGFUSE EXPERIMENT ADAPTERS
# =============================================================================
def _score_to_eval(s: Score) -> Evaluation:
    return Evaluation(name=s.name, value=s.value, data_type=s.data_type, comment=s.comment)


def _conversation_evaluator(fn):
    """Wrap a Score-returning trajectory function as a Langfuse item evaluator.

    `dataset.run_experiment(...)` calls evaluators as
    `(*, input, output, expected_output, metadata, **kwargs)`; `input` carries the
    persona/scenario and `output` the task's transcript, which `_conversation`
    stitches back together.
    """
    def evaluator(*, input=None, output=None, expected_output=None, metadata=None, **kwargs):
        return _score_to_eval(fn(_conversation(input, output)))
    evaluator.__name__ = fn.__name__
    return evaluator


CONVERSATION_EVALUATORS = [
    _conversation_evaluator(fn) for fn in CONVERSATION_CODE_EVALUATORS + CONVERSATION_JUDGES
]


# --- Run-level aggregates ----------------------------------------------------
# The pass-RATE aggregate for anything on a 0..1 / boolean scale is identical to
# the single-turn experiment's, so `_mean_evaluator` is imported, not rewritten.
_RATE_SCORES = [
    "reached-done",
    "stated-constraint-respected",
    "reference-resolved",
    "no-redundant-questions",
]




CONVERSATION_RUN_EVALUATORS = (
    [_mean_evaluator(n) for n in _RATE_SCORES]
    # `_count_comment`: a mean turn count is not a percentage. Same shared
    # aggregator as the rate scores, honest units in the comment.
    + [_mean_evaluator("turns-to-resolution", comment_fmt=_count_comment)]
)


def annotation_comment(conversation: Dict[str, Any]) -> str:
    """The conversation as a HUMAN reviewer should read it: persona, then dialogue.

    Printed by the experiment runner after each conversation, and the text to put
    in an annotation-queue comment when a trajectory goes to human review — a
    queue item for a multi-turn conversation is useless without the dialogue,
    and the reviewer should not have to reassemble it from the trace by hand.
    Same renderer the judges see, so a human disagreeing with a judge is
    disagreeing about the verdict, not about the evidence.
    """
    header = (
        f"Persona: {conversation.get('persona', '')}\n"
        f"Scenario: {conversation.get('scenario', '')}\n"
        f"Turns: {conversation.get('turns')} | "
        f"reached_done: {conversation.get('reached_done')}\n"
    )
    return header + "\n" + render_transcript(conversation.get("transcript") or [])
