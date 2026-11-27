"""
Measure what each linkage mode does to a cross-process handoff (SPEC §6, question 2).

For every mode this starts the billing and tech-support A2A services as separate processes,
runs one customer turn from a third process (support-turn → concierge → handoff →
tech-support → handoff → billing), and inspects what each process's Langfuse SDK exported.

    .venv/bin/python scripts/linkage_matrix.py            # offline: spans captured to JSONL
    .venv/bin/python scripts/linkage_matrix.py --markdown docs/LINKAGE_MATRIX.md
    .venv/bin/python scripts/linkage_matrix.py --live --markdown docs/LINKAGE_MATRIX.live.md

Offline mode needs no Langfuse server and no LLM key: the agents are deterministic stubs,
and the spans are exactly what the SDK would send (after its export filter, app-root
marking and baggage handling). "Logical roots" follows Langfuse's definition: an
observation with no parent, or one the SDK marked as an application root.

`--live` runs every mode twice: offline as above, then against the Langfuse project in the
demo `.env`, reading the result back through the v2 observations API. There "roots" is the
server's own `isRootObservation`, the flag trace-level evaluators and the trace list use.
Live runs write to the `linkage-matrix` environment, away from the demo's dashboards. Spans
from split modes carry no session, so each mode is found by environment and time window.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

APP_ROOT = "langfuse.internal.is_app_root"
PORTS = {"billing": "19310", "tech-support": "19311"}
LIVE_ENV = "linkage-matrix"


def _env(capture: Optional[Path], run: str = "") -> dict:
    env = {**os.environ, "PYTHONPATH": str(DEMO),
           "BILLING_PORT": PORTS["billing"], "TECH_SUPPORT_PORT": PORTS["tech-support"]}
    for k in list(env):
        if k.startswith("LANGFUSE_"):  # keys come from the demo .env, never the shell
            env.pop(k)
    env.pop("HANDOFF_CAPTURE_DIR", None)
    if capture:
        env["HANDOFF_CAPTURE_DIR"] = str(capture)
    else:
        env.update(HANDOFF_ENVIRONMENT=LIVE_ENV, HANDOFF_MATRIX_RUN=run)
    return env


def _wait_ready(port: str, timeout: float = 20.0) -> None:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"service on :{port} did not start")


async def run_one_turn(mode: str) -> None:
    """The orchestrator side of one turn (runs in its own process)."""
    from langfuse import propagate_attributes

    from handoff import config
    from handoff.linkage import TurnContext
    from handoff.transport import A2ATransport

    lf = config.get_langfuse("orchestrator")
    run = os.environ.get("HANDOFF_MATRIX_RUN")
    session, user = f"matrix-{mode}" + (f"-{run}" if run else ""), "cust-1042"
    transport = A2ATransport(linkage=mode)
    with propagate_attributes(session_id=session, user_id=user, trace_name="support-turn",
                              tags=["linkage-matrix", f"linkage:{mode}"]):
        with lf.start_as_current_observation(as_type="agent", name="support-turn",
                                             input={"message": "Internet down since last night. Credit?"},
                                             metadata={"role": "orchestrator", "linkage": mode}) as root:
            with lf.start_as_current_observation(as_type="agent", name="concierge",
                                                 metadata={"role": "agent", "agent": "concierge"}):
                res = await transport.handoff(frm="concierge", to="tech-support", kind="transfer",
                                              reason="connectivity issue with a credit request",
                                              message="Customer's home internet is down; they want a credit.",
                                              payload={"line_id": "L-1042-1", "symptom": "no connectivity"},
                                              turn=TurnContext(session_id=session, user_id=user))
            root.update(output={"reply": res.text, "state": res.state})
    await transport.aclose()
    lf.flush()


def analyse(rows: list[dict]) -> dict:
    ids = {r["span_id"] for r in rows}
    remote = [r for r in rows if r["service"] != "orchestrator"]
    roots = [r for r in rows if r["parent_id"] is None or r["attributes"].get(APP_ROOT)]
    return {
        "traces": len({r["trace_id"] for r in rows}),
        "spans": len(rows),
        "logical_roots": len(roots),
        "root_names": ", ".join(f"{r['name']}@{r['service']}" for r in roots),
        "remote_session": f"{sum(1 for r in remote if r['attributes'].get('session.id'))}/{len(remote)}",
        "remote_user": f"{sum(1 for r in remote if r['attributes'].get('user.id'))}/{len(remote)}",
        "remote_trace_name": ", ".join(sorted({str(r['attributes'].get('langfuse.trace.name')) for r in remote})),
        "remote_env": ", ".join(sorted({str(r['attributes'].get('langfuse.environment')) for r in remote})),
        "dangling_parents": sum(1 for r in rows if r["parent_id"] and r["parent_id"] not in ids),
        "sessions": ", ".join(sorted({str(r['attributes'].get('session.id')) for r in rows})),
    }


def _run_processes(mode: str, env: dict) -> None:
    procs = [subprocess.Popen([sys.executable, "-m", "handoff.serve", "--agent", a, "--stub"], cwd=DEMO,
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             for a in ("billing", "tech-support")]
    try:
        for a in ("billing", "tech-support"):
            _wait_ready(PORTS[a])
        out = subprocess.run([sys.executable, __file__, "--run-one", mode], cwd=DEMO, env=env,
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(f"orchestrator failed for {mode}:\n{out.stderr[-2000:]}")
        time.sleep(1.0)  # services flush after each task; give the exporter a beat
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def measure(mode: str) -> dict:
    from handoff.capture import read_capture

    with tempfile.TemporaryDirectory(prefix=f"linkage-{mode}-") as tmp:
        capture = Path(tmp)
        _run_processes(mode, _env(capture))
        return analyse(read_capture(capture))


# --- live: the same turn against a real Langfuse project -----------------------------------

_ORCHESTRATOR = {"support-turn", "concierge"}


def _is_orchestrator(o: dict) -> bool:
    md = o.get("metadata") or {}
    return o["name"] in _ORCHESTRATOR or (o["name"] == "handoff" and md.get("handoff_from") == "concierge")


def read_back(start: datetime, end: datetime, expect: int, timeout: float = 240.0) -> list[dict]:
    """Observations the server holds for one mode's run, once ingestion has settled."""
    import httpx

    from handoff import config

    auth = (config.setting("LANGFUSE_PUBLIC_KEY"), config.setting("LANGFUSE_SECRET_KEY"))
    params = {"environment": LIVE_ENV, "fromStartTime": start.isoformat().replace("+00:00", "Z"),
              "toStartTime": end.isoformat().replace("+00:00", "Z"), "fields": "core,basic,metadata", "limit": 100}
    deadline, rows, seen = time.time() + timeout, [], -1
    while time.time() < deadline:
        r = httpx.get(f"{config.LANGFUSE_BASE_URL}/api/public/v2/observations", params=params, auth=auth, timeout=30)
        r.raise_for_status()
        rows = r.json()["data"]
        if len(rows) >= expect and len(rows) == seen:  # complete and stable across two polls
            return rows
        seen = len(rows)
        time.sleep(6)
    raise RuntimeError(f"only {len(rows)}/{expect} observations arrived within {timeout:.0f}s")


def analyse_live(rows: list[dict]) -> dict:
    ids = {o["id"] for o in rows}
    remote = [o for o in rows if not _is_orchestrator(o)]
    roots = [o for o in rows if o.get("isRootObservation")]
    return {
        "traces": len({o["traceId"] for o in rows}),
        "spans": len(rows),
        "logical_roots": len(roots),
        "root_names": ", ".join(sorted(o["name"] for o in roots)),
        "remote_session": f"{sum(1 for o in remote if o.get('sessionId'))}/{len(remote)}",
        "remote_user": f"{sum(1 for o in remote if o.get('userId'))}/{len(remote)}",
        "dangling_parents": sum(1 for o in rows if o.get("parentObservationId") and o["parentObservationId"] not in ids),
        "sessions": ", ".join(sorted({str(o.get("sessionId")) for o in rows})),
    }


def measure_live(mode: str) -> dict:
    offline = measure(mode)
    run = uuid.uuid4().hex[:6]
    start = datetime.now(timezone.utc) - timedelta(seconds=2)
    _run_processes(mode, _env(None, run))
    end = datetime.now(timezone.utc) + timedelta(seconds=2)
    live = analyse_live(read_back(start, end, expect=offline["spans"]))
    keys = ("traces", "logical_roots", "remote_session", "remote_user", "dangling_parents")
    return {**live, "run": run, "offline": offline,
            "agrees": all(str(live[k]) == str(offline[k]) for k in keys)}


COLUMNS = [("mode", "Mode"), ("traces", "Traces"), ("logical_roots", "Logical roots"),
           ("remote_session", "Remote obs. with session"), ("remote_user", "…with user"),
           ("remote_trace_name", "Remote trace name"), ("remote_env", "Remote environment"),
           ("dangling_parents", "Dangling parents"), ("root_names", "Roots")]


def to_markdown(results: list[dict]) -> str:
    head = "| " + " | ".join(c[1] for c in COLUMNS) + " |\n|" + "---|" * len(COLUMNS) + "\n"
    return head + "\n".join("| " + " | ".join(f"`{r[k]}`" if k == "mode" else str(r[k]) for k, _ in COLUMNS) + " |"
                            for r in results) + "\n"


LIVE_COLUMNS = [("mode", "Mode"), ("traces", "Traces"), ("logical_roots", "Server roots (`isRootObservation`)"),
                ("remote_session", "Remote obs. with session"), ("remote_user", "…with user"),
                ("dangling_parents", "Dangling parents"), ("root_names", "Roots"), ("agrees", "Matches offline")]


def to_markdown_live(results: list[dict]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from handoff import config

    head = (f"Measured {stamp} against `{config.LANGFUSE_BASE_URL}` (environment `{LIVE_ENV}`), read back through "
            "`GET /api/public/v2/observations`.\n\n")
    head += "| " + " | ".join(c[1] for c in LIVE_COLUMNS) + " |\n|" + "---|" * len(LIVE_COLUMNS) + "\n"
    def cell(r, k):
        if k == "mode":
            return f"`{r[k]}`"
        if k == "agrees":
            return "yes" if r[k] else "**no**"
        return str(r[k])
    return head + "\n".join("| " + " | ".join(cell(r, k) for k, _ in LIVE_COLUMNS) + " |" for r in results) + "\n"


def main() -> None:
    from handoff.linkage import MODES

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-one", choices=MODES)
    ap.add_argument("--modes", nargs="*", default=list(MODES))
    ap.add_argument("--markdown", type=Path)
    ap.add_argument("--live", action="store_true", help="also run against the Langfuse project in the demo .env")
    args = ap.parse_args()
    if args.run_one:
        asyncio.run(run_one_turn(args.run_one))
        return
    results = []
    for mode in args.modes:
        if args.live:
            r = {"mode": mode, **measure_live(mode)}
            print(f"{mode:14s} traces={r['traces']} server_roots={r['logical_roots']} "
                  f"remote_session={r['remote_session']} dangling={r['dangling_parents']} "
                  f"matches_offline={r['agrees']}  [{r['root_names']}]  run={r['run']}", flush=True)
        else:
            r = {"mode": mode, **measure(mode)}
            print(f"{mode:14s} traces={r['traces']} roots={r['logical_roots']} remote_session={r['remote_session']} "
                  f"remote_env={r['remote_env']} dangling={r['dangling_parents']}  [{r['root_names']}]", flush=True)
        results.append(r)
    md = to_markdown_live(results) if args.live else to_markdown(results)
    print("\n" + md)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(md)


if __name__ == "__main__":
    main()
