#!/usr/bin/env python3
"""Seed the PromoPlanner Human Review annotation queue with ambiguous-score traces.

1. Ensure the queue exists. Creating one needs the `human_brief_review` score config,
   from scripts/setup_score_configs.py, which seed_all.py runs first.
2. Pick candidates from the seeded history: Scores v3 `response-factuality` scores between
   0.6 and 0.8 whose subject is a trace, joined to Observations v2 to keep only synthetic
   `promo_planner_run` traces. With none in range, fall back to the newest
   `promo_planner_run` traces.
3. Fill the queue up to TARGET_ITEMS, skipping traces already in it. A queue that already
   holds TARGET_ITEMS items is left unchanged, so re-runs never grow it.

Exits 1 when a read or write fails, or when there is no history to pick from.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

from rich.console import Console

from src.config import load_config, load_env
from src.langfuse_rest import ApiError, Client, LangfuseEnvError, Tally

console = Console()

QUEUE_NAME = "PromoPlanner Human Review"
QUEUE_DESCRIPTION = "Traces with ambiguous factuality scores (0.6-0.8) for human review"
REVIEW_SCORE_CONFIG = "human_brief_review"
HISTORY_TRACE = "promo_planner_run"
FACTUALITY_SCORE = "response-factuality"
AMBIGUOUS = (0.6, 0.8)
TARGET_ITEMS = 10
MAX_CANDIDATE_CHECKS = 60


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trace_name_filter(name: str) -> str:
    # traceName is not a query parameter of /v2/observations: it goes in `filter`.
    return json.dumps([{"type": "string", "column": "traceName", "operator": "=", "value": name}])


def ensure_queue(client: Client) -> tuple[str, str]:
    """(queue id, "created" | "unchanged")."""
    for q in client.paged("/api/public/annotation-queues"):
        if q.get("name") == QUEUE_NAME:
            return q["id"], "unchanged"
    configs = {c.get("name"): c for c in client.paged("/api/public/score-configs")}
    cfg = configs.get(REVIEW_SCORE_CONFIG)
    if not cfg:
        raise ApiError(None, f"score config {REVIEW_SCORE_CONFIG} is missing; run scripts/setup_score_configs.py")
    q = client.post("/api/public/annotation-queues", {
        "name": QUEUE_NAME, "description": QUEUE_DESCRIPTION, "scoreConfigIds": [cfg["id"]]}) or {}
    if not q.get("id"):
        raise ApiError(None, "queue create returned no id")
    return q["id"], "created"


def is_history_trace(client: Client, trace_id: str, since: datetime, until: datetime) -> bool:
    d = client.get("/api/public/v2/observations", traceId=trace_id, filter=_trace_name_filter(HISTORY_TRACE),
                   fromStartTime=_iso(since), toStartTime=_iso(until), fields="core", limit=1) or {}
    return bool(d.get("data"))


def ambiguous_candidates(client: Client, since: datetime, until: datetime) -> list[str]:
    """Trace ids of seeded PromoPlanner runs with an ambiguous factuality score."""
    scores = client.cursor("/api/public/v3/scores", {
        "name": FACTUALITY_SCORE, "dataType": "NUMERIC", "valueMin": AMBIGUOUS[0], "valueMax": AMBIGUOUS[1],
        "fields": "subject", "fromTimestamp": _iso(since), "limit": 100}, cap=500)
    seen: list[str] = []
    for s in scores:
        subject = s.get("subject") or {}
        if subject.get("kind") == "trace" and subject.get("id") and subject["id"] not in seen:
            seen.append(subject["id"])
    out = []
    for tid in seen[:MAX_CANDIDATE_CHECKS]:
        if is_history_trace(client, tid, since, until):
            out.append(tid)
    return out


def newest_history_traces(client: Client, since: datetime, until: datetime) -> list[str]:
    rows = client.cursor("/api/public/v2/observations", {
        "filter": _trace_name_filter(HISTORY_TRACE), "fields": "core",
        "fromStartTime": _iso(since), "toStartTime": _iso(until), "limit": 100}, cap=300)
    out: list[str] = []
    for r in rows:
        if r.get("traceId") and r["traceId"] not in out:
            out.append(r["traceId"])
    return out


def seed_annotation_queue(client: Client | None = None, now: datetime | None = None) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1
    now = now or datetime.now(UTC)
    days_back = load_config().synthetic_history.days_back
    # History is backdated up to days_back from its anchor day, which may be a week old;
    # its scores carry the ingestion time.
    since, until = now - timedelta(days=days_back + 8), now + timedelta(minutes=5)
    tally = Tally("Queue items")

    try:
        queue_id, queue_state = ensure_queue(client)
        console.print(f"[{'green' if queue_state == 'created' else 'dim'}]Queue {queue_state}: "
                      f"{QUEUE_NAME} ({queue_id})[/]")
        existing = {i.get("objectId") for i in client.paged(f"/api/public/annotation-queues/{queue_id}/items")}
        if len(existing) >= TARGET_ITEMS:
            for _ in existing:
                tally.add("unchanged")
            console.print(f"[dim]Queue already holds {len(existing)} item(s); nothing added.[/dim]")
            console.print(f"\n[bold]{tally.line()}[/bold]")
            return 0
        picks = ambiguous_candidates(client, since, until)
        source = f"{FACTUALITY_SCORE} in {AMBIGUOUS[0]}-{AMBIGUOUS[1]}"
        if not picks:
            picks = newest_history_traces(client, since, until)
            source = f"newest {HISTORY_TRACE} traces (no ambiguous scores found)"
    except ApiError as e:
        console.print(f"[red]Queue seeding failed: {e}[/red]")
        return 1

    if not picks:
        console.print(f"[red]No {HISTORY_TRACE} traces to queue: run uv run scripts/generate_history.py first[/red]")
        return 1

    console.print(f"[cyan]Candidates: {len(picks)} from {source}[/cyan]")
    need = TARGET_ITEMS - len(existing)
    for tid in picks:
        if need <= 0:
            break
        if tid in existing:
            tally.add("unchanged")
            continue
        try:
            client.post(f"/api/public/annotation-queues/{queue_id}/items",
                        {"objectId": tid, "objectType": "TRACE", "status": "PENDING"})
            tally.add("created")
            existing.add(tid)
            need -= 1
        except ApiError as e:
            tally.add("failed", f"{tid}: {e}")
            console.print(f"[red]failed to queue {tid}: {e}[/red]")

    console.print(f"\n[bold]{tally.line()}[/bold]")
    return 1 if tally.failed else 0


if __name__ == "__main__":
    sys.exit(seed_annotation_queue())
