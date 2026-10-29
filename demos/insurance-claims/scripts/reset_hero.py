"""
Reset the featured claim's session before a live demo: delete every trace in session CLM-2026-00042, then
run it once (recommendation → simulated examiner approval → letter) so a pre-run session exists as a
fallback. The presenter then processes it live in the workbench, adding a second run to the session.

    .venv/bin/python scripts/reset_hero.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import claimsdb, config, graph, lfread  # noqa: E402
from claims.examiner import simulate  # noqa: E402

HERO = "CLM-2026-00042"


async def main() -> int:
    f = [{"type": "stringOptions", "column": "sessionId", "operator": "any of", "value": [HERO]},
         {"type": "datetime", "column": "startTime", "operator": ">=", "value": "2026-01-01T00:00:00Z"}]
    ids = sorted({r["traceId"] for r in lfread.paged("/api/public/v2/observations", {"filter": json.dumps(f),
                                                                                      "fields": "core"})})
    if ids:
        config.api("DELETE", "/api/public/traces", {"traceIds": ids})
    print(f"deleted {len(ids)} traces in session {HERO}")
    c = claimsdb.get(HERO)
    rec = await graph.run_claim(HERO, mode="workbench", tags=["prerun"])
    review = simulate(c, rec)
    await graph.resume_claim(HERO, rec["thread_id"], review)
    config.flush()
    print("recommendation", rec["recommendation"], "| rules", rec["rules"])
    print("trace  ", rec["trace_url"])
    print("session", config.session_url(HERO))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
