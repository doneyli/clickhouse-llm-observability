"""
Loop step 5: check the handoff judge against the reviewers before trusting it again.

    .venv/bin/python scripts/judge_calibration.py
    .venv/bin/python scripts/judge_calibration.py --markdown     # a table for the docs

For every reviewed item in "Flagged handoffs", the reviewer's label says which agent the
handoff should have gone to; the handoff was right if that is where it went. The online
`handoff-appropriate` judge gave its own true/false on the same observation. This prints how
often they agree, Cohen's kappa (agreement beyond chance), and where they disagree, with the
judge's reason. The queue holds the judge's flags AND a sample it passed (flag_handoffs.py), so
both kinds of judge error can show up.

Small samples: a handful of items gives a direction, not a measurement. Say so when you show it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff.lfapi import paginate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flag_handoffs import JUDGE  # noqa: E402
from promote_to_dataset import reviewed  # noqa: E402


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args()
    rows = reviewed()
    if not rows:
        sys.exit("No reviewed items yet: label some in the Flagged handoffs queue (or flag_handoffs.py --simulate-reviewer)")
    judge = {}
    for s in paginate("/api/public/v3/scores", params={"name": JUDGE, "fields": "core,subject,details"}):
        sub = s.get("subject") or {}
        if sub.get("kind") == "observation":
            judge[sub["id"]] = s
    pairs, diffs = [], []
    for r in rows:
        j = judge.get(r["observation_id"])
        obs = paginate("/api/public/v2/observations", params={"traceId": r["trace_id"], "fields": "core,metadata"})
        hop = next((o for o in obs if o["id"] == r["observation_id"]), None)
        if j is None or hop is None:
            continue
        actual = hop["metadata"].get("handoff_to")
        reviewer_ok, judge_ok = r["label"] == actual, bool(j["value"])
        pairs.append((reviewer_ok, judge_ok))
        if reviewer_ok != judge_ok:
            diffs.append((actual, r["label"], judge_ok, j.get("comment") or ""))
    n = len(pairs)
    agree = sum(a == b for a, b in pairs)
    k = kappa(pairs)
    tp = sum(1 for r, j in pairs if not r and not j)   # both say wrong: a caught misroute
    fn = sum(1 for r, j in pairs if not r and j)       # judge passed a misroute
    fp = sum(1 for r, j in pairs if r and not j)       # judge flagged a right handoff
    tn = sum(1 for r, j in pairs if r and j)
    if a.markdown:
        print(f"| Reviewed | Agreement | Cohen's kappa | Misroutes caught | Misroutes missed | False flags |")
        print("|---|---|---|---|---|---|")
        print(f"| {n} | {agree}/{n} ({agree / n:.0%}) | {k if k is None else round(k, 2)} | {tp} | {fn} | {fp} |")
        for actual, label, judge_ok, why in diffs:
            print(f"\n- went to `{actual}`, reviewer says `{label}`, judge said {judge_ok}: {why[:200]}")
        return
    print(f"{n} reviewed handoffs with a judge verdict")
    print(f"  agreement      {agree}/{n} ({agree / n:.0%})")
    print(f"  Cohen's kappa  {'n/a (one class only)' if k is None else round(k, 2)}")
    print(f"  confusion      misroutes caught {tp} · missed {fn} · false flags {fp} · right and passed {tn}")
    for actual, label, judge_ok, why in diffs:
        print(f"  ✗ went to {actual}, reviewer says {label}; judge said {judge_ok}: {why[:160]}")
    if n < 20:
        print(f"\n(n={n}: a direction, not a measurement)")


if __name__ == "__main__":
    main()
