"""
Query Router Demo — FastAPI front door.

Endpoints:
    GET  /health   -> router liveness + reachability of the 3 handler services + Langfuse status
    POST /query     -> {question, session_id?} -> {route, confidence, rationale, answer, handled_by, ...}

The router classifies the question (its own `route-query` generation), gates on
confidence, and dispatches over HTTP to exactly one specialist handler
(text-to-sql :8002 / vector-rag :8003 / agentic-rag :8006) or an in-process
fallback. One Langfuse trace `route-and-dispatch` shows the router decision AND
the chosen handler's full nested subtree. Runs on :8000 (mapped to host :8008).
"""

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from handlers import HANDLERS, dispatch
import langfuse_config as lf
from router import classify

app = FastAPI(title="Query Router Demo", version="1.0.0")


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


def run(question: str, session_id: Optional[str] = None) -> dict:
    """Classify -> gate -> dispatch, all under ONE trace named route-and-dispatch."""
    session_id = session_id or lf.new_session_id()
    with lf.trace_context("route-and-dispatch", session_id=session_id):  # verb-first, stable name
        decision = classify(question)
        result = dispatch(decision, question, session_id)
    lf.flush()
    return {"question": question, **decision, **result, "session_id": session_id}


@app.get("/health")
def health():
    """Report our own liveness, whether Langfuse tracing is on, and whether each
    downstream handler answers /health (the router still runs — and degrades to
    fallback — if a handler is down, so this is informational, not fatal)."""
    handlers = {}
    for route, base_url in HANDLERS.items():
        try:
            r = httpx.get(f"{base_url}/health", timeout=3.0)
            handlers[route] = "ok" if r.status_code == 200 else f"http {r.status_code}"
        except Exception as e:
            handlers[route] = f"unreachable: {e.__class__.__name__}"
    return {"status": "ok", "langfuse": lf.is_langfuse_enabled(), "handlers": handlers}


@app.post("/query")
def query(req: QueryRequest):
    return run(req.question, session_id=req.session_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
