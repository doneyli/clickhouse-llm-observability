"""
Property Concierge portal — the show-able app.

A small FastAPI service that puts a friendly web UI in front of the SAME
instrumented agent used everywhere else in this demo. Every chat message runs
one agent turn, so a live query in the browser produces a real trace (with
observation-level scores) in the Langfuse 'real-estate' project — and the UI
hands the presenter a direct "View trace in Langfuse" deep link.

Run:
    ./.venv/bin/python -m uvicorn webapp.server:app --port 8080
    # or: ./run_portal.sh
"""

import base64
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.config import (get_langfuse, verify_project, LANGFUSE_HOST,
                          LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, AGENT_MODEL)
from agent.concierge import run_turn
from agent.catalog import get_listing

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Property Concierge")


def _project_id() -> str:
    """Resolve the project id so we can build trace deep-links."""
    auth = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
    req = urllib.request.Request(f"{LANGFUSE_HOST}/api/public/projects",
                                 headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return data["data"][0]["id"]
    except Exception:
        return ""


PROJECT_ID = ""


@app.on_event("startup")
def _startup():
    global PROJECT_ID
    verify_project(quiet=True)
    get_langfuse()  # warm the client
    PROJECT_ID = _project_id()
    print(f"Property Concierge portal ready — Langfuse project id={PROJECT_ID}")


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None


# Per-session conversation memory so portal follow-ups carry context. Each turn
# is still its own Langfuse trace (grouped by session_id); this just feeds the
# agent the conversation so far. In-memory (resets on restart) — fine for a demo.
_SESSIONS: dict[str, list] = {}
_SESSIONS_LOCK = threading.Lock()
MAX_HISTORY_TURNS = 8  # keep the last ~4 exchanges


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "model": AGENT_MODEL, "langfuse_host": LANGFUSE_HOST}


@app.post("/api/chat")
def chat(req: ChatRequest):
    sid = req.session_id or "default"
    # One conversation = one trace: a deterministic trace id from the session id,
    # so every turn of this chat lands in the same trace as turn-1, turn-2, ….
    conv_trace_id = get_langfuse().create_trace_id(seed=sid)
    with _SESSIONS_LOCK:
        history = list(_SESSIONS.get(sid, []))
    turn_index = len(history) // 2  # 2 entries (user+assistant) per prior turn

    result = run_turn(req.query, session_id=req.session_id, user_id="portal-visitor",
                      extra_tags=["portal"], history=history,
                      conversation_trace_id=conv_trace_id, turn_index=turn_index)

    with _SESSIONS_LOCK:
        turns = _SESSIONS.get(sid, []) + [
            {"role": "user", "content": req.query},
            {"role": "assistant", "content": result["answer"]},
        ]
        _SESSIONS[sid] = turns[-MAX_HISTORY_TURNS:]

    listings = [get_listing(i) for i in result["listings_shown"]]
    listings = [l for l in listings if l]

    trace_url = (f"{LANGFUSE_HOST}/project/{PROJECT_ID}/traces/{result['trace_id']}"
                 if PROJECT_ID else f"{LANGFUSE_HOST}")

    return {
        "answer": result["answer"],
        "listings": listings,
        "tools_called": result["tools_called"],
        "trace_id": result["trace_id"],
        "trace_url": trace_url,
        "session_id": req.session_id,
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
