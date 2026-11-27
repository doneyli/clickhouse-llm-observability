"""Owned identity and read-back for a synthetic history batch.

A batch is everything one `generate_history.py` run writes. Its key hashes the inputs that
shape the traces (seed, total, the synthetic-history settings, fleet, models) and the UTC
day its timestamps count back from. Trace ids derive from the key and the trace's index,
and the generator derives every observation id from the trace id, so the same key always
produces the same rows.

Before writing, `generate_history.py` asks Langfuse whether a recent batch with today's
inputs is already there by reading back its first and last trace. A complete batch means
an unchanged re-run writes nothing; a partial one is re-sent with the same ids, which
upserts in place.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.langfuse_rest import ApiError, Client

GENERATOR_VERSION = 1  # bump when trace_generator.py changes what a batch contains


def batch_key(cfg: Any, total: int, seed: int, day: date) -> str:
    """Stable key for the batch these inputs would write, anchored on `day` (UTC)."""
    shape = {
        "v": GENERATOR_VERSION,
        "seed": seed,
        "total": total,
        "day": day.isoformat(),
        "synthetic": cfg.synthetic_history.model_dump(),
        "fleet": [a.model_dump() for a in cfg.agent_fleet.other_agents],
        "hero": cfg.agent_fleet.hero_agent.name,
        "models": cfg.llm.models.model_dump(),
        "customer": cfg.customer.display_name,
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


def anchor(day: date) -> datetime:
    """The instant a batch's timestamps count back from: midnight UTC on its day."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def trace_id(key: str, index: int) -> str:
    """A 32-hex trace id, the same for the same batch and index on every run."""
    return hashlib.sha256(f"brand-promo-history:{key}:{index}".encode()).hexdigest()[:32]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def trace_exists(client: Client, tid: str, start: datetime, end: datetime) -> bool:
    """Whether any observation of trace `tid` has landed (Observations API v2).

    A self-hosted server on the pinned v3 image (docker-compose.yaml, langfuse 3.221.1)
    has no Observations v2 and answers 404; only then does this fall back to the v1
    observations list, which that server still serves.
    """
    try:
        d = client.get("/api/public/v2/observations", traceId=tid, fields="core",
                       fromStartTime=_iso(start), toStartTime=_iso(end), limit=1) or {}
    except ApiError as e:
        if e.status != 404:
            raise
        d = client.get("/api/public/observations", traceId=tid, limit=1) or {}
    return bool(d.get("data"))


def batch_state(client: Client, key: str, total: int, day: date, days_back: int) -> str:
    """"complete", "partial" or "absent", from the batch's first and last trace."""
    start = anchor(day) - timedelta(days=days_back + 1)
    end = anchor(day) + timedelta(days=2)
    first = trace_exists(client, trace_id(key, 0), start, end)
    last = trace_exists(client, trace_id(key, total - 1), start, end)
    if first and last:
        return "complete"
    return "partial" if (first or last) else "absent"
