"""Read-back helpers on the Langfuse v4 public API (cursor paging, required time bounds)."""

from __future__ import annotations

from claims import config

SINCE = "2026-01-01T00:00:00Z"


def paged(path: str, params: dict, cap: int = 5000) -> list[dict]:
    out, cursor = [], None
    while len(out) < cap:
        p = {**params, "limit": 100, **({"cursor": cursor} if cursor else {})}
        d = config.api("GET", path, params=p)
        out += d.get("data") or []
        cursor = (d.get("meta") or {}).get("cursor") or (d.get("meta") or {}).get("nextCursor")
        if not cursor or not d.get("data"):
            return out
    return out


def experiments(dataset: str) -> list[dict]:
    ds = config.api("GET", f"/api/public/v2/datasets/{dataset}")
    return paged("/api/public/experiments", {"datasetId": ds["id"], "fromStartTime": SINCE})


def experiment_items(experiment_id: str) -> list[dict]:
    return paged("/api/public/experiment-items", {"experimentId": experiment_id, "fromStartTime": SINCE,
                                                  "fields": "core,io,scores"})


def delete_experiment(experiment_id: str) -> int:
    """No experiment-delete endpoint: delete its item traces (and with them observations and scores)."""
    ids = sorted({i.get("traceId") for i in experiment_items(experiment_id) if i.get("traceId")})
    for k in range(0, len(ids), 1000):
        config.api("DELETE", "/api/public/traces", {"traceIds": ids[k:k + 1000]})
    return len(ids)
