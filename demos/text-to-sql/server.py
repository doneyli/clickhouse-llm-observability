"""
Text-to-SQL Demo — FastAPI server.

Endpoints:
    GET  /health   -> liveness + Langfuse status
    POST /query    -> {question, session_id?, trace_context?} -> {answer, session_id}

Makes the already-mapped port 8002 real (mirrors demos/agentic-rag/server.py and
demos/vector-rag/server.py; the query-router front door on :8008 dispatches
here). When called standalone the trace is named `text-to-sql` (today's CLI
behavior, unchanged); when the router passes `trace_context`, this run nests
under the router's trace as a `text-to-sql-handler` agent observation
(Langfuse SDK v3 distributed tracing). The CLI (`python main.py`) is untouched.
"""

from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from sql_pipeline import create_pipeline
from langfuse_config import (
    get_langfuse_client,
    get_langfuse_handler,
    is_langfuse_enabled,
    langfuse_trace,
)

app = FastAPI(title="Text-to-SQL Demo", version="1.0.0")

# Lazy singleton so the process starts fast and the pipeline is reused.
_pipeline_singleton = None


def _pipeline():
    global _pipeline_singleton
    if _pipeline_singleton is None:
        _pipeline_singleton = create_pipeline()
    return _pipeline_singleton


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    trace_context: Optional[dict] = None  # {"trace_id","parent_span_id"} from the router


@app.get("/health")
def health():
    return {"status": "ok", "langfuse": is_langfuse_enabled()}


@app.post("/query")
def query(req: QueryRequest):
    handler = get_langfuse_handler()
    callbacks = [handler] if handler else None
    client = get_langfuse_client()

    if req.trace_context and client:
        # Join the caller's trace: this observation and every CallbackHandler
        # span under it nest into the router's trace (SDK v3 distributed tracing).
        with client.start_as_current_observation(
            as_type="agent", name="text-to-sql-handler",
            trace_context=req.trace_context, input={"question": req.question},
        ) as obs:
            answer = _pipeline().query(req.question, callbacks=callbacks)
            if obs:
                obs.update(output=answer)
    else:
        # Standalone: today's CLI behavior — a self-owned `text-to-sql` trace.
        with langfuse_trace():
            answer = _pipeline().query(req.question, callbacks=callbacks)

    # Non-destructive flush (never shutdown() — the singleton client is reused
    # across requests) so the trace is exported promptly.
    if client is not None:
        try:
            client.flush()
        except Exception:
            pass
    return {"answer": answer, "session_id": req.session_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
