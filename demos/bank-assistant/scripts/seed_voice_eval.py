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

Run:  .venv/bin/python scripts/seed_voice_eval.py                    # openai / gpt-audio
      .venv/bin/python scripts/seed_voice_eval.py --provider google --model gemini-2.5-flash
Then: .venv/bin/python scripts/run_voice_calls.py     (rules score NEW traces only)

KNOWN LIMIT (Langfuse Cloud, 2026-10-08): Langfuse pauses an evaluator whose
model fails its validation (`EVAL_MODEL_CONFIG_INVALID`). OpenAI's audio-input
chat models (gpt-audio, gpt-audio-mini) are paused on create — they reject
`response_format: json_schema` (verified against the OpenAI API; tool calls do
work), and text models like gpt-4o validate but cannot hear audio. This script
therefore enables a rule ONLY when its evaluator comes back `active`, and
otherwise prints why. Use a provider whose model takes audio AND structured
output (e.g. a Google AI Studio / Vertex Gemini connection) to switch it on.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, voice  # noqa: E402

# Default judge: OpenAI's audio-input chat model (Claude models take no audio input).
DEFAULT_PROVIDER, DEFAULT_MODEL = "openai", "gpt-audio"

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=DEFAULT_PROVIDER,
                    help="an LLM-connection provider name in the project (GET /api/public/llm-connections)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="an audio-capable model of that provider")
    args = ap.parse_args()
    judge = {"provider": args.provider, "model": args.model}

    if args.provider == "openai":
        if not config.OPENAI_API_KEY:
            raise SystemExit("OPENAI_API_KEY is required for the OpenAI audio judge")
        # Upsert by provider name. withDefaultModels keeps the standard OpenAI list;
        # the audio models are added explicitly so they are selectable for judges.
        config.api("PUT", "/api/public/llm-connections", {
            "provider": "openai", "adapter": "openai", "secretKey": config.OPENAI_API_KEY,
            "customModels": ["gpt-audio", "gpt-audio-mini"], "withDefaultModels": True})
        print("✓ LLM connection: openai (+ gpt-audio, gpt-audio-mini)")

    evaluators = {e["name"]: e for e in _all("/api/public/v2/evaluators")}
    rules = {r["name"]: r for r in _all("/api/public/v2/evaluation-rules")}
    for j in JUDGES:
        body = {"type": "llm_as_judge", "name": j["name"],
                "description": f"Northwind voice channel — {j['name']} (multi-modal, listens to the call)",
                "prompt": [{"role": "user", "content": j["prompt"]}], "modelConfig": judge,
                "outputDefinition": {"dataType": "NUMERIC", "scoreValueInstructions": j["value"],
                                     "scoreReasoningInstructions": j["reasoning"]}}
        ev = evaluators.get(j["name"])
        if ev is None:
            ev = config.api("POST", "/api/public/v2/evaluators", body)
            print(f"+ evaluator {j['name']}  {judge['provider']}/{judge['model']}")
        else:  # replacing the definition re-validates the model (and un-pauses if it is fixed)
            ev = config.api("PATCH", f"/api/public/v2/evaluators/{ev['id']}",
                            {k: v for k, v in body.items() if k != "name"})
            print(f"~ evaluator {j['name']}  {judge['provider']}/{judge['model']} (reconciled)")
        active = ev.get("status") == "active"
        if not active:
            print(f"  ! evaluator {ev.get('status')}: {ev.get('pausedReason')} — {ev.get('pausedMessage')}")
        rule = {"name": j["name"], "enabled": active, "sampling": 1.0, "filter": ROOT_FILTER,
                "evaluatorAssignments": [{"evaluatorId": ev["id"], "variableMapping": j["mapping"]}]}
        if j["name"] in rules:
            config.api("PATCH", f"/api/public/v2/evaluation-rules/{rules[j['name']]['id']}", rule)
        else:
            config.api("POST", "/api/public/v2/evaluation-rules", rule)
        print(f"{'+' if active else '-'} rule {j['name']} on {voice.TRACE_NAME} root: "
              f"{'ENABLED, sampling=1.0' if active else 'left DISABLED (evaluator not active)'}")


if __name__ == "__main__":
    main()
