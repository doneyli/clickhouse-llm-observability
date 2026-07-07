"""
LibreChat → Langfuse feedback bridge — the **Monitor** node's "feedback" signal.

LibreChat has native thumbs feedback on each answer:
    PUT /api/messages/:conversationId/:messageId/feedback
    body: {"feedback": {"rating": "thumbsUp" | "thumbsDown", ...}}  (or {"feedback": null})

nginx mirrors that route to this sidecar **fire-and-forget** (a down/slow bridge
never affects the user's click — see librechat/nginx.conf). We map the LibreChat
conversation+message back to its Langfuse trace and write a `user-feedback` score,
so real user judgement lands next to the automated code/LLM-judge scores.

Mapping (verified against live traces):
    Langfuse trace.sessionId          == LibreChat conversationId
    Langfuse trace.metadata.messageId == LibreChat responseMessageId (the rated msg)
The Langfuse trace_id itself is a random OTEL id, so we resolve it by lookup.
The traces API reads from ClickHouse (which lags async OTEL ingestion), so the
lookup retries with backoff; the score write is idempotent (deterministic id), so
toggling the thumb updates the score instead of appending duplicates.
"""

import asyncio
import base64
import os

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://langfuse-web:3000").rstrip("/")
PK = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
SK = os.environ.get("LANGFUSE_SECRET_KEY", "")
_AUTH = base64.b64encode(f"{PK}:{SK}".encode()).decode()
HEADERS = {"Authorization": f"Basic {_AUTH}", "Content-Type": "application/json"}

# thumbsUp -> 1, thumbsDown -> 0 (NUMERIC so Langfuse charts a satisfaction rate).
RATING_TO_VALUE = {"thumbsUp": 1, "thumbsDown": 0}
# Backoff for the trace lookup — covers ClickHouse ingestion lag (~up to 45s).
_RETRY_DELAYS = [0, 2, 3, 5, 8, 12, 15]

app = FastAPI(title="LibreChat Feedback Bridge")


async def _resolve_trace_id(client: httpx.AsyncClient, conversation_id: str, message_id: str):
    for delay in _RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            r = await client.get(f"{LANGFUSE_HOST}/api/public/traces",
                                 params={"sessionId": conversation_id, "limit": 50}, headers=HEADERS)
        except Exception as e:  # transient network error → retry
            print(f"[feedback-bridge] traces lookup error: {e}")
            continue
        if r.status_code == 200:
            for t in r.json().get("data", []):
                md = t.get("metadata") or {}
                if md.get("messageId") == message_id:
                    return t["id"]
    return None


async def _handle(conversation_id: str, message_id: str, rating: str):
    value = RATING_TO_VALUE.get(rating)
    if value is None:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        trace_id = await _resolve_trace_id(client, conversation_id, message_id)
        if not trace_id:
            print(f"[feedback-bridge] no trace for conv={conversation_id} msg={message_id}; dropped")
            return
        body = {
            "id": f"lc-fb-{message_id}",  # deterministic → re-rating updates, no dup
            "traceId": trace_id,
            "name": "user-feedback",
            "value": value,
            "dataType": "NUMERIC",
            "comment": f"LibreChat {rating}",
        }
        try:
            r = await client.post(f"{LANGFUSE_HOST}/api/public/scores", json=body, headers=HEADERS)
            print(f"[feedback-bridge] scored trace={trace_id} value={value} status={r.status_code}")
        except Exception as e:
            print(f"[feedback-bridge] score write failed: {e}")


@app.api_route("/mirror/api/messages/{conversation_id}/{message_id}/feedback",
               methods=["PUT", "POST", "DELETE"], status_code=202)
async def mirror(conversation_id: str, message_id: str, request: Request, bg: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    fb = (payload or {}).get("feedback")
    rating = fb.get("rating") if isinstance(fb, dict) else None  # None when cleared
    # Return immediately; do the retrying lookup + score off the request path so the
    # user's feedback click is never coupled to Langfuse latency.
    if rating in RATING_TO_VALUE:
        bg.add_task(_handle, conversation_id, message_id, rating)
    return {"accepted": rating in RATING_TO_VALUE, "rating": rating}


@app.get("/health")
def health():
    return {"ok": True, "langfuse_host": LANGFUSE_HOST, "configured": bool(PK and SK)}
