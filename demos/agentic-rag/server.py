"""
Agentic RAG Demo — FastAPI server.

Endpoints:
    GET  /health         -> liveness + ClickHouse chunk count
    POST /query          -> {question, session_id?, trace_context?} -> agent result (answer, route, steps)

Mirrors the other demo APIs — text-to-sql :8002 and vector-rag :8003 now serve
`/query` too — and is dispatched to by the :8008 query-router front door. Runs
on :8006. When `trace_context` is provided (by the router), the agent nests its
whole subtree under the router's trace instead of opening its own (see
graph.py::run); omit it for today's standalone behavior.
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
    trace_context: Optional[dict] = None  # {"trace_id","parent_span_id"} from the router front door


@app.get("/health")
def health():
    try:
        count = _get_agent().store.count()
        return {"status": "ok", "kb_chunks": count, "langfuse": lf.is_langfuse_enabled()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@app.post("/query")
def query(req: QueryRequest):
    return _get_agent().run(req.question, session_id=req.session_id, trace_context=req.trace_context)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
