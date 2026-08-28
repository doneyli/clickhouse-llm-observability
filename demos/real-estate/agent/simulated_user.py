"""
The simulated user — an LLM that plays the BUYER so a whole CONVERSATION can be
evaluated, not just one turn.

Single-turn evals answer "was that reply good?". They cannot answer "did the
agent still honour, on turn 5, the constraint the user stated once on turn 1?" —
which is where multi-turn agents actually fail. So we put a second model in the
user's chair: it gets a written persona + scenario, holds a real conversation
with the concierge, and stops when its goal is met or the persona would give up.

ROLE INVERSION is the whole trick, and the only genuinely subtle part. One
transcript, two opposite points of view:

    who spoke                concierge's view    simulator's view
    ----------------------   -----------------   ------------------------------
    the simulated buyer      role="user"         role="assistant"  <- its own lines
    the concierge            role="assistant"    role="user"       <- what it answers

Get this backwards and the simulator reads its own messages as the agent's: it
starts answering itself, agrees with everything, and every conversation
"passes" — a silent, total failure of the eval. See build_simulator_messages()
for the mapping and for why a leading `user` turn is always required.

Trace shape: each call is its own `simulated-user` generation, a SIBLING of that
turn's `handle-concierge-chat-message` span inside the experiment item's trace.
Naming and nesting it separately is deliberate — the harness's tokens and cost
must never be mistaken for the agent's when someone compares model costs.

Run (driven by the experiment runner, not directly):
    ./.venv/bin/python scripts/run_simulation_experiment.py --yes
"""

import os
import re
from typing import Any, Dict, List

from .config import get_anthropic, get_langfuse, JUDGE_MODEL

# The simulator ends a conversation by emitting this and nothing else. A
# sentinel (rather than "stop when the model sounds satisfied") keeps the
# terminating condition deterministic and machine-readable, which is what makes
# `reached-done` a trustworthy score instead of a guess.
DONE_SENTINEL = "[[DONE]]"

# The simulator is HARNESS machinery, not the system under test. It defaults to
# JUDGE_MODEL (the eval-side model) rather than AGENT_MODEL for two reasons: a
# `--model claude-sonnet-4-6 | gpt-4o` sweep must not change how the *user*
# behaves (otherwise the runs are not comparable), and the model under test
# should not also be the one driving its own test. Override in .env with
# SIMULATED_USER_MODEL. Reading os.environ here is safe: importing .config has
# already load_dotenv'd this folder's .env with override=True.
SIMULATED_USER_MODEL = os.environ.get("SIMULATED_USER_MODEL", JUDGE_MODEL)

# Short cap on purpose. Real buyers type one or two sentences; a simulator
# allowed to write essays produces transcripts no judge can read and hands the
# agent every constraint at once, which is exactly the failure mode the awkward
# personas exist to avoid.
MAX_USER_TOKENS = 300

# Deliberately NOT stored in Langfuse Prompt Management (unlike the agent's own
# prompts in agent/prompts.py). This is test scaffolding: labelling it
# `production` would imply it ships with the app, and silently changing the
# simulator between runs would invalidate every run-over-run comparison built on
# it. It belongs in version control next to the evaluators it feeds.
SIMULATED_USER_SYSTEM = (
    "You are role-playing a HUMAN USER talking to an online real-estate concierge "
    "chatbot. You are the customer, never the assistant.\n\n"
    "=== YOUR PERSONA ===\n{persona}\n\n"
    "=== YOUR SITUATION ===\n{scenario}\n\n"
    "RULES — follow them exactly:\n"
    "1. Write ONLY what the user says. No narration, no stage directions, no labels "
    "like \"User:\", no meta-commentary about the role-play.\n"
    "2. One short message per turn: 1-3 sentences, the way someone actually types "
    "into a chat box.\n"
    "3. Stay in character. Do NOT be more reasonable, more organised, or more "
    "explicit than your persona would be. If your persona is vague, be vague. If it "
    "is blunt, be blunt. Do not helpfully repeat information you have already given "
    "once, and do not volunteer information nobody asked for.\n"
    "4. React to what the assistant actually said. You are having a conversation, "
    "not reading from a script.\n"
    "5. Never invent property listing ids, prices, or catalogue facts. Refer to what "
    "the assistant showed you the way your persona would.\n"
    "6. Never invent a COMPLAINT about the assistant. Do not accuse it of ignoring "
    "you, forgetting something, answering in the wrong language, repeating itself, "
    "or making a mistake unless that actually happened in the messages above and you "
    "can point to it. Being a difficult customer means being difficult about your own "
    "requirements, never about things the assistant did not do. If it handled your "
    "last message correctly, react to what it actually said.\n"
    "7. When your goal is met, OR when your persona would give up in frustration, "
    "reply with exactly {done} and nothing else.\n\n"
    "Reply now with your next message as the user."
)

# The very first `user` turn the simulator ever sees. It is an INSTRUCTION to the
# simulator, not a line of dialogue, and never appears in the transcript.
OPENING_INSTRUCTION = (
    "Open the conversation: send your first message to the property concierge, "
    "in character, as one short message."
)

# Matches the sentinel however the model dresses it up: `[[DONE]]`, "[[ DONE ]]",
# **[[DONE]]**, or [[done]] followed by a chatty sign-off. Searched (not matched)
# anywhere in the reply, so surrounding whitespace, quotes, backticks, markdown
# emphasis and trailing text all still count as "the simulator is finished".
_DONE_RE = re.compile(r"\[\[\s*DONE\s*\]\]", re.IGNORECASE)


def is_done(message: str) -> bool:
    """True if the simulator signalled that the conversation is over."""
    return bool(_DONE_RE.search(message or ""))


def strip_done(message: str) -> str:
    """The message with the sentinel (and its usual decoration) removed.

    Used for logging only. A reply like `[[DONE]] thanks, that's perfect!` is a
    termination signal, not a turn: the concierge never sees it, so it must not
    be appended to the transcript or counted by `turns-to-resolution`.
    """
    text = _DONE_RE.sub("", message or "")
    return text.strip().strip("`*\"' ").strip()


def _text_of(content: Any) -> str:
    """Flatten a message's content to plain text.

    The runner stores plain strings, but a transcript assembled from
    provider-native history carries content BLOCKS (Anthropic) instead. Mirrors
    scoring.prior_ids_from_history so both readers of a transcript agree.
    """
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        ).strip()
    return str(content or "")


def build_simulator_messages(transcript: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Turn a concierge-POV transcript into the simulator's OWN message list.

    Every role is flipped (see the module docstring): the concierge's
    `assistant` lines become the simulator's `user` input, and the simulated
    buyer's own `user` lines become its `assistant` history.

    The leading OPENING_INSTRUCTION is prepended ALWAYS, not just for an empty
    transcript. The Anthropic API requires the first message to be `user`, and
    after inversion the transcript's own first entry — the buyer's opening line
    — is an `assistant` message. Without a `user` turn in front of it, every
    call after the first would be rejected. (The empty-transcript case, where
    the instruction is the only message, is just the degenerate form of the same
    rule.) Alternation then holds for free, because the transcript alternates
    buyer/concierge by construction.
    """
    messages: List[Dict[str, str]] = [{"role": "user", "content": OPENING_INSTRUCTION}]
    for entry in transcript or []:
        role = "assistant" if entry.get("role") == "user" else "user"
        messages.append({"role": role, "content": _text_of(entry.get("content"))})
    return messages


def simulated_user_reply(persona: str, scenario: str,
                         transcript: List[Dict[str, Any]]) -> str:
    """The simulated buyer's next message, or DONE_SENTINEL to end the conversation.

    `transcript` is the conversation so far in CONCIERGE point of view —
    `[{"role": "user"|"assistant", "content": str}, ...]` — i.e. exactly the
    list the runner also hands to `run_turn(history=...)`. One source of truth
    for the conversation; the inversion happens here and nowhere else.
    """
    lf = get_langfuse()
    messages = build_simulator_messages(transcript)
    system = SIMULATED_USER_SYSTEM.format(persona=persona, scenario=scenario,
                                          done=DONE_SENTINEL)

    # Its own generation, named for the harness rather than the agent: in the
    # item's trace this sits beside the agent's turns, so a reader can see what
    # the "user" was told to do, and the simulator's tokens/cost stay separable
    # from the agent's in every cost view.
    with lf.start_as_current_observation(
        as_type="generation", name="simulated-user", model=SIMULATED_USER_MODEL,
        # All metadata is set ONCE, at construction: a later update(metadata=...)
        # would replace this dict rather than merge into it, so splitting the
        # keys across two calls would silently drop the earlier ones.
        # `messages` minus the opening instruction = messages exchanged so far.
        metadata={"role": "harness", "messages_so_far": len(messages) - 1},
    ) as gen:
        gen.update(input=messages)
        resp = get_anthropic().messages.create(
            model=SIMULATED_USER_MODEL, max_tokens=MAX_USER_TOKENS,
            system=system, messages=messages,
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        # Same usage_details shape agent/llm.py emits, so Langfuse prices this
        # generation from its built-in Claude price list exactly as it prices the
        # agent's — the simulator's spend is visible, not hidden.
        gen.update(output=text,
                   usage_details={"input_tokens": resp.usage.input_tokens,
                                  "output_tokens": resp.usage.output_tokens})
    return text
