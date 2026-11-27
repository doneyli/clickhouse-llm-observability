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
                "hop_cap_hit": conv.cap_hit, "session_id": conv.session_id}
    return task


def make_conversation_task(topology: str, linkage: str, label: str):
    async def task(*, item, **_):
        started = time.perf_counter()
        res = await run_conversation(topology=topology, linkage=linkage, label=label, persona=item.input,
                                     max_turns=int(item.expected_output.get("max_turns", 6)),
                                     scenario=(item.metadata or {}).get("item", "experiment"), traced_simulator=True)
        return {**res.record(topology), "latency_s": round(time.perf_counter() - started, 2)}
    return task


def run(topology: str, *, dataset: str = "routing", label: str = "production", linkage: str = DEFAULT_MODE,
        concurrency: int = 4, limit: int | None = None, name: str | None = None, quiet: bool = False):
    """One experiment run; returns the SDK's ExperimentResult. Also used by scripts/prompt_gate.py."""
    if topology == "a2a" and (down := _services_up()):
        sys.exit(f"A2A services not reachable: {', '.join(down)}. Start them: ./scripts/run_services.sh")
    lf = config.get_langfuse("orchestrator")
    conversations = dataset == "conversations"
    dataset_name = datasets.CONVERSATIONS if conversations else datasets.ROUTING
    ds = lf.get_dataset(dataset_name)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    run_name = name or f"{topology} · {label} · {stamp}"
    models = {a: AGENTS[a].model for a in ("concierge", "tech-support", "billing", "single")}
    meta = {"topology": topology, "prompt_label": label, "release": config.RELEASE,
            "linkage": linkage if topology == "a2a" else "in-process",
            "models": ", ".join(f"{k}={v}" for k, v in models.items())}
    items = [i for i in ds.items if i.status != "ARCHIVED"]
    items = items[:limit] if limit else items
    if not quiet:
        print(f"{run_name}: {len(items)} items, concurrency {concurrency}", flush=True)
    result = lf.run_experiment(
        name=dataset_name, run_name=run_name, description=f"{topology} topology, prompts @{label}",
        data=items,
        task=(make_conversation_task if conversations else make_task)(topology, linkage, label),
        evaluators=CONVERSATION_EVALUATORS if conversations else ITEM_EVALUATORS,
        run_evaluators=CONVERSATION_RUN_EVALUATORS if conversations else RUN_EVALUATORS,
        max_concurrency=concurrency, metadata=meta)
    lf.flush()
    result.items_requested = len(items)
    result.run_name_used = run_name
    return result


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
    result = run(args.topology, dataset=args.dataset, label=args.label, linkage=args.linkage,
                 concurrency=args.concurrency, limit=args.limit, name=args.name)
    failed = [r for r in result.item_results if not isinstance(r.output, dict)]
    print(f"\nitems: {len(result.item_results)}  failed tasks: {len(failed)}")
    for e in result.run_evaluations:
        print(f"  {e.name:24s} {e.value}")
    if result.experiment_url:
        print(f"\n{result.experiment_url}")


if __name__ == "__main__":
    main()
