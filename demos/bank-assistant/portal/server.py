"""
Northwind Bank — presenter portal (FastAPI).

    .venv/bin/python -m uvicorn portal.server:app --port 8090     (or scripts/run_portal.sh)

Three tabs, one page (portal/static/index.html):
  1. Customer assistant — chat with the LangGraph agent as one of 4 customers.
     Every reply links to its Langfuse trace and its APM (Jaeger) trace, and
     thumbs up/down writes a `user-feedback` score onto that trace.
  2. Voice — run a recorded call through northwind.voice (loaded lazily, so the
     portal works before that module exists).
  3. Presenter console — run the demo-act CLI scripts from a FIXED allowlist
     and stream their output. The browser only ever sends an act id; it can
     never choose the command line.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

DEMO_DIR = Path(__file__).resolve().parent.parent
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

# Shell-exported Langfuse keys must never leak in (config.py ignores them too).
for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL"):
    os.environ.pop(_k, None)

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from northwind import agent, config  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
VOICE_DIR = DEMO_DIR / "data" / "voice"
PY = str(DEMO_DIR / ".venv" / "bin" / "python")
PROMPT_NAME = "northwind-assistant-system"
PROMPT_LABEL = os.environ.get("NORTHWIND_PROMPT_LABEL", "production")
AUDIO_EXT = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
             ".webm": "audio/webm"}

# Display data only (the MCP server is the system of record; importing it here
# would start a second Langfuse client under the wrong service name).
CUSTOMERS = [
    {"id": "C-1001", "name": "Ana Torres", "segment": "Everyday", "since": "2017",
     "products": "Checking · Savings · Classic credit •••4417"},
    {"id": "C-1002", "name": "Ben Okafor", "segment": "Premier", "since": "2012",
     "products": "Premier checking · Term deposit · Platinum •••9921"},
    {"id": "C-1003", "name": "Carla Mendes", "segment": "Everyday", "since": "2022",
     "products": "Checking (overdrawn) · Debit •••3088"},
    {"id": "C-1004", "name": "David Kim", "segment": "Premier", "since": "2015",
     "products": "Premier checking · Platinum •••5530"},
]
_CUSTOMER_IDS = {c["id"] for c in CUSTOMERS}
CHANNELS = ["web", "app", "whatsapp"]

# ── Presenter console: the ONLY commands this server will ever execute ────────
ACTS: "OrderedDict[str, dict]" = OrderedDict()


def _act(act_id, group, title, script, args, blurb, show, button="Run"):
    ACTS[act_id] = {"id": act_id, "group": group, "title": title, "script": script,
                    "argv": [PY, script, *args], "blurb": blurb, "show": show, "button": button}


_act("preflight", "0", "Pre-flight check", "portal/preflight.py", [],
     "Checks every dependency the demo needs, without printing secrets.",
     "MCP server, Langfuse auth + project, prompt labels, model keys, Jaeger, n8n, voice samples.")
_act("traffic", "1", "Generate production traffic", "scripts/generate_traffic.py", ["--n", "20"],
     "20 realistic customer turns across channels, customers and intents.",
     "Tracing → Traces & Sessions; Dashboards: cost, latency, tokens by channel.")
_act("redteam", "2", "Red-team attack suite", "scripts/generate_traffic.py", ["--scenario", "security"],
     "Prompt injection, cross-customer access, PII and investment-advice probes.",
     "Filter tags risk:* · security-risk scores · guardrail observations.")
_act("voice", "3", "Run voice calls", "scripts/run_voice_calls.py", [],
     "Processes the recorded calls: speech-to-text → agent → text-to-speech.",
     "Voice traces with audio attachments, STT/TTS generations and latency.")
_act("n8n", "4", "Run n8n complaint workflow", "scripts/run_n8n_samples.py", [],
     "Sends sample complaints through the n8n triage workflow.",
     "Low-code workflow traces next to code-first agent traces.")
_act("exp_prompt", "5", "Experiment: prompt A/B (production vs staging)", "scripts/run_experiment.py",
     ["--prompt-label", "staging"],
     "Runs the golden dataset against the staging prompt.",
     "Datasets → Runs: compare staging vs production side by side.")
_act("exp_model", "6", "Experiment: model comparison", "scripts/run_experiment.py", ["--model", "gpt-4.1"],
     "Same dataset, same prompt, a different model.",
     "Cost, latency and judge scores per model in the run comparison.")
_act("gate", "7", "CI quality gate on development prompt", "scripts/prompt_gate.py",
     ["--prompt-label", "development"],
     "The check a pull request runs before a prompt can ship.",
     "Expect FAIL: the development prompt drops citations — the gate blocks it.")
_act("promote", "8", "Promote prompt (staging → production)", "scripts/prompt_label.py", ["--promote", "staging"],
     "Moves the production label to the staging version. No redeploy.",
     "Prompts → versions & labels; the header version updates.", button="Promote")
_act("rollback", "8", "Roll back prompt", "scripts/prompt_label.py", ["--rollback"],
     "Moves the production label back to the previous version.",
     "Instant rollback — the app picks it up within seconds.", button="Roll back")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class Job:
    def __init__(self, act: dict):
        self.id = uuid.uuid4().hex[:12]
        self.act = act
        self.lines: list[str] = []
        self.started = time.time()
        self.ended: Optional[float] = None
        self.returncode: Optional[int] = None
        self.stopped = False
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._waiters: list[asyncio.Future] = []

    @property
    def running(self) -> bool:
        return self.ended is None

    def add(self, line: str) -> None:
        self.lines.append(line)
        self.notify()

    def notify(self) -> None:
        for f in self._waiters:
            if not f.done():
                f.set_result(None)
        self._waiters.clear()

    async def wait_change(self, seen: int, timeout: float) -> None:
        """Return when there are lines beyond `seen`, the job ended, or `timeout` passed."""
        if len(self.lines) > seen or not self.running:
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)

    def summary(self) -> dict:
        return {"job_id": self.id, "act_id": self.act["id"], "title": self.act["title"],
                "command": _display_cmd(self.act), "running": self.running,
                "returncode": self.returncode, "stopped": self.stopped, "lines": len(self.lines),
                "elapsed_s": round((self.ended or time.time()) - self.started, 1)}


_job: Optional[Job] = None
_job_lock = asyncio.Lock()


def _display_cmd(act: dict) -> str:
    return " ".join([".venv/bin/python", *act["argv"][1:]])


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL")}
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", NO_COLOR="1", TERM="dumb",
               PYTHONPATH=str(DEMO_DIR))
    return env


async def _run_job(job: Job) -> None:
    job.add(f"$ {_display_cmd(job.act)}")
    try:
        job.proc = await asyncio.create_subprocess_exec(
            *job.act["argv"], cwd=str(DEMO_DIR), env=_child_env(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        assert job.proc.stdout is not None
        buf = b""
        while True:
            chunk = await job.proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            # split on \n and \r so progress bars still stream
            parts = re.split(rb"\r\n|\n|\r", buf)
            buf = parts.pop()
            for p in parts:
                job.add(_ANSI.sub("", p.decode("utf-8", "replace")))
        if buf:
            job.add(_ANSI.sub("", buf.decode("utf-8", "replace")))
        job.returncode = await job.proc.wait()
    except Exception as exc:  # noqa: BLE001
        job.add(f"[portal] failed to run: {type(exc).__name__}: {exc}")
        job.returncode = -1
    finally:
        job.ended = time.time()
        rc = job.returncode
        if job.stopped:
            job.add(f"[portal] stopped by presenter after {job.ended - job.started:.1f}s")
        else:
            job.add(f"[portal] exit code {rc} · {job.ended - job.started:.1f}s")
        job.notify()
        if job.act["id"] in ("promote", "rollback"):
            _prompt_cache["at"] = 0.0  # force a fresh version read


# ── Chat sessions (in memory) ─────────────────────────────────────────────────
_SESSIONS: "OrderedDict[str, dict]" = OrderedDict()
_MAX_SESSIONS = 500


def _session(session_id: str, customer_id: str) -> dict:
    s = _SESSIONS.get(session_id)
    if s is None or s["customer_id"] != customer_id:
        s = {"customer_id": customer_id, "history": [], "created": time.time()}
        _SESSIONS[session_id] = s
        while len(_SESSIONS) > _MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
    _SESSIONS.move_to_end(session_id)
    return s


# ── Langfuse helpers (sync SDK calls run in threads, never on the loop) ──────
_prompt_cache: dict = {"at": 0.0, "data": None}
_project_cache: dict = {"id": None}


def _project_id_sync() -> Optional[str]:
    if _project_cache["id"] is None:
        try:
            _project_cache["id"] = config.project_id()
        except Exception:  # noqa: BLE001
            return None
    return _project_cache["id"]


def _prompt_version_sync() -> dict:
    try:
        p = config.get_langfuse().get_prompt(PROMPT_NAME, label="production", cache_ttl_seconds=0,
                                             type="text", max_retries=0, fetch_timeout_seconds=5)
        if getattr(p, "is_fallback", False):
            return {"ok": False, "version": None, "error": "prompt not found"}
        return {"ok": True, "version": p.version, "labels": list(getattr(p, "labels", []) or []),
                "name": PROMPT_NAME}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
        return {"ok": False, "version": None, "error": msg}


async def _prompt_version(force: bool = False) -> dict:
    if force or _prompt_cache["data"] is None or time.time() - _prompt_cache["at"] > 20:
        try:
            data = await asyncio.wait_for(asyncio.to_thread(_prompt_version_sync), timeout=8)
        except asyncio.TimeoutError:
            data = {"ok": False, "version": None, "error": "timed out"}
        _prompt_cache.update(at=time.time(), data=data)
    return _prompt_cache["data"]


async def _flush_bg() -> None:
    try:
        await asyncio.to_thread(config.flush)
    except Exception:  # noqa: BLE001
        pass


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)


_BG: set = set()


# ── Voice (lazy, hot-reloaded when the file changes) ──────────────────────────
_voice_state: dict = {"mod": None, "mtime": None, "error": None}


def _voice_module():
    path = DEMO_DIR / "northwind" / "voice.py"
    if not path.exists():
        _voice_state.update(mod=None, mtime=None, error="northwind/voice.py is not installed yet")
        return None
    mtime = path.stat().st_mtime
    if _voice_state["mod"] is not None and _voice_state["mtime"] == mtime:
        return _voice_state["mod"]
    try:
        if "northwind.voice" in sys.modules:
            mod = importlib.reload(sys.modules["northwind.voice"])
        else:
            mod = importlib.import_module("northwind.voice")
        if not hasattr(mod, "run_voice_turn"):
            raise AttributeError("run_voice_turn() missing")
        _voice_state.update(mod=mod, mtime=mtime, error=None)
        return mod
    except Exception as exc:  # noqa: BLE001
        _voice_state.update(mod=None, mtime=None, error=f"{type(exc).__name__}: {exc}"[:300])
        return None


def _pretty_call_name(stem: str) -> str:
    s = re.sub(r"^\d+[-_ ]*", "", stem).replace("_", " ").replace("-", " ").strip()
    return (s[:1].upper() + s[1:]) if s else stem


def _voice_manifest() -> dict:
    try:
        return {m["file"]: m for m in json.loads((VOICE_DIR / "manifest.json").read_text())}
    except Exception:  # noqa: BLE001
        return {}


def _voice_samples() -> list[dict]:
    if not VOICE_DIR.is_dir():
        return []
    manifest = _voice_manifest()
    names = {c["id"]: c["name"] for c in CUSTOMERS}
    out = []
    for f in sorted(VOICE_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXT and not f.name.startswith("."):
            m = manifest.get(f.name, {})
            note = m.get("script")
            if not note:
                side = f.with_suffix(".txt")
                note = side.read_text(errors="replace").strip()[:600] if side.exists() else None
            cid = m.get("customer_id") if m.get("customer_id") in _CUSTOMER_IDS else None
            out.append({"name": f.name, "title": m.get("scenario") or _pretty_call_name(f.stem),
                        "size_kb": round(f.stat().st_size / 1024), "url": f"/api/voice/file/{f.name}",
                        "script": note, "customer_id": cid, "customer_name": names.get(cid)})
    return out


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the ONE tracer provider + Langfuse client before anything can race
    # for it from a worker thread (two providers would split the APM correlation).
    config.get_langfuse()

    def _warm():
        try:
            _project_id_sync()
            agent.make_llm(config.AGENT_MODEL)  # imports the provider SDK once
        except Exception:  # noqa: BLE001
            pass
    _bg(asyncio.to_thread(_warm))
    _bg(_prompt_version(force=True))
    yield
    if _job and _job.running and _job.proc:
        try:
            _job.proc.terminate()
        except ProcessLookupError:
            pass
    await asyncio.to_thread(config.flush)


app = FastAPI(title="Northwind Bank — demo portal", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/info")
async def info(refresh: bool = False):
    pid = await asyncio.to_thread(_project_id_sync)
    prompt = await _prompt_version(force=refresh)
    base = config.LANGFUSE_BASE_URL
    return {
        "environment": config.ENVIRONMENT,
        "profile": config.PROFILE,
        "langfuse_base_url": base,
        "langfuse_target": "Langfuse Cloud" if "cloud.langfuse.com" in base else
                           ("Self-hosted Langfuse" if "localhost" in base or "127.0.0.1" in base else base),
        "project_id": pid,
        "project_url": f"{base}/project/{pid}" if pid else base,
        "prompt_name": PROMPT_NAME,
        "prompt_label": PROMPT_LABEL,
        "production_prompt": prompt,
        "model": config.AGENT_MODEL,
        "release": config.RELEASE,
        "links": [
            {"label": "Langfuse project", "url": f"{base}/project/{pid}" if pid else base},
            {"label": "Jaeger (APM)", "url": "http://localhost:16686"},
            {"label": "n8n", "url": "http://localhost:5678"},
            {"label": "Self-hosted Langfuse", "url": "http://localhost:3100"},
        ],
        "customers": CUSTOMERS,
        "channels": CHANNELS,
    }


@app.post("/api/session")
async def new_session():
    return {"session_id": f"nw-{uuid.uuid4()}"}


class ChatIn(BaseModel):
    message: str
    customer_id: str = "C-1001"
    channel: str = "web"
    session_id: Optional[str] = None
    prompt_label: Optional[str] = None


@app.post("/api/chat")
async def chat(body: ChatIn):
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "empty message")
    if body.customer_id not in _CUSTOMER_IDS:
        raise HTTPException(400, "unknown customer")
    channel = body.channel if body.channel in CHANNELS else "web"
    label = body.prompt_label if body.prompt_label in ("production", "staging", "development") else None
    session_id = body.session_id or f"nw-{uuid.uuid4()}"
    sess = _session(session_id, body.customer_id)
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(agent.run_turn(
            msg, customer_id=body.customer_id, session_id=session_id, history=list(sess["history"]),
            channel=channel, prompt_label=label, tags=["source:portal"]), timeout=120)
    except asyncio.TimeoutError:
        return JSONResponse({"answer": "The assistant took too long to answer. Please try again.",
                             "error": "timeout after 120s", "session_id": session_id,
                             "latency_ms": round((time.perf_counter() - t0) * 1000)}, status_code=200)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"answer": "Sorry, something went wrong on our side.",
                             "error": f"{type(exc).__name__}: {exc}"[:300], "session_id": session_id,
                             "latency_ms": round((time.perf_counter() - t0) * 1000)}, status_code=200)
    latency = round((time.perf_counter() - t0) * 1000)
    if not res.get("error"):
        sess["history"] += [{"role": "user", "content": msg}, {"role": "assistant", "content": res["answer"]}]
        sess["history"] = sess["history"][-20:]
    _bg(_flush_bg())  # traces visible in Langfuse by the time the presenter clicks
    return {**res, "session_id": session_id, "latency_ms": latency, "turn": len(sess["history"]) // 2,
            "prompt_label": label or PROMPT_LABEL}


class FeedbackIn(BaseModel):
    trace_id: str
    value: int
    comment: Optional[str] = None


@app.post("/api/feedback")
async def feedback(body: FeedbackIn):
    if not re.fullmatch(r"[0-9a-f]{32}", body.trace_id or ""):
        raise HTTPException(400, "invalid trace id")
    value = 1 if body.value else 0
    comment = (body.comment or "").strip()[:1000] or None

    def _score():
        config.get_langfuse().create_score(trace_id=body.trace_id, name="user-feedback", value=value,
                                           data_type="BOOLEAN", comment=comment)
        config.flush()
    try:
        await asyncio.wait_for(asyncio.to_thread(_score), timeout=20)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)
    return {"ok": True, "value": value, "comment": comment}


# ── Voice endpoints ───────────────────────────────────────────────────────────
@app.get("/api/voice")
async def voice_info():
    mod = await asyncio.to_thread(_voice_module)
    return {"available": mod is not None, "error": _voice_state["error"], "samples": _voice_samples(),
            "voice_dir": "data/voice"}


@app.get("/api/voice/file/{name}")
async def voice_file(name: str):
    f = (VOICE_DIR / name).resolve()
    if f.parent != VOICE_DIR.resolve() or not f.is_file() or f.suffix.lower() not in AUDIO_EXT:
        raise HTTPException(404, "not found")
    return FileResponse(f, media_type=AUDIO_EXT[f.suffix.lower()])


@app.post("/api/voice/process")
async def voice_process(sample: Optional[str] = Form(None), customer_id: str = Form("auto"),
                        file: Optional[UploadFile] = File(None)):
    mod = await asyncio.to_thread(_voice_module)
    if mod is None:
        return JSONResponse({"error": _voice_state["error"] or "voice module unavailable"}, status_code=503)
    if customer_id not in _CUSTOMER_IDS:  # "auto" → the caller recorded in data/voice/manifest.json
        customer_id = (_voice_manifest().get(sample or "", {}).get("customer_id") if sample else None)
        customer_id = customer_id if customer_id in _CUSTOMER_IDS else "C-1001"
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in AUDIO_EXT:
            raise HTTPException(400, "upload a .wav or .mp3 file")
        audio = await file.read()
        filename = Path(file.filename).name
    elif sample:
        f = (VOICE_DIR / sample).resolve()
        if f.parent != VOICE_DIR.resolve() or not f.is_file():
            raise HTTPException(404, "sample not found")
        audio = await asyncio.to_thread(f.read_bytes)
        filename = f.name
    else:
        raise HTTPException(400, "choose a sample or upload a file")
    if not audio or len(audio) > 25 * 1024 * 1024:
        raise HTTPException(400, "audio is empty or larger than 25 MB")
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(mod.run_voice_turn(audio, filename, customer_id=customer_id), timeout=180)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "voice pipeline timed out after 180s"}, status_code=504)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:400]}, status_code=500)
    _bg(_flush_bg())
    out = dict(res or {})
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    out["filename"] = filename
    out["customer_id"] = customer_id
    return out


# ── Presenter console endpoints ───────────────────────────────────────────────
@app.get("/api/acts")
async def acts():
    return {"acts": [{k: a[k] for k in ("id", "group", "title", "blurb", "show", "button")}
                     | {"command": _display_cmd(a), "available": (DEMO_DIR / a["script"]).is_file()}
                     for a in ACTS.values()],
            "job": _job.summary() if _job else None}


@app.post("/api/run/{act_id}")
async def run_act(act_id: str):
    global _job
    act = ACTS.get(act_id)
    if act is None:
        raise HTTPException(404, "unknown act")
    if not (DEMO_DIR / act["script"]).is_file():
        raise HTTPException(409, f"{act['script']} is not available yet")
    async with _job_lock:
        if _job and _job.running:
            return JSONResponse({"error": f"'{_job.act['title']}' is still running — stop it first",
                                 "job": _job.summary()}, status_code=409)
        _job = Job(act)
        _bg(_run_job(_job))
    return _job.summary()


@app.post("/api/run-stop")
async def stop_act():
    job = _job
    if not job or not job.running or not job.proc:
        return {"ok": False, "error": "nothing is running"}
    job.stopped = True
    try:
        os.killpg(job.proc.pid, 15)
    except (ProcessLookupError, PermissionError):
        pass

    async def _hard_kill(j: Job):
        await asyncio.sleep(4)
        if j.running and j.proc:
            try:
                os.killpg(j.proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
    _bg(_hard_kill(job))
    return {"ok": True}


@app.get("/api/run-status")
async def run_status():
    return {"job": _job.summary() if _job else None}


@app.get("/api/run-stream/{job_id}")
async def run_stream(job_id: str, request: Request, offset: int = 0):
    job = _job
    if job is None or job.id != job_id:
        raise HTTPException(404, "job not found")
    last = request.headers.get("last-event-id")
    pos = int(last) + 1 if last and last.isdigit() else max(offset, 0)

    def _sse(event: str, data: str, eid: Optional[int] = None) -> str:
        head = f"id: {eid}\n" if eid is not None else ""
        return head + f"event: {event}\n" + "".join(f"data: {ln}\n" for ln in data.split("\n")) + "\n"

    async def gen():
        nonlocal pos
        yield "retry: 1500\n\n"
        while True:
            if await request.is_disconnected():
                return
            while pos < len(job.lines):
                yield _sse("line", job.lines[pos], pos)
                pos += 1
            if not job.running and pos >= len(job.lines):
                yield _sse("done", json.dumps(job.summary()))
                return
            before = len(job.lines)
            await job.wait_change(pos, timeout=10)
            if len(job.lines) == before and job.running:
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
