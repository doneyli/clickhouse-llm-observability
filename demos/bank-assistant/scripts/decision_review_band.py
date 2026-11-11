"""Act / review / ignore: what the decision-model evaluators flagged (EVA-04, EVA-06).

A decision model returns a probability, not a verdict, so every flag can take one
of three paths instead of two (thresholds in northwind/decision_evals.py):
  act     route it now — complaint case, fraud team, vulnerable-customer desk
  review  a human looks: the turns where an SME label teaches the most
  ignore  below the threshold

Reads the decision-model scores on production assistant turns in a window and
prints, per question, the turns in each band SPLIT BY LANGUAGE — Jev 1.13 is
less accurate and over-confident on Spanish text, so compare the columns before
trusting one threshold for both — plus two cross-checks against the
deterministic scores the app writes on the same observation:
  customer-intent vs intent            what the customer asked vs what the agent retrieved and did
  conduct-unsolicited-upsell vs        the decision model vs the upsell regex
      unsolicited-upsell

--queue N adds up to N conduct-screen REVIEW-band turns, most uncertain first, to
the SME review queue: their `sme-compliance` labels are the ground truth that
sets the per-language threshold.

Run: .venv/bin/python scripts/decision_review_band.py [--hours 24] [--queue 10]
"""
import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, decision_evals as de  # noqa: E402

QUEUE = "SME review — assistant answers"
DETERMINISTIC = ("intent", "unsolicited-upsell")


def cursor_all(path, **params):
    out, cursor = [], None
    while True:
        d = config.api("GET", path, params={"limit": 100, **params, **({"cursor": cursor} if cursor else {})})
        out += d.get("data") or []
        cursor = (d.get("meta") or {}).get("cursor")
        if not cursor:
            return out


def page_all(path):
    out, page = [], 1
    while True:
        d = config.api("GET", path, params={"limit": 100, "page": page})
        out += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return out
        page += 1


def collect(since: str):
    """{observation id: {"lang": .., "trace": .., scores…}} for production assistant turns."""
    turns = {o["id"]: {"lang": (o.get("metadata") or {}).get("language", "?"), "trace": o["traceId"]}
             for o in cursor_all("/api/public/v2/observations", name="northwind-assistant",
                                 environment="production", fromStartTime=since, fields="core,metadata")}
    wanted = set(de.questions_by_score()) | set(DETERMINISTIC)
    for s in cursor_all("/api/public/v3/scores", fromTimestamp=since, fields="core,subject"):
        subject = s.get("subject") or {}
        if s["name"] in wanted and subject.get("id") in turns:
            turns[subject["id"]][s["name"]] = s.get("value")
    return turns


def intent_family(intent) -> str:
    """The deterministic `intent` in the decision model's vocabulary."""
    if not isinstance(intent, str):
        return "-"
    return {"attack": "manipulation"}.get(intent, "product-question" if intent.startswith("product-") else intent)


def report(turns: dict) -> list:
    questions = de.questions_by_score()
    scored = {oid: t for oid, t in turns.items() if any(n in t for n in questions)}
    langs = sorted({t["lang"] for t in scored.values()})
    print(f"{len(turns)} production assistant turns in the window, {len(scored)} with decision-model scores\n")
    if not scored:
        print("No decision-model scores yet: run scripts/seed_decision_evals.py, then generate traffic.")
        return []

    review = []  # (uncertainty, observation id, score name) for the SME queue
    print(f"{'question':32} " + "  ".join(f"{lang + ' act/review/ignore':>22}" for lang in langs))
    for name, q in questions.items():
        if q["type"] == "choice":
            continue
        cells = defaultdict(Counter)
        for oid, t in scored.items():
            value = t.get(name)
            if isinstance(value, (int, float)):
                b = de.band(q, float(value))
                cells[t["lang"]][b] += 1
                if b == "review" and name.startswith("conduct-"):
                    review.append((abs(float(value) - 0.5), oid, name))
        print(f"{name:32} " + "  ".join(
            f"{cells[lang]['act']:>10} / {cells[lang]['review']:>3} / {cells[lang]['ignore']:>4}" for lang in langs))

    print("\ncustomer-intent (decision model) vs intent (app, from what the agent retrieved and did)")
    pairs = Counter((t.get("customer-intent"), intent_family(t.get("intent")))
                    for t in scored.values() if t.get("customer-intent"))
    agree = sum(n for (a, b), n in pairs.items() if a == b)
    total = sum(pairs.values())
    print(f"  agree on {agree}/{total} turns" + (f" ({agree / total:.0%})" if total else ""))
    for (a, b), n in sorted(((k, v) for k, v in pairs.items() if k[0] != k[1]), key=lambda kv: -kv[1])[:8]:
        print(f"  {n:>4} × customer asked {a!r}, agent handled it as {b!r}")

    both = [(t["conduct-unsolicited-upsell"] >= de.ACT, t["unsolicited-upsell"] == 1) for t in scored.values()
            if isinstance(t.get("conduct-unsolicited-upsell"), (int, float))
            and isinstance(t.get("unsolicited-upsell"), (int, float))]
    if both:
        c = Counter(both)
        print("\nunsolicited upsell: decision model (P ≥ %.1f) vs regex" % de.ACT)
        print(f"  both flag {c[(True, True)]} · decision model only {c[(True, False)]} · "
              f"regex only {c[(False, True)]} · neither {c[(False, False)]}")
    return sorted(review)


def queue_review_band(review: list, limit: int) -> int:
    q = next((q for q in page_all("/api/public/annotation-queues") if q["name"] == QUEUE), None)
    if q is None:
        print("SME review queue is missing. Run scripts/seed_evals.py first.", file=sys.stderr)
        return 1
    path = f"/api/public/annotation-queues/{q['id']}/items"
    queued = {i["objectId"] for i in page_all(path)}
    added = []
    for _, oid, name in review:
        if len(added) >= limit:
            break
        if oid in queued or oid in {a for a, _ in added}:
            continue
        config.api("POST", path, {"objectId": oid, "objectType": "OBSERVATION", "status": "PENDING"})
        added.append((oid, name))
    print(f"\n+ {len(added)} review-band turns → queue '{QUEUE}'")
    for oid, name in added:
        print(f"  {oid}  {name}")
    print(f"Annotate: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/annotation-queues/{q['id']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--queue", type=int, default=0, metavar="N",
                    help="add up to N conduct-screen review-band turns to the SME review queue")
    a = ap.parse_args(argv)
    since = (datetime.now(timezone.utc) - timedelta(hours=a.hours)).isoformat()
    review = report(collect(since))
    return queue_review_band(review, a.queue) if a.queue else 0


if __name__ == "__main__":
    raise SystemExit(main())
