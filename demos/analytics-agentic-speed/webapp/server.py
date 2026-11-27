"""Live view of the analytics agent: start a run, watch the sweep fill in.

    .venv/bin/python -m uvicorn webapp.server:app --port 8099     # from demos/analytics-agentic-speed
    open http://localhost:8099

One process, one shared ClickHouse client. A run executes the same `run_question` as
the CLI (same trace, same scores); its structured progress events are kept in memory
and streamed to the page as Server-Sent Events, replayed from the start for late joiners.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from opentelemetry import context as otel_context
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))

from explorer.config import ClickHouseSettings, RunConfig, load_env  # noqa: E402
from explorer.models import Question  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
MAX_EVENTS = 20000  # a 4-round, 300-scenario run emits ~1,300


class Run:
    def __init__(self, run_id: str) -> None:
        self.id = run_id
        self.events: list[dict[str, Any]] = []
        self.done = False
        self._waiters: list[asyncio.Future] = []

    def push(self, event: dict[str, Any]) -> None:
        if len(self.events) < MAX_EVENTS:
            self.events.append(event)
        for f in self._waiters:
            if not f.done():
                f.set_result(None)
        self._waiters.clear()

    async def wait(self, timeout: float) -> None:
        f = asyncio.get_running_loop().create_future()
        self._waiters.append(f)
        try:
            await asyncio.wait_for(f, timeout)
        except asyncio.TimeoutError:
            pass


STATE: dict[str, Any] = {"runs": {}, "tasks": set()}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_env()
    from explorer.ch_client import ClickHouseClient
    from explorer.tracing import get_langfuse

    settings = ClickHouseSettings.from_env()
    if "clickhouse.com" in settings.host and settings.user == "demo":
        print("warning: the public playground's quota can't serve sweeps; use your Cloud service")
    ch = ClickHouseClient(settings, max_connections=128)
    await ch.__aenter__()
    STATE.update(ch=ch, lf=get_langfuse(), settings=settings)
    try:
        STATE["ping_ms"] = round(await ch.ping())
    except Exception as e:  # noqa: BLE001 - the page shows it
        STATE["ping_ms"] = None
        STATE["ping_error"] = str(e)[:300]
    yield
    await ch.__aexit__(None, None, None)
    await asyncio.to_thread(STATE["lf"].flush)


app = FastAPI(title="Analytics at Agentic Speed", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class RunRequest(BaseModel):
    question_id: Optional[str] = None
    ask: Optional[str] = Field(default=None, max_length=2000)
    max_scenarios: int = Field(default=300, ge=1, le=600)
    rounds: int = Field(default=4, ge=1, le=6)
    concurrency: int = Field(default=32, ge=1, le=64)


def _questions() -> dict[str, Question]:
    from main import load_questions

    return load_questions()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
async def info() -> dict[str, Any]:
    s: ClickHouseSettings = STATE["settings"]
    cfg = RunConfig()
    host = s.host.split(".")
    return {
        "host": f"{host[0][:4]}…{'.'.join(host[1:])}" if len(host) > 2 else s.host,
        "table": s.qualified_table, "ping_ms": STATE.get("ping_ms"), "ping_error": STATE.get("ping_error"),
        "model": cfg.model, "effort": cfg.effort,
        "questions": [{"id": q.id, "text": q.text, "answer_keys": q.answer_keys} for q in _questions().values()],
    }


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict[str, str]:
    from explorer.agent import run_question

    if req.ask and req.ask.strip():
        question = Question(id="adhoc", text=req.ask.strip())
    else:
        qs = _questions()
        if not req.question_id or req.question_id not in qs:
            raise HTTPException(400, "pick a question or type one")
        question = qs[req.question_id]
    cfg = RunConfig(max_scenarios_per_round=req.max_scenarios, max_rounds=req.rounds,
                    concurrency=req.concurrency)
    run = Run(uuid.uuid4().hex[:12])
    STATE["runs"][run.id] = run

    async def go() -> None:
        # FastAPI emits its own OpenTelemetry span per request (`fastapi.endpoint`), and
        # a task inherits the request's context. Langfuse's export filter drops that span,
        # so without a clean context the agent's root would point at a parent that never
        # arrives, and that ends long before the run does. Start every run as its own trace.
        token = otel_context.attach(otel_context.Context())
        try:
            await run_question(question, cfg, ch=STATE["ch"], lf=STATE["lf"], tags=["live", "ui"],
                               on_progress=run.push)
        except Exception as e:  # noqa: BLE001 - run_question already scores its own errors
            run.push({"type": "fatal", "error": f"{type(e).__name__}: {e}"[:500]})
        finally:
            otel_context.detach(token)
            run.done = True
            run.push({"type": "closed"})
            await asyncio.to_thread(STATE["lf"].flush)

    task = asyncio.create_task(go())
    STATE["tasks"].add(task)
    task.add_done_callback(STATE["tasks"].discard)
    return {"run_id": run.id}


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    run: Optional[Run] = STATE["runs"].get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")

    async def stream():
        i = 0
        while True:
            while i < len(run.events):
                yield f"data: {json.dumps(run.events[i], default=str)}\n\n"
                i += 1
            if run.done and i >= len(run.events):
                return
            await run.wait(15)
            if i >= len(run.events):
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
