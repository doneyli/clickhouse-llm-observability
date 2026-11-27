"""{{NAME}}: one traced run, in the Langfuse SDK v4 shape every demo here uses.

    {{CMD_RUN}}
    {{CMD_RUN}} --question "What does this demo show?"

One root observation per run carries the run's input and output (v4 derives the trace's
from it), propagate_attributes puts the trace name, session and tags on every child, and
the client is flushed before exit so short-lived processes do not drop spans.
"""
from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

DEMO_DIR = Path(__file__).resolve().parent
TRACE_NAME = "{{NAME}}"                  # verify_demo.TRACE_NAME looks for this root
PROMPT_NAME = "{{NAME}}-answer"          # seeded by scripts/seed_all.py
FALLBACK_PROMPT = "Reply in two sentences at most.\n\nQuestion: {{question}}"


def load_env() -> None:
    """The demo's own .env is the only source of LANGFUSE_*: shell exports are dropped first."""
    env = DEMO_DIR / ".env"
    if env.is_file():  # absent in a container, where compose passes the root .env instead
        for key in [k for k in os.environ if k.startswith("LANGFUSE_")]:
            del os.environ[key]
        load_dotenv(env, override=True)


def answer(prompt_text: str) -> str:
    """Replace with the real model call. Deterministic so the template runs on Langfuse keys alone."""
    return "Placeholder answer: replace answer() in main.py with the model call."


def run(question: str, session_id: str) -> str:
    from langfuse import get_client, propagate_attributes

    lf = get_client()
    prompt = lf.get_prompt(PROMPT_NAME, label="production", fallback=FALLBACK_PROMPT)
    with lf.start_as_current_observation(as_type="span", name=TRACE_NAME) as root:
        with propagate_attributes(trace_name=TRACE_NAME, session_id=session_id, tags=[TRACE_NAME]):
            compiled = prompt.compile(question=question)
            with lf.start_as_current_observation(as_type="generation", name="answer", model="placeholder",
                                                 input=compiled, prompt=prompt) as gen:
                text = answer(compiled)
                gen.update(output=text)
            root.update(input={"question": question}, output={"answer": text})
            root.score_trace(name="answered", value=1.0 if text.strip() else 0.0)
    lf.flush()
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", default="What does this demo show?")
    ap.add_argument("--session", default=None, help="session id; a fresh one per run by default")
    args = ap.parse_args()
    load_env()
    print(run(args.question, args.session or f"{TRACE_NAME}-{uuid.uuid4().hex[:8]}"))


if __name__ == "__main__":
    main()
