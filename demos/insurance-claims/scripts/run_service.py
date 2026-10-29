"""
Run one annuity service request (retirement / annuities) end to end and compare with the oracle.

    .venv/bin/python scripts/run_service.py SR-2026-00077                       # the NY senior replacement
    .venv/bin/python scripts/run_service.py SR-2026-00077 --rules-label staging # the 'process-first' candidate

The case manager is simulated from the oracle (approve when right, override otherwise) — a resume of the
LangGraph interrupt, i.e. a second trace in the request's session.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import claimsdb, config, servicing  # noqa: E402
from claims.examiner import simulate  # noqa: E402


async def main_async(a) -> int:
    r = claimsdb.get_service(a.request_id)
    exp = r["expected"]
    res = await servicing.run_service(a.request_id, mode="workbench", labels={servicing.RULES_PROMPT: a.rules_label},
                                      tags=["cli"])
    review = simulate(r, res)
    fin = await servicing.resume_service(a.request_id, res["thread_id"], review)
    config.flush()
    got, want = set(res["rules"]), set(exp["rule_ids"])
    print(f"{a.request_id} · {r['scenario']}")
    print(f"  recommendation {res['recommendation']}  (expected {exp['decision']})  → final {fin['final_decision']}")
    print(f"  rules found    {sorted(got)}")
    print(f"  missed         {sorted(want - got) or '-'}   extra {sorted(got - want) or '-'}")
    print(f"  guard          {res['guard']}")
    print(f"  surrender charge {res['calculation']['surrender_charge']} (expected {exp['surrender_charge']})")
    print(f"  trace   {res['trace_url']}")
    print(f"  session {res['session_url']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request_id")
    ap.add_argument("--rules-label", default="production")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
