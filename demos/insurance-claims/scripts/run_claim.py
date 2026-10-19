"""
Run one claim through the graph and print what happened, next to what should have happened.

    .venv/bin/python scripts/run_claim.py CLM-2026-00042
    .venv/bin/python scripts/run_claim.py CLM-2026-00042 --sop-label staging      # the F1 candidate
    CLAIMS_RELEASE=claims-1.4.0 .venv/bin/python scripts/run_claim.py CLM-2026-10102   # the F2 retriever bug

Needs ./scripts/run_services.sh. Auto mode: the examiner is simulated from the oracle (approves when
the recommendation is right, overrides otherwise), inside the same trace.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import claimsdb, config, graph  # noqa: E402
from claims.examiner import simulate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("claim_id")
    ap.add_argument("--sop-label", default="production")
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-examiner", action="store_true", help="approve whatever is recommended")
    a = ap.parse_args()
    claim = claimsdb.get(a.claim_id)
    exp = claim["expected"]

    async def go():
        examiner = None if a.no_examiner else "simulate"
        res = await graph.run_claim(a.claim_id, labels={"claims-sop-rules": a.sop_label}, model=a.model,
                                    examiner=None, tags=["cli"])
        if examiner:
            review = simulate(claim, res)
            graph.record_review(res["trace_id"], res["root_id"], res["recommendation"], review)
            res["review"] = review
        return res

    res = asyncio.run(go())
    config.flush()
    got = set(res["rules"])
    want = set(exp["rule_ids"])
    print(f"{a.claim_id} · {claim['scenario']}")
    print(f"  recommendation {res['recommendation']}  (expected {exp['decision']})")
    print(f"  rules found    {sorted(got)}")
    print(f"  missed         {sorted(want - got) or '-'}   extra {sorted(got - want) or '-'}")
    print(f"  guard          {res['guard']}")
    print(f"  calculation    {res['calculation']}  (expected gross {exp['gross_benefit']}, interest {exp['interest_due']})")
    if res.get("review"):
        print(f"  examiner       {json.dumps(res['review'])}")
    print(f"  cost ≈ ${res['cost_usd']:.4f} · prompts {res['prompt_versions']}")
    print(f"  trace   {res['trace_url']}")
    print(f"  session {res['session_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
