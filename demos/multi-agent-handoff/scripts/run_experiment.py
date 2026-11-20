"""
Run a dataset through one topology as a Langfuse experiment (SPEC §9, question 3).

    .venv/bin/python scripts/run_experiment.py --topology single
    .venv/bin/python scripts/run_experiment.py --topology local
    ./scripts/run_services.sh && .venv/bin/python scripts/run_experiment.py --topology a2a
    .venv/bin/python scripts/run_experiment.py --topology a2a --dataset conversations   # simulated customer

Same items, same prompts, same models, same tools: only the architecture changes. Compare
the runs in Langfuse (Datasets → handoff-routing → Runs): outcome and process scores exist
for all three; routing and trajectory scores only where there are handoffs. `local` vs.
`a2a` isolates the cost of the network hop; `single` vs. `local` the cost of the extra agents.
Each run gets a time-stamped name, because reusing a run name appends to the old run.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config, datasets  # noqa: E402
from handoff.agents.registry import AGENTS, SPECIALISTS  # noqa: E402
from handoff.conversation import run_conversation  # noqa: E402
from handoff.evals import (CONVERSATION_EVALUATORS, CONVERSATION_RUN_EVALUATORS, ITEM_EVALUATORS,  # noqa: E402
                           RUN_EVALUATORS)
from handoff.linkage import DEFAULT_MODE, MODES  # noqa: E402
from handoff.orchestrator import Conversation, Orchestrator  # noqa: E402


def _services_up() -> list[str]:
    import httpx

    down = []
    for a in SPECIALISTS:
        try:
            httpx.get(f"{config.agent_url(a)}/.well-known/agent-card.json", timeout=3).raise_for_status()
        except httpx.HTTPError:
            down.append(a)
    return down


def make_task(topology: str, linkage: str, label: str):
    async def task(*, item, **_):
        inp = item.input
        orch = Orchestrator(topology=topology, linkage=linkage, prompt_label=label)
        conv = Conversation(customer_id=inp["customer_id"], topology=topology,
                            scenario=(item.metadata or {}).get("item", "experiment"))
        started = time.perf_counter()
        try:
            r = await orch.turn(conv, inp["message"])
        finally:
            await orch.aclose()
        return {"reply": r.reply, "replied_by": r.replied_by, "agent_path": r.agent_path,
                "first_handoff": r.first_handoff,
                "handoffs": [{k: h.get(k) for k in ("kind", "from", "to", "payload", "message", "state")} for h in r.handoffs],
                "tools": r.tools, "effects": r.effects, "owner_after": r.owner_after,
                "latency_s": round(time.perf_counter() - started, 2), "topology": topology,
                "session_id": conv.session_id}
    return task


def make_conversation_task(topology: str, linkage: str, label: str):
    async def task(*, item, **_):
        started = time.perf_counter()
        res = await run_conversation(topology=topology, linkage=linkage, label=label, persona=item.input,
                                     max_turns=int(item.expected_output.get("max_turns", 6)),
                                     scenario=(item.metadata or {}).get("item", "experiment"), traced_simulator=True)
        return {**res.record(topology), "latency_s": round(time.perf_counter() - started, 2)}
    return task


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", choices=["single", "local", "a2a"], required=True)
    ap.add_argument("--dataset", choices=["routing", "conversations"], default="routing")
    ap.add_argument("--linkage", choices=MODES, default=DEFAULT_MODE)
    ap.add_argument("--label", default="production", help="prompt label for every agent")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, help="first N items only (smoke test)")
    ap.add_argument("--name", help="run name (default: topology · label · UTC time)")
    args = ap.parse_args()

    if args.topology == "a2a" and (down := _services_up()):
        sys.exit(f"A2A services not reachable: {', '.join(down)}. Start them: ./scripts/run_services.sh")
    lf = config.get_langfuse("orchestrator")
    conversations = args.dataset == "conversations"
    dataset_name = datasets.CONVERSATIONS if conversations else datasets.ROUTING
    dataset = lf.get_dataset(dataset_name)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    run_name = args.name or f"{args.topology} · {args.label} · {stamp}"
    models = {a: AGENTS[a].model for a in ("concierge", "tech-support", "billing", "single")}
    meta = {"topology": args.topology, "prompt_label": args.label, "release": config.RELEASE,
            "linkage": args.linkage if args.topology == "a2a" else "in-process",
            "models": ", ".join(f"{k}={v}" for k, v in models.items())}
    items = dataset.items[: args.limit] if args.limit else dataset.items
    print(f"{run_name}: {len(items)} items, concurrency {args.concurrency}", flush=True)
    result = lf.run_experiment(
        name=dataset_name, run_name=run_name, description=f"{args.topology} topology, prompts @{args.label}",
        data=items,
        task=(make_conversation_task if conversations else make_task)(args.topology, args.linkage, args.label),
        evaluators=CONVERSATION_EVALUATORS if conversations else ITEM_EVALUATORS,
        run_evaluators=CONVERSATION_RUN_EVALUATORS if conversations else RUN_EVALUATORS,
        max_concurrency=args.concurrency, metadata=meta)
    lf.flush()
    failed = [r for r in result.item_results if not isinstance(r.output, dict)]
    print(f"\nitems: {len(result.item_results)}  failed tasks: {len(failed)}")
    for e in result.run_evaluations:
        print(f"  {e.name:24s} {e.value}")
    url = result.experiment_url
    if url:
        print(f"\n{url}")


if __name__ == "__main__":
    main()
