"""
Shared configuration for the Real Estate Property Concierge demo.

Key-isolation is the #1 landmine: this demo targets a *dedicated* Langfuse
project ("real-estate"). If the surrounding shell has other LANGFUSE_* keys
exported, every trace/dataset/score would silently land in the wrong project.

To prevent that we:
  1. Load this folder's .env explicitly.
  2. HARD-SET os.environ (override, never setdefault) from those values.
  3. Instantiate Langfuse() with the keys explicitly.
  4. verify_project() confirms the keys resolve to the expected project name.
"""

import os
import sys
import base64
import urllib.request
import urllib.error
import json
from pathlib import Path

from dotenv import load_dotenv

# --- Load this folder's .env (overriding any inherited shell values) ---
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)

LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

# HARD override so any get_client()/SDK path uses the real-estate project keys.
os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST

EXPECTED_PROJECT = "real-estate"

AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-4-6")
# The OpenAI model used for the Claude-vs-GPT comparison (agent side).
OPENAI_AGENT_MODEL = os.environ.get("OPENAI_AGENT_MODEL", "gpt-4o")
# Accept either spelling of the OpenAI key.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_AI_API_KEY")

# Tags applied to every trace so the demo's traffic is easy to filter in the UI.
BASE_TAGS = ["real-estate", "property-concierge"]

_langfuse = None
_anthropic = None


def get_langfuse():
    """Return a singleton Langfuse client bound to the real-estate project keys."""
    global _langfuse
    if _langfuse is None:
        from langfuse import Langfuse

        _langfuse = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
    return _langfuse


def get_anthropic():
    """Return a singleton Anthropic client."""
    global _anthropic
    if _anthropic is None:
        import anthropic

        _anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


_openai = None


def get_openai():
    """Return a singleton OpenAI client (used when the agent runs on a GPT model)."""
    global _openai
    if _openai is None:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY / OPEN_AI_API_KEY not set — needed to run the agent "
                "on an OpenAI model. Add it to demos/real-estate/.env."
            )
        import openai

        _openai = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _openai


def _basic_auth() -> str:
    return base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()


def langfuse_api(method: str, path: str, body=None, timeout: int = 20):
    """Call the Langfuse REST API with the project's keys.

    Returns (status_code, parsed_json). HTTP error responses are returned as
    (code, {"error": ...}); connection failures raise urllib.error.URLError.
    Shared by every entrypoint so auth/timeout/error handling live in one place.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{LANGFUSE_HOST}{path}", data=data, method=method,
        headers={"Authorization": f"Basic {_basic_auth()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read(300).decode(errors="replace")}


def verify_project(quiet: bool = False) -> str:
    """
    Confirm the configured keys resolve to EXPECTED_PROJECT.

    Returns the project name. Exits the process if the keys are wrong so we
    never silently pollute another project (the key-shadowing landmine).
    """
    try:
        _, data = langfuse_api("GET", "/api/public/projects", timeout=10)
    except urllib.error.URLError as e:
        print(f"ERROR: cannot reach Langfuse at {LANGFUSE_HOST}: {e}", file=sys.stderr)
        sys.exit(1)

    projects = data.get("data", [])
    names = [p.get("name") for p in projects]
    if EXPECTED_PROJECT not in names:
        print(
            f"ERROR: configured keys resolve to project(s) {names}, "
            f"expected '{EXPECTED_PROJECT}'. Refusing to run so we don't "
            f"pollute the wrong project.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not quiet:
        print(f"✓ Langfuse project verified: {EXPECTED_PROJECT} @ {LANGFUSE_HOST}")
    return EXPECTED_PROJECT
