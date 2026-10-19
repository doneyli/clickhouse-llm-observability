"""
Production-like traffic: each claim runs as an examiner would see it — the recommendation (stops at the
examiner interrupt), then the simulated examiner's review (resume: approve or override, then the letter),
sometimes an examiner question. One claim = one session with 2–3 traces.

  --scenario baseline   golden + adversarial claims on the current release and production prompts
  --scenario canary     a 10% canary of the 'concise' candidate (claims-sop-rules label staging)
  --scenario release-1.4.0   NY/CA/TX/FL/IL claims on the release with the retriever bug (F2)
  --scenario adversarial     the 12 adversarial claims only
  --scenario service         the 22 annuity service requests (retirement / annuities)
  --scenario service-canary  replacements on the 'process-first' annuity-service-rules candidate (label staging)
  --limit N  --concurrency N

    .venv/bin/python scripts/generate_traffic.py --scenario baseline
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import claimsdb, config, graph, servicing  # noqa: E402
from claims.examiner import simulate  # noqa: E402

QUESTIONS = ["Why is this claim pending, and what exactly do I need to request?",
             "Which SOP says the commission must be charged back, and how much?",
             "Does the contestability review block the whole payment or only part of it?",
             "Is the state interest rule applied correctly here?",
             "Why does the community property rule apply to this policy?"]


async def one(c: dict, sem, *, labels: dict, release, tags: list, ask: bool) -> dict:
    async with sem:
        try:
            rec = await graph.run_claim(c["claim_id"], mode="workbench", labels=labels, release=release, tags=tags)
            review = simulate(c, rec)
            await graph.resume_claim(c["claim_id"], rec["thread_id"], review, labels=labels)
            if ask:
                await graph.ask_assistant(c["claim_id"], rec["thread_id"], random.Random(c["claim_id"]).choice(QUESTIONS),
                                          examiner_id=review["examiner_id"], labels=labels)
            return {"claim_id": c["claim_id"], "ok": True, "override": review["action"] != "approve",
                    "guard": (rec.get("guard") or {}).get("triggered")}
        except Exception as exc:  # noqa: BLE001 — keep the batch going; the trace has the error
            return {"claim_id": c["claim_id"], "ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def one_service(r: dict, sem, *, labels: dict, tags: list) -> dict:
    async with sem:
        try:
            rec = await servicing.run_service(r["request_id"], mode="workbench", labels=labels, tags=tags)
            review = simulate(r, rec)
            await servicing.resume_service(r["request_id"], rec["thread_id"], review, labels=labels)
            return {"claim_id": r["request_id"], "ok": True, "override": review["action"] != "approve",
                    "guard": (rec.get("guard") or {}).get("triggered")}
        except Exception as exc:  # noqa: BLE001
            return {"claim_id": r["request_id"], "ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def main_async(a) -> int:
    if a.scenario.startswith("service"):
        reqs = list(claimsdb.load_service().values())
        labels, tags = {}, [f"traffic:{a.scenario}"]
        if a.scenario == "service-canary":
            reqs = [r for r in reqs if r["request_type"] == "replacement"]
            labels = {servicing.RULES_PROMPT: "staging"}
            tags.append("canary")
        if a.limit:
            reqs = reqs[:a.limit]
        sem = asyncio.Semaphore(a.concurrency)
        res = await asyncio.gather(*[one_service(r, sem, labels=labels, tags=tags) for r in reqs])
        config.flush()
        ok = [r for r in res if r["ok"]]
        print(f"{a.scenario}: {len(ok)}/{len(res)} requests · overrides {sum(r['override'] for r in ok)} · "
              f"guard triggered {sum(bool(r['guard']) for r in ok)}")
        for r in res:
            if not r["ok"]:
                print("  ERROR", r["claim_id"], r["error"])
        return 0 if ok else 1
    claims = list(claimsdb.load().values())
    labels, release, tags = {}, None, [f"traffic:{a.scenario}"]
    if a.scenario == "baseline":
        pick = claims
    elif a.scenario == "canary":
        rng = random.Random(42)
        pick = [c for c in claims if c["_set"] == "golden" and len(c["expected"]["rule_ids"]) >= 4]
        pick = rng.sample(pick, min(len(pick), a.limit or 12))
        labels = {"claims-sop-rules": "staging"}
        tags.append("canary")
    elif a.scenario == "adversarial":
        pick = [c for c in claims if c["_set"] == "adversarial"]
    else:
        release = "claims-1.4.0"
        pick = [c for c in claims if c["_set"] == "golden" and c["state"] in ("NY", "CA", "TX", "FL", "IL")
                and c["line"] == "individual_life"]
    if a.limit:
        pick = pick[:a.limit]
    sem = asyncio.Semaphore(a.concurrency)
    res = await asyncio.gather(*[one(c, sem, labels=labels, release=release, tags=tags, ask=(i % 7 == 0))
                                 for i, c in enumerate(pick)])
    config.flush()
    ok = [r for r in res if r["ok"]]
    print(f"{a.scenario}: {len(ok)}/{len(res)} claims · overrides {sum(r['override'] for r in ok)} · "
          f"guard triggered {sum(bool(r['guard']) for r in ok)}")
    for r in res:
        if not r["ok"]:
            print("  ERROR", r["claim_id"], r["error"])
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="baseline", choices=["baseline", "canary", "release-1.4.0", "adversarial", "service", "service-canary"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=6)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
