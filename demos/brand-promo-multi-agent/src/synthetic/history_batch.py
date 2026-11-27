"""Owned identity and read-back for a synthetic history batch.

A batch is everything one `generate_history.py` run writes. Its key fingerprints every
resolved input the generator reads (see `generation_inputs`), the generator's own source
(`definition_digest`), the seed, the size and the UTC day its timestamps count back from.
Trace ids derive from the key and the trace's index, and the generator derives every
observation and score id from the trace id, so the same key always produces the same rows.

Every trace and observation of a batch carries the version `stamp(key, total)`. That makes
the batch countable apart from any other data in the project: `read_counts` asks the
Metrics API for the batch's observations per (trace name, observation name) and its scores
per name, and `batch_state` compares those with the counts the generator plans for the
batch offline (`trace_generator.batch_counts`). Every group must be there in full, so a
missing ingestion chunk anywhere in the batch, not only at its ends, reads as "partial".

`owned_batches` lists every stamp this generator has written, and `delete_batch` removes
one superseded batch by its derived trace ids. Nothing else in the project is touched.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.langfuse_rest import ApiError, Client

GENERATOR_VERSION = 2  # 2: version stamp on every trace and observation
STAMP_PREFIX = "brand-promo-history/"
# The modules that decide what a batch contains. Any change to them is a new batch.
DEFINITION_FILES = ("trace_generator.py", "query_templates.py", "distributions.py")
DELETE_CHUNK = 500  # trace ids per DELETE /api/public/traces call
ROW_LIMIT = 1000    # the Metrics API's maximum; a batch has a few dozen groups


def definition_digest(files: tuple[str, ...] = DEFINITION_FILES) -> str:
    """Hash of the generator's source, so a changed generator never reuses an old batch."""
    h = hashlib.sha256(f"v{GENERATOR_VERSION}".encode())
    here = Path(__file__).parent
    for name in files:
        h.update(name.encode() + b"\0" + (here / name).read_bytes())
    return h.hexdigest()


def generation_inputs(cfg: Any) -> dict[str, Any]:
    """Every resolved config value the history generator reads, and nothing else.

    trace_generator.py reads synthetic_history, the fleet, the hero agent's name, the four
    models and the customer's display name; query_templates.py reads the brand names, the
    SKUs, the regions and the retail partners. A change to any of them changes the content.
    """
    return {
        "synthetic": cfg.synthetic_history.model_dump(),
        "fleet": [a.model_dump() for a in cfg.agent_fleet.other_agents],
        "hero": cfg.agent_fleet.hero_agent.name,
        "models": cfg.llm.models.model_dump(),
        "customer": cfg.customer.display_name,
        "brands": cfg.all_brand_names(),
        "skus": cfg.all_skus(),
        "regions": list(cfg.regions),
        "retail_partners": list(cfg.retail_partners),
    }


def batch_key(cfg: Any, total: int, seed: int, day: date, definition: str | None = None) -> str:
    """Stable key for the batch these inputs would write, anchored on `day` (UTC)."""
    shape = {
        "definition": definition or definition_digest(),
        "seed": seed,
        "total": total,
        "day": day.isoformat(),
        "inputs": generation_inputs(cfg),
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


def stamp(key: str, total: int) -> str:
    """The version every trace and observation of the batch carries."""
    return f"{STAMP_PREFIX}{key}/{total}"


def parse_stamp(value: str) -> tuple[str, int] | None:
    """(key, total) from a stamp this module wrote, else None."""
    if not value or not value.startswith(STAMP_PREFIX):
        return None
    parts = value[len(STAMP_PREFIX):].split("/")
    if len(parts) != 2 or len(parts[0]) != 16 or not parts[1].isdigit():
        return None
    return parts[0], int(parts[1])


def anchor(day: date) -> datetime:
    """The instant a batch's timestamps count back from: midnight UTC on its day."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def window(day: date, days_back: int) -> tuple[datetime, datetime]:
    """The time range a batch anchored on `day` falls in."""
    return anchor(day) - timedelta(days=days_back + 1), anchor(day) + timedelta(days=2)


def trace_id(key: str, index: int) -> str:
    """A 32-hex trace id, the same for the same batch and index on every run."""
    return hashlib.sha256(f"brand-promo-history:{key}:{index}".encode()).hexdigest()[:32]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def metrics(client: Client, query: dict[str, Any]) -> list[dict]:
    """Run one Metrics API query.

    Langfuse Cloud serves Metrics v2. A self-hosted server on the pinned v3 image
    (docker-compose.yaml, langfuse 3.221.1) answers /v2/metrics with 404; only then does
    this fall back to the v1 metrics endpoint, which that server still serves.
    """
    q = json.dumps(query)
    try:
        d = client.get("/api/public/v2/metrics", query=q) or {}
    except ApiError as e:
        if e.status != 404:
            raise
        d = client.get("/api/public/metrics", query=q) or {}
    return d.get("data") or []


def _count(row: dict) -> int:
    # The measure comes back as `<aggregation>_<measure>`; ClickHouse may send it as a string.
    return int(float(row.get("count_count") or 0))


def read_counts(client: Client, version: str, start: datetime, end: datetime) -> dict[str, Any]:
    """The batch's observations per (trace name, name) and its scores per name, as landed."""
    span = {"fromTimestamp": _iso(start), "toTimestamp": _iso(end), "config": {"row_limit": ROW_LIMIT}}
    count = [{"measure": "count", "aggregation": "count"}]
    obs_rows = metrics(client, dict(span, view="observations", metrics=count,
                                    dimensions=[{"field": "traceName"}, {"field": "name"}],
                                    filters=[{"column": "version", "operator": "=", "type": "string",
                                              "value": version}]))
    score_rows = metrics(client, dict(span, view="scores-numeric", metrics=count,
                                      dimensions=[{"field": "name"}],
                                      filters=[{"column": "traceVersion", "operator": "=", "type": "string",
                                                "value": version}]))
    obs = {}
    for r in obs_rows:
        k = (r.get("traceName") or None, r.get("name"))
        obs[k] = obs.get(k, 0) + _count(r)
    scores = {}
    for r in score_rows:
        scores[r.get("name")] = scores.get(r.get("name"), 0) + _count(r)
    return {"observations": obs, "scores": scores}


def compare(expected: dict[str, Any], found: dict[str, Any]) -> tuple[str, list[str]]:
    """("complete" | "partial" | "absent", the groups that fall short).

    Every planned group must be there in full. A group may read higher than planned (an
    upsert that ClickHouse has not merged yet counts twice for a while), never lower.
    """
    short = []
    for (trace_name, name), n in sorted(expected["observations"].items(), key=str):
        have = found["observations"].get((trace_name, name), 0)
        if have < n:
            short.append(f"{trace_name}/{name}: {have}/{n} observations")
    for name, n in sorted(expected["scores"].items()):
        have = found["scores"].get(name, 0)
        if have < n:
            short.append(f"score {name}: {have}/{n}")
    landed = sum(found["observations"].values()) + sum(found["scores"].values())
    if not short:
        return "complete", []
    return ("partial" if landed else "absent"), short


def batch_state(client: Client, key: str, total: int, day: date, days_back: int,
                expected: dict[str, Any]) -> tuple[str, list[str]]:
    """Read the batch back and compare it with its plan (see `compare`)."""
    start, end = window(day, days_back)
    return compare(expected, read_counts(client, stamp(key, total), start, end))


def owned_batches(client: Client, start: datetime, end: datetime) -> dict[str, int]:
    """Every batch stamp this generator wrote in the range, with its observation count."""
    rows = metrics(client, {
        "view": "observations", "metrics": [{"measure": "count", "aggregation": "count"}],
        "dimensions": [{"field": "version"}],
        "filters": [{"column": "version", "operator": "starts with", "type": "string", "value": STAMP_PREFIX}],
        "fromTimestamp": _iso(start), "toTimestamp": _iso(end), "config": {"row_limit": ROW_LIMIT}})
    out: dict[str, int] = {}
    for r in rows:
        v = r.get("version") or ""
        if parse_stamp(v):
            out[v] = out.get(v, 0) + _count(r)
    return out


def delete_batch(client: Client, version: str) -> int:
    """Delete one owned batch by its derived trace ids; returns how many ids were sent.

    Only ids derived from the stamp's own key are named, so nothing outside the batch can
    be deleted. Langfuse deletes asynchronously; a later run that still sees the stamp
    sends the same ids again, which is harmless.
    """
    parsed = parse_stamp(version)
    if not parsed:
        raise ValueError(f"not a history stamp: {version!r}")
    key, total = parsed
    ids = [trace_id(key, i) for i in range(total)]
    for i in range(0, len(ids), DELETE_CHUNK):
        client.request("DELETE", "/api/public/traces", body={"traceIds": ids[i:i + DELETE_CHUNK]})
    return len(ids)
