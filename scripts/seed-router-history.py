#!/usr/bin/env python3
"""
Seed ~300 backdated `route-and-dispatch` router traces over 14 days — NO LLM
calls (direct batch ingestion via the public /api/public/ingestion endpoint), so
the Router Ops dashboard tells its story on day one:

  * Route distribution over time — a week-over-week DRIFT: docs_simple-heavy in
    week 1, analytics_sql share doubling in week 2.
  * Fallback rate — ~8% of traces divert to the in-process fallback + escalation.
  * Avg router_confidence — realistic per-route distributions (high for clear
    routes, low for fallback).
  * Misroute rate — ~6% of reviewed traces carry routing_correct=0 (BOOLEAN),
    clustered on a couple of days so the widget shows a visible dip.

Each trace mirrors the live shape: a `route-query` generation (model
claude-haiku-4-5) with {route, confidence, rationale} + metadata.route, a runtime
router_confidence score, and either a dispatch-<route> span or a fallback-handler
generation + escalate-to-human event.

Usage:
    python scripts/seed-router-history.py                 # 300 traces / 14 days
    python scripts/seed-router-history.py --count 300 --days 14 --seed 42

Env: LANGFUSE_HOST/BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
     (defaults: http://localhost:3001, pk-lf-1234567890, sk-lf-1234567890).
"""

import argparse
import base64
import json
import os
import random
import sys
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

HOST = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001")).rstrip("/")
PK = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
SK = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
_AUTH = base64.b64encode(f"{PK}:{SK}".encode()).decode()

ROUTER_MODEL = os.getenv("ROUTER_MODEL", "claude-haiku-4-5")

QUESTIONS = {
    "analytics_sql": ["How many taxi rides in NYC last month?", "Top 5 starred GitHub repos this year?",
                      "Average trip distance in NYC?", "Most downloaded PyPI packages?"],
    "docs_simple": ["What is a vector index?", "What is ClickHouse?", "What are embeddings?",
                    "What does the Langfuse session view show?"],
    "docs_complex": ["Compare ClickHouse-native vectors with Chroma and when each wins.",
                     "Walk through CRAG self-correction and its failure cases.",
                     "Contrast naive RAG with agentic RAG end to end."],
    "fallback": ["Is ClickHouse fast?", "Write me a poem about databases.",
                 "What's the weather in Amsterdam?", "Show me how RAG performs on real data."],
}
FALLBACK_REASONS = ["low_confidence", "out_of_scope", "malformed_output", "handler_unreachable"]
_uid = lambda: str(uuid.uuid4())


def _iso(dt):
    return dt.isoformat()


def _post_batch(events):
    body = json.dumps({"batch": events}).encode()
    req = urllib.request.Request(
        f"{HOST}/api/public/ingestion", data=body, method="POST",
        headers={"Authorization": f"Basic {_AUTH}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _pick_route(rng, day_offset, days):
    """Week-dependent route weights => visible drift (analytics_sql doubles in week 2)."""
    if rng.random() < 0.08:
        return "fallback"
    older_half = day_offset >= days / 2  # older = week 1
    if older_half:
        weights = {"docs_simple": 0.55, "docs_complex": 0.25, "analytics_sql": 0.20}
    else:
        weights = {"analytics_sql": 0.40, "docs_simple": 0.38, "docs_complex": 0.22}
    routes, w = zip(*weights.items())
    return rng.choices(routes, weights=w, k=1)[0]


def _confidence(rng, route):
    if route == "fallback":
        return round(rng.uniform(0.35, 0.68), 2)
    return round(rng.uniform(0.80, 0.98), 2)


def _build_trace(rng, days):
    day_offset = rng.randint(0, days - 1)
    base = datetime.now(timezone.utc) - timedelta(days=day_offset)
    t0 = base.replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59),
                      second=rng.randint(0, 59), microsecond=0)
    route = _pick_route(rng, day_offset, days)
    conf = _confidence(rng, route)
    question = rng.choice(QUESTIONS[route])
    is_ambiguous = route == "fallback" or conf < 0.85

    trace_id, gen_id = _uid(), _uid()
    events = []

    # root trace
    events.append({"id": _uid(), "type": "trace-create", "timestamp": _iso(t0), "body": {
        "id": trace_id, "timestamp": _iso(t0), "name": "route-and-dispatch",
        "input": {"question": question}, "tags": ["query-router", "demo"],
        "sessionId": f"query-router-{uuid.uuid4().hex[:8]}",
        "userId": f"demo-user-{rng.randint(1, 25)}",
        "metadata": {"synthetic": True}}})

    # route-query generation
    g0, g1 = t0, t0 + timedelta(milliseconds=rng.randint(200, 900))
    events.append({"id": _uid(), "type": "generation-create", "timestamp": _iso(g1), "body": {
        "id": gen_id, "traceId": trace_id, "name": "route-query", "model": ROUTER_MODEL,
        "startTime": _iso(g0), "endTime": _iso(g1),
        "input": {"question": question},
        "output": {"route": route, "confidence": conf, "rationale": "synthetic classification"},
        "metadata": {"route": route, "threshold": 0.70,
                     "fallback_triggered": route == "fallback", "router_model": ROUTER_MODEL},
        "usageDetails": {"input": rng.randint(120, 260), "output": rng.randint(20, 60)}}})

    # runtime router_confidence score on the route-query observation
    events.append({"id": _uid(), "type": "score-create", "timestamp": _iso(g1), "body": {
        "id": _uid(), "traceId": trace_id, "observationId": gen_id,
        "name": "router_confidence", "value": conf, "dataType": "NUMERIC",
        "comment": "threshold=0.70"}})

    t = g1
    if route == "fallback":
        reason = rng.choice(FALLBACK_REASONS)
        f0, f1 = t, t + timedelta(milliseconds=rng.randint(300, 1200))
        events.append({"id": _uid(), "type": "generation-create", "timestamp": _iso(f1), "body": {
            "id": _uid(), "traceId": trace_id, "name": "fallback-handler", "model": ROUTER_MODEL,
            "startTime": _iso(f0), "endTime": _iso(f1),
            "input": {"question": question}, "output": "best-effort answer with caveat",
            "metadata": {"route": "fallback"}}})
        events.append({"id": _uid(), "type": "event-create", "timestamp": _iso(f1), "body": {
            "id": _uid(), "traceId": trace_id, "name": "escalate-to-human", "startTime": _iso(f1),
            "input": {"reason": reason, "confidence": conf}}})
    else:
        d0, d1 = t, t + timedelta(milliseconds=rng.randint(800, 6000))
        events.append({"id": _uid(), "type": "span-create", "timestamp": _iso(d1), "body": {
            "id": _uid(), "traceId": trace_id, "name": f"dispatch-{route}",
            "startTime": _iso(d0), "endTime": _iso(d1),
            "input": {"question": question}, "output": {"answer": "specialist answer"}}})

    # Post-hoc routing_correct on ~30% "reviewed" traces; ~6% false overall, but
    # clustered on 2 days so the misroute-rate widget shows a visible dip.
    if rng.random() < 0.30:
        cluster = day_offset in (8, 9)
        false_rate = 0.45 if (cluster and is_ambiguous) else 0.06
        correct = 0 if rng.random() < false_rate else 1
        events.append({"id": _uid(), "type": "score-create", "timestamp": _iso(t0), "body": {
            "id": _uid(), "traceId": trace_id, "observationId": gen_id,
            "name": "routing_correct", "value": correct, "dataType": "BOOLEAN",
            "comment": "human review (synthetic)" if correct else "misroute (synthetic)"}})

    return events


def main():
    ap = argparse.ArgumentParser(description="Seed backdated router history (no LLM calls)")
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch-size", type=int, default=100)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    print(f"Seeding {args.count} router traces over {args.days} days -> {HOST}", file=sys.stderr)

    pending, sent, errors = [], 0, 0
    for _ in range(args.count):
        pending.extend(_build_trace(rng, args.days))
        if len(pending) >= args.batch_size * 6:
            try:
                resp = _post_batch(pending)
                errors += len(resp.get("errors", []) or [])
            except Exception as e:
                print(f"  batch error: {e}", file=sys.stderr)
                errors += 1
            sent += 1
            pending = []
    if pending:
        try:
            resp = _post_batch(pending)
            errors += len(resp.get("errors", []) or [])
        except Exception as e:
            print(f"  batch error: {e}", file=sys.stderr)
            errors += 1
        sent += 1

    print(f"Done. {args.count} traces in {sent} batch POST(s); ingestion errors: {errors}", file=sys.stderr)
    print(f"Build the Router Ops dashboard, then explore: {HOST} -> Traces (name=route-and-dispatch)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
