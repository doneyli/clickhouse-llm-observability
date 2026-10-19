"""
A2A client for the doc-intake service — plain JSON-RPC over HTTP, no a2a-sdk.

Why not the SDK: the Strands service pins a2a-sdk<0.4 while current releases are
1.x; calling `message/send` directly keeps this venv independent of that pin and
gives direct control over the W3C trace headers.

Trace context: `traceparent` + `baggage` go in the HTTP headers (config.inject_trace_context).
The baggage carries this claim's session, user, trace name, version and environment,
scoped to this one call with propagate_attributes(as_baggage=True) by the caller.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Optional

import httpx

from claims import config

A2A_PROTOCOL = "0.3"  # what the Strands service (a2a-sdk 0.3.x) speaks


class A2AError(RuntimeError):
    pass


def file_part(path: Path, name: Optional[str] = None) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {"kind": "file", "file": {"name": name or path.name, "mimeType": mime,
                                     "bytes": base64.b64encode(path.read_bytes()).decode()}}


async def send_message(text: str, files: list[dict], *, context_id: Optional[str] = None,
                       metadata: Optional[dict] = None, url: Optional[str] = None,
                       timeout: float = 180.0) -> dict:
    """Send one A2A message; return {"text", "task_id", "context_id", "state"}."""
    url = (url or config.DOC_INTAKE_URL).rstrip("/") + "/"
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
            "params": {"message": {
                "kind": "message", "role": "user", "messageId": str(uuid.uuid4()),
                **({"contextId": context_id} if context_id else {}),
                "parts": [{"kind": "text", "text": text}, *files],
                "metadata": metadata or {}}}}
    headers = {"Content-Type": "application/json", "A2A-Version": A2A_PROTOCOL,
               **config.inject_trace_context()}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise A2AError(f"A2A HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "error" in data:
        raise A2AError(f"A2A error: {data['error']}")
    result = data.get("result") or {}
    state = (result.get("status") or {}).get("state", "unknown")
    texts = [p.get("text", "") for a in result.get("artifacts") or [] for p in a.get("parts") or []
             if p.get("kind") == "text"]
    if state != "completed":
        msg = (result.get("status") or {}).get("message") or {}
        detail = " ".join(p.get("text", "") for p in msg.get("parts") or [])
        raise A2AError(f"A2A task {state}: {detail[:300]}")
    return {"text": "\n".join(texts), "task_id": result.get("id"), "context_id": result.get("contextId"),
            "state": state}


def parse_json(text: str):
    """The agent's JSON answer, tolerating a ```json fence or surrounding prose."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
