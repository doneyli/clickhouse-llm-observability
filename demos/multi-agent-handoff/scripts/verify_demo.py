"""
Is the demo ready, and does every seeded fault trip the layer that should catch it?

    .venv/bin/python scripts/verify_demo.py              # readiness only: no model calls, ~10 s
    .venv/bin/python scripts/verify_demo.py --faults     # + one live turn per fault (F1–F5), ~3–5 min

Readiness checks the Langfuse project and the local services: prompts and fault labels,
datasets, evaluators active and their rules enabled, the review queue, the A2A services
and their agent cards, recent production traffic.

`--faults` runs one customer turn per fault with its switch on (SPEC §10), plus a clean
baseline, reads each trace and its online scores back from Langfuse, and checks that the
catching layer tripped. It also reports what the "missed by" layer saw, because that is
the lesson of each fault. Judges are probabilistic, so a judge miss is a WARN, not a FAIL.
Exit 1 if any required check fails. Needs the A2A services: ./scripts/run_services.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

from handoff import config  # noqa: E402

FAIL, WARN = [], []
HERO = "My home internet has been down since last night. I want money back for it."

# check → the turn to run, with its fault switch
CHECKS = {
    "baseline": {"customer": "cust-1042", "message": HERO},
    "F1": {"customer": "cust-1042", "message": HERO, "label": "fault-f1"},
    "F2": {"customer": "cust-1016", "env": {"HANDOFF_SLIM_CONTEXT": "1"},
           "message": "My fiber router has a red light and nothing works. I already restarted it twice. "
                      "Please send a technician."},
    "F3": {"customer": "cust-1013", "label": "fault-f3",
           "message": "My internet was out for days last week and my bill looks wrong. What's going on?"},
    "F4": {"customer": "cust-1042", "message": HERO, "linkage": "traceparent"},
    "F5": {"customer": "cust-1011", "message": "How much is my bill this month?",
           "env": {"BILLING_URL": "http://127.0.0.1:9"}},  # billing unreachable from the orchestrator
}
FAULT_NAMES = {"baseline": "no fault", "F1": "misroute", "F2": "context loss", "F3": "ping-pong",
               "F4": "broken propagation", "F5": "remote agent down"}


def check(ok: bool, name: str, detail: str = "", required: bool = True) -> bool:
    tag = "PASS" if ok else ("FAIL" if required else "WARN")
    print(f"[{tag}] {name}{' — ' + detail if detail else ''}", flush=True)
    if not ok:
        (FAIL if required else WARN).append(name)
    return ok


def api(path: str, **params) -> dict:
    auth = (config.setting("LANGFUSE_PUBLIC_KEY"), config.setting("LANGFUSE_SECRET_KEY"))
    r = httpx.get(f"{config.LANGFUSE_BASE_URL}{path}", params=params, auth=auth, timeout=60)
    r.raise_for_status()
    return r.json()


# --- readiness -------------------------------------------------------------------------------

def readiness() -> None:
    from handoff import datasets, faults, prompts
    from handoff.agents.registry import SPECIALISTS

    projects = api("/api/public/projects")["data"]
    check(bool(projects), "Langfuse keys", f"project {projects[0]['name'] if projects else '?'} at {config.LANGFUSE_BASE_URL}")
    for agent in prompts.PROMPT_NAMES:
        name = prompts.prompt_name(agent)
        try:
            p = api(f"/api/public/v2/prompts/{name}", label="production")
            check(True, f"prompt {name}@production", f"v{p['version']}")
        except httpx.HTTPStatusError:
            check(False, f"prompt {name}@production", "run scripts/seed_prompts.py")
    for label, texts in faults.PROMPT_FAULTS.items():
        for agent in texts:
            name = prompts.prompt_name(agent)
            try:
                p = api(f"/api/public/v2/prompts/{name}", label=label)
                check(True, f"prompt {name}@{label}", f"v{p['version']}")
            except httpx.HTTPStatusError:
                check(False, f"prompt {name}@{label}", "run scripts/seed_prompts.py --faults")
    lf = config.get_langfuse("verify")
    for ds, want in ((datasets.ROUTING, 40), (datasets.CONVERSATIONS, 12)):
        try:  # the same read the experiments use
            active = [i for i in lf.get_dataset(ds).items if str(i.status).upper().endswith("ACTIVE")]
            promoted = [i for i in active if (i.metadata or {}).get("source") == "production"]
            seeded = len(active) - len(promoted)
            check(seeded == want, f"dataset {ds}", f"{seeded} seeded items (want {want})"
                  + (f" + {len(promoted)} promoted from production" if promoted else ""))
        except Exception as exc:  # noqa: BLE001
            check(False, f"dataset {ds}", f"{type(exc).__name__}: run scripts/seed_datasets.py")
    evaluators = {e["name"]: e for e in api("/api/public/v2/evaluators", limit=100)["data"]}
    rules = {r["name"]: r for r in api("/api/public/v2/evaluation-rules", limit=100)["data"]}
    for name in ("handoff-context-complete", "handoff-loop", "handoff-appropriate", "customer-resolved"):
        e = evaluators.get(name)
        if not e:
            check(False, f"evaluator {name}", "run scripts/seed_evals.py --judges --queue")
            continue
        why = f"{e.get('pausedReason')}: {e.get('pausedMessage')}" if e.get("status") == "paused" else e["type"]
        check(e.get("status") == "active", f"evaluator {name}", why)
        check(bool(rules.get(name, {}).get("enabled")), f"rule {name}", "enabled" if rules.get(name) else "missing")
    names = {q["name"] for q in api("/api/public/annotation-queues", limit=100)["data"]}
    for queue in ("Transferred conversations", "Flagged handoffs"):
        check(queue in names, f"review queue '{queue}'", "" if queue in names else "run scripts/seed_evals.py --queue")
    boards = {d["name"] for d in api("/api/public/unstable/dashboards", limit=100)["data"]}
    for board in ("Handoffs — quality", "Handoffs — agents"):
        check(board in boards, f"dashboard '{board}'", "" if board in boards else "run scripts/seed_dashboards.py")
    try:  # the loop moves concierge@production; a rehearsal stopped half-way leaves the F1 bug live
        live = api("/api/public/v2/prompts/concierge", label="production")
        bug = live["prompt"] == faults.F1_CONCIERGE
        check(not bug, "concierge@production is not the F1 bug", f"v{live['version']}"
              + (": the loop is mid-way. scripts/loop.py promote (the fix) or scripts/loop.py reset" if bug else ""),
              required=False)
    except httpx.HTTPStatusError:
        pass
    for agent in SPECIALISTS:
        try:
            card = httpx.get(f"{config.agent_url(agent)}/.well-known/agent-card.json", timeout=3).json()
            check(True, f"A2A service {agent}", f"{config.agent_url(agent)} · card v{card.get('version')}")
        except (httpx.HTTPError, ValueError):
            check(False, f"A2A service {agent}", "start them: ./scripts/run_services.sh")
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    turns = api("/api/public/v2/observations", name="support-turn", environment="production",
                fromStartTime=since, fields="core", limit=1000)["data"]
    check(len(turns) > 0, "production traffic in the last 24 h", f"{len(turns)} turns"
          + ("" if turns else ": run scripts/run_traffic.py"), required=False)


# --- faults ------------------------------------------------------------------------------------

async def run_turn(name: str) -> dict:
    """One customer turn for a check (runs in its own process, with the check's env)."""
    from handoff.orchestrator import Conversation, Orchestrator

    c = CHECKS[name]
    orch = Orchestrator(topology="a2a", linkage=c.get("linkage", "baggage"), prompt_label=c.get("label"))
    conv = Conversation(customer_id=c["customer"], topology="a2a", scenario=f"verify-{name}")
    try:
        r = await orch.turn(conv, c["message"])
    finally:
        await orch.aclose()
        config.flush()
    return {"trace_id": r.trace_id, "session_id": conv.session_id, "first_handoff": r.first_handoff,
            "path": r.agent_path, "reply": r.reply, "effects": r.effects,
            "handoffs": [{k: h.get(k) for k in ("kind", "from", "to", "state")} for h in r.handoffs]}


def _spawn(name: str) -> dict:
    c = CHECKS[name]
    env = {k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE_")}
    env.update(c.get("env", {}))
    out = subprocess.run([sys.executable, __file__, "--run-turn", name], cwd=DEMO, env=env,
                         capture_output=True, text=True, timeout=400)
    line = next((ln for ln in reversed(out.stdout.splitlines()) if ln.startswith("{")), None)
    if out.returncode != 0 or not line:
        return {"error": (out.stderr or out.stdout)[-1200:]}
    return json.loads(line)


def _scores(trace_id: str, since: str, names: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {n: [] for n in names}
    for n in names:
        for s in api("/api/public/v3/scores", name=n, fromTimestamp=since, limit=100,
                     fields="core,subject,details")["data"]:
            if (s.get("subject") or {}).get("traceId") == trace_id:
                out[n].append(s)
    return out


def _observations(trace_id: str) -> list[dict]:
    return api("/api/public/v2/observations", traceId=trace_id, fields="core,basic,metadata,io", limit=200)["data"]


def _wait(trace_id: str, since: str, needs: dict[str, int], timeout: float) -> dict[str, list[dict]]:
    """Poll until each score name has at least `needs[name]` scores on the trace, or time out."""
    deadline, got = time.time() + timeout, {}
    while time.time() < deadline:
        got = _scores(trace_id, since, list(needs))
        if all(len(got[n]) >= k for n, k in needs.items()):
            return got
        time.sleep(10)
    return got


def _vals(scores: list[dict]) -> list:
    return [s.get("value") if s.get("value") is not None else s.get("stringValue") for s in scores]


def faults(wait: float) -> list[tuple]:
    since = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    print("\nrunning one live turn per check (in parallel) …", flush=True)
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        runs = dict(zip(CHECKS, pool.map(_spawn, CHECKS)))
    table = []
    for name, run in runs.items():
        print(f"\n— {name} ({FAULT_NAMES[name]})", flush=True)
        if "error" in run:
            check(False, f"{name}: turn ran", run["error"].strip().splitlines()[-1] if run["error"].strip() else "?")
            continue
        tid = run["trace_id"]
        print(f"  trace {tid}  path {'>'.join(run['path'])}  first handoff {run['first_handoff']}", flush=True)
        n_handoffs = len([h for h in run["handoffs"]])
        if name == "baseline":
            got = _wait(tid, since, {"handoff-context-complete": 1, "handoff-loop": 1, "handoff-succeeded": 1,
                                     "handoff-appropriate": 1, "customer-resolved": 1}, wait)
            check(run["first_handoff"] == "tech-support", "baseline: routed to tech-support", str(run["first_handoff"]))
            check(all(_vals(got["handoff-context-complete"])), "baseline: handoff-context-complete passes",
                  str(_vals(got["handoff-context-complete"])))
            check(_vals(got["handoff-loop"]) == [False], "baseline: no loop", str(_vals(got["handoff-loop"])))
            check(all(_vals(got["handoff-appropriate"])) and got["handoff-appropriate"],
                  "baseline: judge agrees with every handoff", str(_vals(got["handoff-appropriate"])), required=False)
            obs = _observations(tid)
            roots = [o for o in obs if o.get("isRootObservation")]
            check(len(roots) == 1 and all(o.get("sessionId") for o in obs), "baseline: one root, session on every row",
                  f"{len(roots)} root(s), {sum(1 for o in obs if not o.get('sessionId'))} rows without session")
            table.append((name, "—", "clean", "—", "—"))
        elif name == "F1":
            check(run["first_handoff"] == "billing", "F1 active: concierge sent the outage credit to billing",
                  str(run["first_handoff"]))
            got = _wait(tid, since, {"handoff-appropriate": max(1, n_handoffs), "customer-resolved": 1}, wait)
            caught = any(v is False for v in _vals(got["handoff-appropriate"]))
            check(caught, "F1 caught: handoff-appropriate judge flags a handoff",
                  str(_vals(got["handoff-appropriate"])), required=False)
            from handoff import evals
            offline = evals.handoff_target_correct(output={"topology": "a2a", "first_handoff": run["first_handoff"]},
                                                   expected_output={"first_handoff": "tech-support"})[0]
            check(offline.value == 0, "F1 caught: handoff-target-correct (experiment evaluator) = 0", offline.comment)
            resolved = _vals(got["customer-resolved"])
            table.append((name, "handoff-appropriate judge · handoff-target-correct",
                          f"judge {_vals(got['handoff-appropriate'])} · target 0",
                          "customer-resolved (end-to-end judge)", f"{resolved}"))
        elif name == "F2":
            got = _wait(tid, since, {"handoff-context-complete": 1}, wait)
            missing = [s for s in got["handoff-context-complete"] if s.get("value") is False]
            check(bool(missing) and "line_id" in (missing[0].get("comment") or ""),
                  "F2 caught: handoff-context-complete names line_id", missing[0].get("comment") if missing else
                  str(_vals(got["handoff-context-complete"])))
            techs = run["effects"].get("technicians") or []
            table.append((name, "handoff-context-complete (code evaluator)",
                          missing[0].get("comment") if missing else "not flagged",
                          "the outcome on a lucky run", f"technician booked: {bool(techs)}"))
        elif name == "F3":
            got = _wait(tid, since, {"handoff-loop": 1, "handoff-appropriate": 1}, wait)
            loop = got["handoff-loop"]
            check(bool(loop) and loop[0].get("value") is True, "F3 caught: handoff-loop flags the turn",
                  loop[0].get("comment") if loop else "no score")
            per_hop = _vals(got["handoff-appropriate"])
            table.append((name, "handoff-loop (code evaluator)", loop[0].get("comment") if loop else "not flagged",
                          "per-hop judge (each hop looks reasonable)",
                          f"{sum(1 for v in per_hop if v)}/{len(per_hop)} hops judged appropriate"))
        elif name == "F4":
            time.sleep(20)  # remote spans of a split trace arrive on their own schedule
            obs = _observations(tid)
            roots = [o for o in obs if o.get("isRootObservation")]
            no_session = [o for o in obs if not o.get("sessionId")]
            check(len(roots) > 1 and bool(no_session), "F4 caught: trace integrity (extra roots, rows without session)",
                  f"{len(roots)} roots ({', '.join(sorted(o['name'] for o in roots))}), "
                  f"{len(no_session)}/{len(obs)} rows without session")
            got = _wait(tid, since, {"customer-resolved": 1, "handoff-context-complete": 1}, wait)
            handoff_obs = [o for o in obs if o["name"] == "handoff"]
            table.append((name, "trace integrity (verify_demo, dashboard)",
                          f"{len(roots)} roots, {len(no_session)} rows without session",
                          "quality scores (answers still correct)",
                          f"customer-resolved {_vals(got['customer-resolved'])}; "
                          f"{len(got['handoff-context-complete'])}/{len(handoff_obs)} handoffs scored online"))
        elif name == "F5":
            obs = _observations(tid)
            failed = [o for o in obs if o["name"] == "handoff" and o.get("level") == "ERROR"]
            check(bool(failed), "F5 caught: handoff observation level ERROR",
                  (failed[0].get("statusMessage") or "")[:100] if failed else "no ERROR handoff")
            got = _wait(tid, since, {"handoff-succeeded": 1}, wait)
            check(any(v is False for v in _vals(got["handoff-succeeded"])), "F5 caught: handoff-succeeded = false",
                  str(_vals(got["handoff-succeeded"])))
            table.append((name, "handoff ERROR level · handoff-succeeded", f"{len(failed)} failed handoff(s)",
                          "none: graceful degradation", run["reply"][:80].replace("\n", " ")))
    return table


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", action="store_true", help="also run one live turn per fault (costs a few cents)")
    ap.add_argument("--wait", type=float, default=300, help="seconds to wait for online scores per check")
    ap.add_argument("--run-turn", choices=list(CHECKS), help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.run_turn:
        print(json.dumps(asyncio.run(run_turn(args.run_turn))))
        return
    readiness()
    if args.faults:
        if any(n.startswith("A2A service") for n in FAIL):
            check(False, "faults", "skipped: the A2A services are not running")
        else:
            table = faults(args.wait)
            print("\n| Fault | Caught by | Observed | Missed by | Observed |\n|---|---|---|---|---|")
            for row in table:
                print("| " + " | ".join(str(c) for c in row) + " |")
    print(f"\n{len(FAIL)} required failure(s), {len(WARN)} warning(s)")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
