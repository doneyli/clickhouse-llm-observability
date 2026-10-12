"""Small workshop portal. Customer selection simulates identity, not authentication."""
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from northwind import config, tracing


@asynccontextmanager
async def lifespan(app):
    yield
    config.flush()


app = FastAPI(title="Northwind banking workshop", lifespan=lifespan)
STATIC = config.ROOT / "src/static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class Chat(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    customer_id: Literal["C-1001", "C-1002", "C-1003", "C-1004"] = "C-1001"
    session_id: str = Field(min_length=1, max_length=100)
    history: list[Message] = Field(default_factory=list, max_length=20)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "tracing": tracing.ENABLED, "workshop": config.WORKSHOP_ID,
            "model": config.AGENT_MODEL, "langfuse_url": config.LANGFUSE_BASE_URL}


@app.post("/chat")
async def chat(request: Chat):
    from northwind.agent import run_turn
    try:
        result = await run_turn(request.question, customer_id=request.customer_id, session_id=request.session_id,
                                history=[message.model_dump() for message in request.history])
        config.flush()
        if result["error"]:
            raise HTTPException(502, "The agent could not complete this turn. Check the model key and MCP process.")
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "The agent could not complete this turn. Check the server terminal and workshop setup.")
