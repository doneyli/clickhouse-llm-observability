"""
Presenter portal: the marketer's workspace in front of the agent, plus a presenter
console for the fault switches. Every run streams its steps live and links to its trace.

    .venv/bin/python webapp/server.py          # http://localhost:8097
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent import config  # noqa: E402
from agent.loop import new_session_id, run_agent  # noqa: E402
from agent.scenarios import SCENARIOS, user_for  # noqa: E402
from agent.tools import FIXES  # noqa: E402
from agent.world import CUSTOMERS  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
PORT = 8097
app = FastAPI(title="Marketing agent — presenter portal")
_runs: Dict[str, "queue.Queue"] = {}


class RunRequest(BaseModel):
    customer: str
    task: str
    scenario: Optional[str] = None
    session_id: Optional[str] = None
    history: List[Dict[str, str]] = []
    fixes: List[str] = []
    prompt_version: Optional[int] = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def portal_config():
    return {
        "project_url": config.project_url(),
        "environment": config.ENVIRONMENT,
        "model": config.AGENT_MODEL,
        "fixes": sorted(FIXES),
        "customers": [{"id": c.id, "name": c.name, "category": c.category, "marketers": c.marketers,
                       "scenarios": [{"name": s.name, "task": s.task, "note": s.note,
                                      "user": user_for(s), "follow_ups": s.follow_ups}
                                     for s in SCENARIOS.values() if s.customer == c.id]}
                      for c in CUSTOMERS.values()],
    }


@app.post("/api/run")
def start_run(req: RunRequest):
    if req.customer not in CUSTOMERS:
        raise HTTPException(400, f"unknown customer {req.customer}")
    if set(req.fixes) - FIXES:
        raise HTTPException(400, f"unknown fixes {sorted(set(req.fixes) - FIXES)}")
    run_id = uuid.uuid4().hex[:12]
    events: "queue.Queue" = queue.Queue()
    _runs[run_id] = events
    session_id = req.session_id or new_session_id(req.customer)
    scenario = SCENARIOS.get(req.scenario) if req.scenario else None

    def work():
        try:
            r = run_agent(req.customer, req.task, task_kind=scenario.name if scenario else "adhoc",
                          user_id=user_for(scenario) if scenario else None, session_id=session_id,
                          history=req.history, fixes=req.fixes, prompt_version=req.prompt_version,
                          surface="portal", on_event=lambda m: events.put({"type": "step", "text": m}))
            config.get_langfuse().flush()
            events.put({"type": "done", "trace_url": r.url, "final_text": r.final_text, "steps": r.steps,
                        "duration_s": round(r.duration_s, 1), "decision": r.decision,
                        "decision_comment": r.decision_comment, "session_id": session_id,
                        "session_url": config.project_url(f"/sessions/{session_id}"),
                        "instructions_version": r.instructions_version,
                        "tool_calls": [c["name"] for c in r.state.calls]})
        except Exception as e:  # surface the failure in the UI instead of hanging the stream
            events.put({"type": "error", "text": f"{type(e).__name__}: {e}"})

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id, "session_id": session_id}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    events = _runs.get(run_id)
    if events is None:
        raise HTTPException(404, "unknown run")

    def stream():
        while True:
            try:
                ev = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(ev)}\n\n"
            if ev["type"] in ("done", "error"):
                _runs.pop(run_id, None)
                return

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    project = config.verify_project()
    print(f"Portal → http://localhost:{PORT}   (Langfuse project {project['name']}, environment {config.ENVIRONMENT})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
