"""
Judge vs examiner: does the `rule-completeness` LLM judge agree with the SME label `sme-rule-set-complete` on
the same observations? Prints the confusion matrix, agreement and Cohen's kappa. A judge is trusted on a
dashboard only once it agrees with the examiners well enough.

    .venv/bin/python scripts/judge_calibration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import lfread  # noqa: E402

SINCE = "2026-10-01T00:00:00Z"


def by_obs(name: str) -> dict[str, dict]:
    out = {}
    for s in lfread.paged("/api/public/v3/scores", {"name": name, "fields": "core,subject", "fromTimestamp": SINCE}):
        oid = (s.get("subject") or {}).get("id")
        if oid:
            out[oid] = s
    return out


def b(v) -> bool:
    return v in (1, 1.0, True)


def main() -> int:
    judge, sme = by_obs("rule-completeness"), by_obs("sme-rule-set-complete")
    both = [o for o in sme if o in judge]
    if not both:
        print("no observation has both a judge score and an SME label yet (scripts/fill_annotation_queue.py)")
        return 1
    tt = sum(b(judge[o]["value"]) and b(sme[o]["value"]) for o in both)
    ff = sum(not b(judge[o]["value"]) and not b(sme[o]["value"]) for o in both)
    tf = sum(b(judge[o]["value"]) and not b(sme[o]["value"]) for o in both)   # judge says complete, SME says missed
    ft = sum(not b(judge[o]["value"]) and b(sme[o]["value"]) for o in both)
    n = len(both)
    po = (tt + ff) / n
    pj, ps = (tt + tf) / n, (tt + ft) / n
    pe = pj * ps + (1 - pj) * (1 - ps)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    print(f"{n} observations with both labels")
    print("                     SME complete   SME missed a rule")
    print(f"judge complete       {tt:>12}   {tf:>17}")
    print(f"judge incomplete     {ft:>12}   {ff:>17}")
    print(f"agreement {po:.2f} · Cohen's kappa {kappa:.2f}")
    if tf:
        print(f"\n{tf} case(s) where the judge said complete and the SME found a missed rule — the judge's blind spot "
              "(it only sees the retrieved SOP text). This is why retrieval-coverage and the guard are deterministic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
