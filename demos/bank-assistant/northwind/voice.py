"""
Northwind Bank voice channel — a phone call through the SAME assistant (multi-modal OBS).

    caller audio ─► speech-to-text ─► northwind-assistant (agent.run_turn) ─► text-to-speech ─► reply audio
      (media)        generation        guardrails · LLM · RAG · MCP tools      generation         (media)

What lands in Langfuse for ONE caller utterance (one trace):
  * root `agent` observation `northwind-voice-call` — input = the caller's audio as
    Langfuse media (playable in the trace view), output = transcript, answer and the
    reply audio; metadata = models, voice, per-stage latency
  * `speech-to-text` generation — model, input audio, output transcript, token usage, cost
  * the whole `northwind-assistant` subtree from agent.py (guardrails, LLM generations,
    RAG retriever, MCP tool spans, deterministic scores) NESTED under the voice root:
    run_turn opens its observation inside our OTel context, so it joins this trace
  * `text-to-speech` generation — the speakable text in, the reply audio out, usage,
    cost and time-to-first-audio (completion_start_time)
  * session (the call), user (the customer), tags channel:voice / team:contact-center

Why media and not base64 in the payload: LangfuseMedia uploads the bytes once to
Langfuse's object storage and leaves a `@@@langfuseMedia:...@@@` token in the
observation. Traces stay small, the audio is deduplicated by content hash, and
managed LLM-as-a-judge evaluators can resolve the token and LISTEN to the call.

The APM copy (config.py) gets the same spans with timings but no payloads — the
caller's voice never reaches the APM.
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import openai
from langfuse import propagate_attributes
from langfuse.media import LangfuseMedia

from northwind import agent, config

TRACE_NAME = "northwind-voice-call"
TAGS = ["northwind-assistant", "channel:voice", "team:contact-center"]

# Preferred model first; the next one is used only if the account cannot call it.
STT_MODELS = ("gpt-4o-mini-transcribe", "whisper-1")
TTS_MODELS = ("gpt-4o-mini-tts", "tts-1")
TTS_VOICE = "alloy"
TTS_INSTRUCTIONS = ("You are a contact-center agent at a retail bank. Speak calmly, warmly and "
                    "clearly, at a measured pace. Sound reassuring, never rushed.")

# Langfuse Cloud ships no default prices for OpenAI's speech models, so cost is
# computed here from OpenAI list prices (USD) and ingested as cost_details.
# Ingested cost always wins over inferred cost. Alternative: define the models
# once per project (Settings → Models) and send usage only.
_PRICES = {
    "gpt-4o-mini-transcribe": {"input_audio": 3.00e-6, "input_text": 1.25e-6, "output": 5.00e-6},
    "whisper-1": {"input_audio_seconds": 0.006 / 60},
    "gpt-4o-mini-tts": {"input": 0.60e-6, "output_audio": 12.00e-6},
    "tts-1": {"input_characters": 15.00e-6},
}

_MIME_BY_SUFFIX = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".mpeg": "audio/mpeg",
                   ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".ogg": "audio/ogg",
                   ".oga": "audio/oga", ".webm": "audio/webm", ".flac": "audio/flac"}

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Could you say it again?"

_client: Optional[openai.AsyncOpenAI] = None


def _openai() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _now_ms() -> float:
    return time.perf_counter() * 1000


def audio_mime(filename: str) -> str:
    """MIME type Langfuse renders as an audio player. Unknown suffix → wav."""
    return _MIME_BY_SUFFIX.get(Path(filename).suffix.lower(), "audio/wav")


def _cost(model: str, usage: dict) -> Optional[dict]:
    prices = _PRICES.get(model)
    if not prices:
        return None
    cost = {k: round(usage.get(k, 0) * p, 8) for k, p in prices.items() if usage.get(k)}
    return {**cost, "total": round(sum(cost.values()), 8)} if cost else None


def _usage_from_transcription(usage) -> dict:
    """OpenAI returns tokens for gpt-4o-*-transcribe and seconds for whisper-1."""
    if usage is None:
        return {}
    if getattr(usage, "type", None) == "duration":
        return {"input_audio_seconds": max(1, round(usage.seconds))}
    details = getattr(usage, "input_token_details", None)
    return {k: v for k, v in {
        "input_audio": getattr(details, "audio_tokens", None),
        "input_text": getattr(details, "text_tokens", None),
        "output": getattr(usage, "output_tokens", None),
    }.items() if v}


def speakable(answer: str) -> str:
    """The text the caller will HEAR: no citations, links or markdown.

    The written answer keeps "[KB-202](https://…)"; read aloud it is noise (or a
    URL spelled out). Logging the speakable text as the TTS input makes this
    rewrite visible in the trace, next to the written answer on the root.
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", answer)       # [label](url) → label
    text = re.sub(r"https?://\S+", "", text)                       # bare URLs
    text = re.sub(r"\s*[—–,-]?\s*\bKB-\d{3}\b", "", text)          # citation ids
    text = re.sub(r"\(\s*[)\]]|\[\s*\]", "", text)                 # brackets left empty
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)  # horizontal rules
    text = re.sub(r"[*_`#>|]+", "", text)                          # markdown emphasis, tables
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿️]", "", text)  # emoji
    text = re.sub(r"^\s*(?:[-•]|\d+\.)\s+", "", text, flags=re.MULTILINE)  # list markers
    text = re.sub(r"\s+([,.;:)])", r"\1", re.sub(r"\s+", " ", text))
    return text.strip()[:4000]  # the TTS input limit is 4096 characters


async def _speech_to_text(langfuse, audio: bytes, filename: str, media: LangfuseMedia) -> str:
    """Transcribe the caller. One generation, whichever model actually served it."""
    with langfuse.start_as_current_observation(
            as_type="generation", name="speech-to-text", input={"audio": media},
            metadata={"filename": filename, "audio_bytes": len(audio)}) as gen:
        failed: list[str] = []
        for model in STT_MODELS:
            try:
                res = await _openai().audio.transcriptions.create(
                    model=model, file=(filename, audio, audio_mime(filename)))
                break
            except (openai.NotFoundError, openai.PermissionDeniedError, openai.BadRequestError) as exc:
                failed.append(f"{model}: {type(exc).__name__}")
        else:
            gen.update(level="ERROR", status_message="; ".join(failed))
            raise RuntimeError(f"speech-to-text failed: {failed}")
        transcript = (res.text or "").strip()
        usage = _usage_from_transcription(getattr(res, "usage", None))
        gen.update(model=model, output=transcript, usage_details=usage or None,
                   cost_details=_cost(model, usage),
                   level="WARNING" if failed or not transcript else "DEFAULT",
                   status_message=(f"fell back after {failed}" if failed else
                                   None if transcript else "empty transcript"))
        return transcript


async def _tts_stream(model: str, text: str) -> tuple[bytes, dict, Optional[datetime]]:
    """gpt-4o-mini-tts over SSE: returns audio, real token usage and first-audio time."""
    chunks: list[bytes] = []
    usage: dict = {}
    first: Optional[datetime] = None
    async with _openai().audio.speech.with_streaming_response.create(
            model=model, voice=TTS_VOICE, input=text, instructions=TTS_INSTRUCTIONS,
            response_format="mp3", stream_format="sse") as resp:
        async for line in resp.iter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            event = json.loads(line[5:])
            if event.get("type") == "speech.audio.delta":
                first = first or datetime.now(timezone.utc)
                chunks.append(base64.b64decode(event["audio"]))
            elif event.get("type") == "speech.audio.done":
                u = event.get("usage") or {}
                usage = {k: v for k, v in {"input": u.get("input_tokens"),
                                           "output_audio": u.get("output_tokens")}.items() if v}
    return b"".join(chunks), usage, first


async def _text_to_speech(langfuse, text: str) -> tuple[bytes, str]:
    """Synthesize the reply. Reply audio goes on the generation OUTPUT as media."""
    with langfuse.start_as_current_observation(
            as_type="generation", name="text-to-speech", input=text,
            model_parameters={"voice": TTS_VOICE, "response_format": "mp3"}) as gen:
        failed: list[str] = []
        for model in TTS_MODELS:
            try:
                if model == "gpt-4o-mini-tts":
                    audio, usage, first = await _tts_stream(model, text)
                else:  # tts-1 has no SSE usage; it is billed per input character
                    res = await _openai().audio.speech.create(
                        model=model, voice=TTS_VOICE, input=text, response_format="mp3")
                    audio, usage, first = res.content, {"input_characters": len(text)}, None
                break
            except (openai.NotFoundError, openai.PermissionDeniedError, openai.BadRequestError) as exc:
                failed.append(f"{model}: {type(exc).__name__}")
        else:
            gen.update(level="ERROR", status_message="; ".join(failed))
            raise RuntimeError(f"text-to-speech failed: {failed}")
        mime = "audio/mpeg"
        gen.update(model=model, output={"audio": LangfuseMedia(content_bytes=audio, content_type=mime)},
                   usage_details=usage or None, cost_details=_cost(model, usage),
                   completion_start_time=first, metadata={"audio_bytes": len(audio)},
                   level="WARNING" if failed else "DEFAULT",
                   status_message=f"fell back after {failed}" if failed else None)
        return audio, mime


async def run_voice_turn(audio: bytes, filename: str = "call.wav", *, customer_id: str = "C-1001",
                         session_id: Optional[str] = None, history: Optional[list] = None) -> dict:
    """One caller utterance = one trace: audio in → transcript → agent → audio out."""
    langfuse = config.get_langfuse()
    session_id = session_id or f"call-{uuid.uuid4().hex[:12]}"  # a phone call is a session
    caller_audio = LangfuseMedia(content_bytes=audio, content_type=audio_mime(filename))
    timings: dict = {}

    with propagate_attributes(session_id=session_id, user_id=customer_id, tags=TAGS,
                              trace_name=TRACE_NAME, metadata={"channel": "voice"},
                              version=config.RELEASE):
        with langfuse.start_as_current_observation(
                as_type="agent", name=TRACE_NAME, input={"audio": caller_audio},
                metadata={"filename": filename, "audio_bytes": len(audio)}) as root:
            trace_id = root.trace_id

            t = _now_ms()
            transcript = await _speech_to_text(langfuse, audio, filename, caller_audio)
            timings["stt_ms"] = round(_now_ms() - t)

            t = _now_ms()
            if transcript:
                # Same agent as the web/app channels. Its root observation becomes a
                # CHILD of this voice root (shared OTel context) — one trace end to end.
                # trace_name is passed so the inner propagate_attributes keeps our name.
                turn = await agent.run_turn(
                    transcript, customer_id=customer_id, session_id=session_id, history=history,
                    channel="voice", trace_name=TRACE_NAME,
                    extra_metadata={"input_modality": "audio", "stt_source": filename})
            else:
                turn = {"answer": NOT_UNDERSTOOD, "trace_id": trace_id, "tools_used": [],
                        "blocked": False, "risks": [], "error": None}
            timings["agent_ms"] = round(_now_ms() - t)

            t = _now_ms()
            spoken = speakable(turn["answer"])
            reply_audio, reply_mime = await _text_to_speech(langfuse, spoken)
            timings["tts_ms"] = round(_now_ms() - t)

            nested = turn["trace_id"] == trace_id
            root.update(
                output={"transcript": transcript, "answer": turn["answer"],
                        "reply_audio": LangfuseMedia(content_bytes=reply_audio, content_type=reply_mime)},
                metadata={**timings, "voice": TTS_VOICE, "agent_trace_nested": nested,
                          "tools_used": ",".join(turn.get("tools_used") or []) or "none",
                          "apm_trace_url": config.apm_url(trace_id)},
                level="ERROR" if turn.get("error") else "DEFAULT",
                status_message=turn.get("error"))

    return {"transcript": transcript, "answer": turn["answer"],
            "reply_audio_b64": base64.b64encode(reply_audio).decode(), "reply_mime": reply_mime,
            "trace_id": trace_id, "trace_url": config.trace_url(trace_id),
            "apm_url": config.apm_url(trace_id), "session_id": session_id,
            "agent_trace_nested": nested, "tools_used": turn.get("tools_used") or [],
            "blocked": turn.get("blocked", False), "risks": turn.get("risks") or [], **timings}
