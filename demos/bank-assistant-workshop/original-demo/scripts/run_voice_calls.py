"""Run the recorded calls (data/voice/*.mp3) through the voice channel.

    .venv/bin/python scripts/run_voice_calls.py                  # all calls (English + Spanish)
    .venv/bin/python scripts/run_voice_calls.py --lang en        # only the 5 English calls
    .venv/bin/python scripts/run_voice_calls.py --lang es        # only the 3 Spanish calls
    .venv/bin/python scripts/run_voice_calls.py --only scam      # filename substring (e.g. es-1)
    .venv/bin/python scripts/run_voice_calls.py --verify         # + read every trace back
    .venv/bin/python scripts/run_voice_calls.py --save-replies   # reply audio → logs/voice-replies/

Each recording is one call (one session) and one trace: caller audio →
speech-to-text → the Northwind agent → text-to-speech → reply audio.

--verify reads each trace back from the Langfuse public API and checks what a
workshop attendee will look for: audio media on the root input and output, the
STT and TTS generations, the agent subtree in the SAME trace, and that the
uploaded audio is retrievable from /api/public/media/{id}. A trace that answers
200 before its observations land is not a pass — observations are polled.
For a call with a known language (manifest `lang`), the language is passed to
speech-to-text as a hint, and --verify also checks that the transcript and the
spoken reply are in that language.
"""
import argparse
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import httpx

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))
from northwind import config, voice  # noqa: E402

SAMPLES = DEMO_DIR / "data" / "voice"
MEDIA_TOKEN = re.compile(r"@@@langfuseMedia:type=([^|]+)\|id=([^|]+)\|source=[^@]+@@@")


def _calls(only: str | None, lang: str | None = None) -> list[dict]:
    manifest = SAMPLES / "manifest.json"
    calls = (json.loads(manifest.read_text()) if manifest.exists()
             else [{"file": p.name, "customer_id": "C-1001", "scenario": p.stem}
                   for p in sorted(SAMPLES.glob("*.mp3"))])
    return [c for c in calls if (not only or only in c["file"])
            and (not lang or c.get("lang", "en") == lang)]


def _observations(trace_id: str, want: int = 6, timeout_s: int = 120) -> list[dict]:
    """Poll the v2 observations API until the trace's spans (and media) have landed."""
    deadline, obs = time.time() + timeout_s, []
    while time.time() < deadline:
        try:
            obs = config.api("GET", "/api/public/v2/observations",
                             params={"traceId": trace_id, "fields": "core,basic,io,time,model,usage", "limit": 100})["data"]
        except RuntimeError:
            obs = []
        names = {o.get("name") for o in obs}
        if len(obs) >= want and {"speech-to-text", "text-to-speech", voice.TRACE_NAME} <= names:
            root = next(o for o in obs if o.get("name") == voice.TRACE_NAME)
            if MEDIA_TOKEN.search(json.dumps(root.get("output"))):  # output lands last
                return obs
        time.sleep(5)
    return obs


def verify(trace_id: str, lang: str | None = None, result: dict | None = None) -> bool:
    obs = _observations(trace_id)
    by_name = {o.get("name"): o for o in obs}
    root, stt, tts = (by_name.get(n) for n in (voice.TRACE_NAME, "speech-to-text", "text-to-speech"))
    agent_root = by_name.get("northwind-assistant")
    checks = {
        "observations in trace": len(obs),
        "root input has caller audio": bool(root and MEDIA_TOKEN.search(json.dumps(root.get("input")))),
        "root output has reply audio": bool(root and MEDIA_TOKEN.search(json.dumps(root.get("output")))),
        "speech-to-text generation": bool(stt and stt.get("type") == "GENERATION"
                                          and stt.get("output")),
        "text-to-speech generation": bool(tts and tts.get("type") == "GENERATION"
                                          and MEDIA_TOKEN.search(json.dumps(tts.get("output")))),
        "agent subtree nested under voice root": bool(
            agent_root and root and agent_root.get("parentObservationId") == root.get("id")),
        "all observations share the trace id": bool(obs) and all(o.get("traceId") == trace_id for o in obs),
    }
    # Every distinct media id on the root (caller audio in, reply audio out) must
    # resolve to a signed URL whose bytes match the recorded content length.
    media_ok = []
    tokens = MEDIA_TOKEN.findall(json.dumps([root.get("input"), root.get("output")])) if root else []
    for media_id in dict.fromkeys(m_id for _, m_id in tokens):
        m = config.api("GET", f"/api/public/media/{media_id}")
        blob = httpx.get(m["url"], timeout=30)
        media_ok.append(blob.status_code == 200 and len(blob.content) == m.get("contentLength"))
        print(f"      media {media_id[:10]}… {m.get('contentType')} {m.get('contentLength')} B "
              f"→ download {'OK' if media_ok[-1] else 'FAILED'}")
    checks["media retrievable"] = bool(media_ok) and all(media_ok)
    if lang and result is not None:  # the caller's language survives STT, and the reply matches it
        heard = voice.detect_language(result.get("transcript") or "")
        said = voice.detect_language(voice.speakable(result.get("answer") or ""))
        checks[f"transcript in '{lang}'"] = f"{heard}" if heard == lang else False
        checks[f"spoken reply in '{lang}'"] = f"{said}" if said == lang else False
    passed = all(bool(v) for v in checks.values())
    for k, v in checks.items():
        print(f"      {'✓' if v else '✗'} {k}: {v}")
    for label, gen in (("STT", stt), ("TTS", tts)):
        if gen:
            print(f"      {label} model={gen.get('model')} usage={gen.get('usageDetails')} "
                  f"cost=${gen.get('totalCost')} latency={gen.get('latency')}s "
                  f"first-output={gen.get('timeToFirstToken')}s")
    return passed


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run only files containing this substring")
    ap.add_argument("--lang", choices=["en", "es"], default=None, help="run only calls in this language")
    ap.add_argument("--verify", action="store_true", help="read each trace back from Langfuse")
    ap.add_argument("--save-replies", action="store_true", help="write reply audio to logs/voice-replies/")
    args = ap.parse_args()

    results = []
    for call in _calls(args.only, args.lang):
        audio = (SAMPLES / call["file"]).read_bytes()
        lang = call.get("lang")
        print(f"\n▶ {call['file']} — {call.get('scenario', '')}  (as {call['customer_id']}, lang={lang or 'auto'})")
        try:
            r = await voice.run_voice_turn(audio, call["file"], customer_id=call["customer_id"], language=lang)
        except Exception as exc:  # noqa: BLE001 — one bad call must not stop the workshop
            print(f"  ✗ {type(exc).__name__}: {exc}")
            continue
        print(f"  heard : {r['transcript']}")
        print(f"  said  : {r['answer'][:300]}")
        print(f"  tools : {', '.join(r['tools_used']) or 'none'}   risks: {', '.join(r['risks']) or 'none'}"
              f"   stt/agent/tts ms: {r['stt_ms']}/{r['agent_ms']}/{r['tts_ms']}")
        print(f"  trace : {r['trace_url']}")
        if args.save_replies:
            out = DEMO_DIR / "logs" / "voice-replies" / call["file"].replace(".mp3", ".reply.mp3")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(base64.b64decode(r["reply_audio_b64"]))
            print(f"  reply : {out.relative_to(DEMO_DIR)}")
        results.append((call["file"], lang, r))

    config.flush()  # spans AND media uploads leave the process before we read back

    if args.verify:
        print("\n── verifying on Langfuse ──")
        ok = True
        for name, lang, r in results:
            print(f"  {name}  {r['trace_id']}")
            ok &= verify(r["trace_id"], lang, r)
        print("\nALL VOICE TRACES VERIFIED" if ok and results else "\nVERIFICATION FAILED")
        sys.exit(0 if ok and results else 1)


if __name__ == "__main__":
    asyncio.run(main())
