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

import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.config import (get_langfuse, record_score, verify_project,
                          langfuse_api, LANGFUSE_HOST, AGENT_MODEL)
from agent.concierge import run_turn
from agent.catalog import get_listing

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Property Concierge")


def _project_id() -> str:
    """Resolve the project id so we can build trace deep-links."""
    try:
        _, data = langfuse_api("GET", "/api/public/projects", timeout=10)
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


class FeedbackRequest(BaseModel):
    trace_id: str
    # 1 = 👍 helpful, 0 = 👎 not helpful. Stored NUMERIC so Langfuse can chart it
    # as a satisfaction rate (mean of user-feedback) alongside the automated evals.
    value: int
    comment: str | None = None


# Per-session state: conversation history (agent context) + a monotonic turn
# counter (stable turn-N labels — must NOT be derived from the capped history).
# One conversation = one trace. In-memory (resets on restart) — fine for a demo.
_SESSIONS: dict[str, dict] = {}
_SESSIONS_LOCK = threading.Lock()
MAX_HISTORY_TURNS = 8  # cap the history fed to the agent (~4 exchanges)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "model": AGENT_MODEL, "langfuse_host": LANGFUSE_HOST}


@app.post("/api/chat")
def chat(req: ChatRequest):
    # Session-less callers get a unique id so they never share history or a trace.
    sid = req.session_id or f"anon-{uuid.uuid4().hex[:12]}"
    # One conversation = one trace: a deterministic trace id from the session id,
    # so every turn of this chat lands in the same trace as turn-1, turn-2, ….
    conv_trace_id = get_langfuse().create_trace_id(seed=sid)
    with _SESSIONS_LOCK:
        state = _SESSIONS.get(sid, {"history": [], "turns": 0})
        history = list(state["history"])
        turn_index = state["turns"]

    # Pass the resolved sid (not the raw None) so the trace carries the session.
    result = run_turn(req.query, session_id=sid, user_id="portal-visitor",
                      extra_tags=["portal"], history=history,
                      conversation_trace_id=conv_trace_id, turn_index=turn_index)

    with _SESSIONS_LOCK:
        prev = _SESSIONS.get(sid, {"history": [], "turns": 0})
        new_hist = (prev["history"] + [
            {"role": "user", "content": req.query},
            {"role": "assistant", "content": result["answer"]},
        ])[-MAX_HISTORY_TURNS:]
        _SESSIONS[sid] = {"history": new_hist, "turns": prev["turns"] + 1}

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


@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    """Attach explicit user feedback (👍/👎) to the trace as a Langfuse score.

    This is the **Monitor** node's 'feedback' signal in the AI Engineering loop:
    real user judgement lands next to the automated code/LLM-judge scores on the
    very same trace, so low-rated conversations are easy to surface and route to
    review or into the eval dataset.
    """
    if not req.trace_id or not req.trace_id.strip():
        raise HTTPException(status_code=400, detail="trace_id is required")
    lf = get_langfuse()
    value = 1 if req.value else 0
    record_score(
        lf,
        trace_id=req.trace_id,
        name="user-feedback",
        value=value,
        data_type="NUMERIC",
        comment=req.comment or ("👍 helpful" if value else "👎 not helpful"),
    )
    lf.flush()
    return {"ok": True, "trace_id": req.trace_id, "value": value}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
