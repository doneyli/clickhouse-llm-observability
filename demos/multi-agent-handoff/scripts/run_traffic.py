"""
Production-like traffic: simulated customers hold whole conversations (SPEC §17).

    .venv/bin/python scripts/run_traffic.py                       # the 12 conversation goals, a2a
    .venv/bin/python scripts/run_traffic.py --only conv-01 conv-07 --topology local
    HANDOFF_SLIM_CONTEXT=1 .venv/bin/python scripts/run_traffic.py --only conv-02   # fault F2

Each customer message is one `support-turn` trace in the `production` environment; a
conversation is one session. So everything online runs as it would in production: the
code evaluators and judges on every handoff and turn, the dashboards. Per conversation it
also writes the conversation-level scores on the session (turns, transfers, how it ended,
and whether the ledger matches the goal: there is no session-target judge), and sends every
conversation with a transfer to the "Transferred conversations" review queue.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import config, datasets  # noqa: E402
from handoff.conversation import run_conversation, score_session  # noqa: E402
from handoff.linkage import DEFAULT_MODE, MODES  # noqa: E402

QUEUE = "Transferred conversations"


def _api(method: str, path: str, **kw) -> dict:
    auth = (config.setting("LANGFUSE_PUBLIC_KEY"), config.setting("LANGFUSE_SECRET_KEY"))
    r = httpx.request(method, f"{config.LANGFUSE_BASE_URL}{path}", auth=auth, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def queue_id() -> str | None:
    queues = _api("GET", "/api/public/annotation-queues", params={"limit": 100}).get("data") or []
    return next((q["id"] for q in queues if q["name"] == QUEUE), None)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", choices=["single", "local", "a2a"], default="a2a")
    ap.add_argument("--linkage", choices=MODES, default=DEFAULT_MODE)
    ap.add_argument("--label", default=None, help="prompt label (default: production)")
    ap.add_argument("--only", nargs="*", help="conversation ids, e.g. conv-01 conv-07")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    items = [i for i in datasets.conversation_items()
             if not args.only or i["metadata"]["item"] in args.only]
    qid = queue_id()
    if qid is None:
        print(f'(no "{QUEUE}" queue: run scripts/seed_evals.py --queue to send transfers for review)')
    sem = asyncio.Semaphore(args.concurrency)

    async def one(item):
        async with sem:
            res = await run_conversation(topology=args.topology, linkage=args.linkage, label=args.label,
                                         persona=item["input"], max_turns=item["expected_output"]["max_turns"],
                                         scenario=item["metadata"]["item"], traced_simulator=False)
        scores = dict((n, v) for n, v, _, _ in score_session(res, item["expected_output"]))
        queued = False
        if qid and res.transfers:
            _api("POST", f"/api/public/annotation-queues/{qid}/items",
                 json={"objectId": res.session_id, "objectType": "SESSION"})
            queued = True
        print(f"{item['metadata']['item']}  {res.session_id}  turns={res.turns} transfers={res.transfers} "
              f"ended={res.ended_by} resolution={'ok' if scores.get('resolution-correct') else 'WRONG'}"
              f"{'  → review queue' if queued else ''}  owners={' > '.join(res.owners)}", flush=True)
        return res

    results = await asyncio.gather(*[one(i) for i in items])
    config.flush()
    print(f"\n{len(results)} conversations, {sum(r.turns for r in results)} turns, "
          f"{sum(1 for r in results if r.transfers)} with a transfer")


if __name__ == "__main__":
    asyncio.run(main())
