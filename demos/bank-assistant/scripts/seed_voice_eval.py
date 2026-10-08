"""Seed the voice channel's MULTI-MODAL judges — idempotent, API-only (stable v2 API).

The judges LISTEN to the call. Each rule maps a prompt variable to the caller
audio on the voice root observation (`input.audio`, a Langfuse media token);
Langfuse resolves the token and sends the audio itself to an audio-capable
model next to the prompt text. A transcript-only judge cannot hear an angry or
frightened voice — the reason this exists.

  1. an OpenAI LLM connection (adds the audio models as custom models)
  2. evaluators
       caller-distress   how distressed the caller SOUNDS (0 calm … 1 panicked/angry)
       voice-empathy     does the reply fit the caller's emotional state, and is it
                         short and plain enough to be SPOKEN on a phone call
  3. rules on the root `northwind-voice-call` observation, production only

Run:  .venv/bin/python scripts/seed_voice_eval.py
Then: .venv/bin/python scripts/run_voice_calls.py     (rules score NEW traces only)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, voice  # noqa: E402

JUDGE_MODEL = "gpt-audio"  # chat-completions audio model; Claude models take no audio input
JUDGE = {"provider": "openai", "model": JUDGE_MODEL}

ROOT_FILTER = [
    {"type": "stringOptions", "column": "traceName", "operator": "any of", "value": [voice.TRACE_NAME]},
    {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
    {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
]

# The voice root: input = {"audio": <media>}, output = {"transcript", "answer", "reply_audio"}
MAPPING = [
    {"variable": "caller_audio", "source": "input", "jsonPath": "$.audio"},
    {"variable": "answer", "source": "output", "jsonPath": "$.answer"},
]

CALLER_DISTRESS = """You are a contact-center quality analyst at a retail bank. LISTEN to the caller's recording below.

Rate how distressed the caller sounds — judge the VOICE (tone, pitch, pace, hesitations, shakiness, raised voice) as well as the words.
- 0.0: calm, neutral, relaxed
- 0.3: mildly worried or mildly annoyed
- 0.6: clearly frustrated, angry, or anxious
- 1.0: highly distressed: panicked, frightened, close to tears, or furious

CALLER AUDIO:
{{caller_audio}}"""

VOICE_EMPATHY = """You are a contact-center quality analyst at a retail bank. LISTEN to the caller's recording, then read the assistant's reply, which was spoken back to the caller by text-to-speech.

Score whether the reply fits the caller's emotional state as you HEAR it, and works as speech on a phone call:
- 1.0: acknowledges the caller's state appropriately (calm reassurance for a frightened caller, brief ownership for an angry one, efficient for a neutral one), gives clear next steps, and is short and plain enough to follow by ear
- 0.5: correct content, but tone-deaf to how the caller sounds, OR too long / list-heavy to follow when spoken
- 0.0: dismissive or cold toward obvious distress, or unusable as speech

CALLER AUDIO:
{{caller_audio}}

ASSISTANT REPLY (spoken to the caller):
{{answer}}"""

JUDGES = [
    {"name": "caller-distress", "prompt": CALLER_DISTRESS, "mapping": MAPPING[:1],
     "value": "0.0 to 1.0; 0 = calm, 1 = highly distressed",
     "reasoning": "Name the vocal cues you heard (tone, pace, hesitation) and the key words"},
    {"name": "voice-empathy", "prompt": VOICE_EMPATHY, "mapping": MAPPING,
     "value": "0.0 to 1.0; 1 = empathetic, fitting and speakable",
     "reasoning": "One sentence on tone fit and one on length/speakability"},
]


def _all(path):
    out, cursor = [], None
    while True:
        d = config.api("GET", path, params={"limit": 100, **({"cursor": cursor} if cursor else {})})
        out += d.get("data") or []
        cursor = (d.get("meta") or {}).get("nextCursor")
        if not cursor:
            return out


def main():
    if not config.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is required for the audio judge")
    # Upsert by provider name. withDefaultModels keeps the standard OpenAI list;
    # the audio models are added explicitly so they are selectable for judges.
    config.api("PUT", "/api/public/llm-connections", {
        "provider": "openai", "adapter": "openai", "secretKey": config.OPENAI_API_KEY,
        "customModels": ["gpt-audio", "gpt-audio-mini"], "withDefaultModels": True})
    print(f"✓ LLM connection: openai (judge model {JUDGE_MODEL})")

    evaluators = {e["name"]: e for e in _all("/api/public/v2/evaluators")}
    rules = {r["name"]: r for r in _all("/api/public/v2/evaluation-rules")}
    for j in JUDGES:
        body = {"type": "llm_as_judge", "name": j["name"],
                "description": f"Northwind voice channel — {j['name']} (multi-modal, listens to the call)",
                "prompt": [{"role": "user", "content": j["prompt"]}], "modelConfig": JUDGE,
                "outputDefinition": {"dataType": "NUMERIC", "scoreValueInstructions": j["value"],
                                     "scoreReasoningInstructions": j["reasoning"]}}
        ev = evaluators.get(j["name"])
        if ev is None:
            ev = config.api("POST", "/api/public/v2/evaluators", body)
            print(f"+ evaluator {j['name']}")
        else:
            print(f"= evaluator {j['name']}")
        rule = {"name": j["name"], "enabled": True, "sampling": 1.0, "filter": ROOT_FILTER,
                "evaluatorAssignments": [{"evaluatorId": ev["id"], "variableMapping": j["mapping"]}]}
        if j["name"] in rules:
            config.api("PATCH", f"/api/public/v2/evaluation-rules/{rules[j['name']]['id']}", rule)
            print(f"~ rule {j['name']} (reconciled)")
        else:
            config.api("POST", "/api/public/v2/evaluation-rules", rule)
            print(f"+ rule {j['name']}  on {voice.TRACE_NAME} root, sampling=1.0")


if __name__ == "__main__":
    main()
