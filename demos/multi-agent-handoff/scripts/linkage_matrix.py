"""
Measure what each linkage mode does to a cross-process handoff (SPEC §6, question 2).

For every mode this starts the billing and tech-support A2A services as separate processes,
runs one customer turn from a third process (support-turn → concierge → handoff →
tech-support → handoff → billing), and inspects what each process's Langfuse SDK exported.

    .venv/bin/python scripts/linkage_matrix.py            # offline: spans captured to JSONL
    .venv/bin/python scripts/linkage_matrix.py --markdown docs/LINKAGE_MATRIX.md

Offline mode needs no Langfuse server and no LLM key: the agents are deterministic stubs,
and the spans are exactly what the SDK would send (after its export filter, app-root
marking and baggage handling). "Logical roots" follows Langfuse's definition: an
observation with no parent, or one the SDK marked as an application root.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

APP_ROOT = "langfuse.internal.is_app_root"
PORTS = {"billing": "19310", "tech-support": "19311"}


def _env(capture: Path) -> dict:
    env = {**os.environ, "HANDOFF_CAPTURE_DIR": str(capture), "PYTHONPATH": str(DEMO),
           "BILLING_PORT": PORTS["billing"], "TECH_SUPPORT_PORT": PORTS["tech-support"]}
    for k in list(env):
        if k.startswith("LANGFUSE_"):
            env.pop(k)
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
    session, user = f"matrix-{mode}", "cust-1042"
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


def measure(mode: str) -> dict:
    from handoff.capture import read_capture

    with tempfile.TemporaryDirectory(prefix=f"linkage-{mode}-") as tmp:
        capture = Path(tmp)
        env = _env(capture)
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
        return analyse(read_capture(capture))


COLUMNS = [("mode", "Mode"), ("traces", "Traces"), ("logical_roots", "Logical roots"),
           ("remote_session", "Remote obs. with session"), ("remote_user", "…with user"),
           ("remote_trace_name", "Remote trace name"), ("remote_env", "Remote environment"),
           ("dangling_parents", "Dangling parents"), ("root_names", "Roots")]


def to_markdown(results: list[dict]) -> str:
    head = "| " + " | ".join(c[1] for c in COLUMNS) + " |\n|" + "---|" * len(COLUMNS) + "\n"
    return head + "\n".join("| " + " | ".join(f"`{r[k]}`" if k == "mode" else str(r[k]) for k, _ in COLUMNS) + " |"
                            for r in results) + "\n"


def main() -> None:
    from handoff.linkage import MODES

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-one", choices=MODES)
    ap.add_argument("--modes", nargs="*", default=list(MODES))
    ap.add_argument("--markdown", type=Path)
    args = ap.parse_args()
    if args.run_one:
        asyncio.run(run_one_turn(args.run_one))
        return
    results = []
    for mode in args.modes:
        r = {"mode": mode, **measure(mode)}
        results.append(r)
        print(f"{mode:14s} traces={r['traces']} roots={r['logical_roots']} remote_session={r['remote_session']} "
              f"remote_env={r['remote_env']} dangling={r['dangling_parents']}  [{r['root_names']}]", flush=True)
    md = to_markdown(results)
    print("\n" + md)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(md)


if __name__ == "__main__":
    main()
