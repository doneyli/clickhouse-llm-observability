"""
Agentic RAG Demo — FastAPI server.

Endpoints:
    GET  /health         -> liveness + ClickHouse chunk count
    POST /query          -> {question, session_id?} -> agent result (answer, route, steps)

Mirrors the other demo APIs (text-to-sql :8002, vector-rag :8003); runs on :8006.
"""

from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from graph import create_agent
import langfuse_config as lf

app = FastAPI(title="Agentic RAG Demo", version="1.0.0")

# Built once at startup (loads embedding model + ClickHouse connection).
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        _agent = create_agent()
    return _agent


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


@app.get("/health")
def health():
    try:
        count = _get_agent().store.count()
        return {"status": "ok", "kb_chunks": count, "langfuse": lf.is_langfuse_enabled()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@app.post("/query")
def query(req: QueryRequest):
    return _get_agent().run(req.question, session_id=req.session_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
