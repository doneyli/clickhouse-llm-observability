"""
Run one scripted conversation through the support desk.

    .venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a --linkage baggage
    .venv/bin/python scripts/run_scenario.py hero-outage-credit --topology local
    .venv/bin/python scripts/run_scenario.py hero-outage-credit --topology single
    .venv/bin/python scripts/run_scenario.py --list

`a2a` needs the agent services running (scripts/run_services.sh). With HANDOFF_CAPTURE_DIR
set, spans are written there instead of Langfuse; `scripts/show_trace.py <dir>` renders them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

from handoff import config  # noqa: E402
from handoff.linkage import MODES  # noqa: E402


def scenarios() -> dict:
    return {s["id"]: s for s in json.loads((config.DATA_DIR / "scenarios.json").read_text())}


async def run(scenario: dict, topology: str, linkage: str, label: str | None) -> dict:
    from handoff.orchestrator import Conversation, Orchestrator

    orch = Orchestrator(topology=topology, linkage=linkage, prompt_label=label)
    conv = Conversation(customer_id=scenario["customer_id"], topology=topology, scenario=scenario["id"])
    print(f"session {conv.session_id}  customer {conv.customer_id}  topology={topology}"
          + (f" linkage={linkage}" if topology == "a2a" else ""))
    turns = []
    try:
        for msg in scenario["turns"]:
            r = await orch.turn(conv, msg)
            print(f"\n  customer: {msg}\n  {r.replied_by}: {r.reply}\n"
                  f"     path={'>'.join(r.agent_path)}  owner_after={r.owner_after}  trace={r.trace_id}")
            turns.append({"message": msg, "reply": r.reply, "replied_by": r.replied_by, "path": r.agent_path,
                          "owner_after": r.owner_after, "trace_id": r.trace_id})
    finally:
        await orch.aclose()
        config.flush()
    print(f"\n  effects: {json.dumps(conv.effects)}")
    return {"session_id": conv.session_id, "turns": turns, "effects": conv.effects}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="hero-outage-credit")
    ap.add_argument("--topology", choices=["single", "local", "a2a"], default="a2a")
    ap.add_argument("--linkage", choices=MODES, default="baggage")
    ap.add_argument("--label", default=None, help="Langfuse prompt label (default: production)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for sid, s in scenarios().items():
            print(f"{sid:28s} {s['customer_id']}  {s['turns'][0]}")
        return
    asyncio.run(run(scenarios()[args.scenario], args.topology, args.linkage, args.label))


if __name__ == "__main__":
    main()
