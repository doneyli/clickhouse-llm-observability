"""Reconcile asynchronous managed policy scores onto the same observation.

No judge calls. Cursor-paginated v4 reads; idempotent score writes. Replaying
the window handles late scores and process restarts without passing missing data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from northwind.compliance import (BY_NAME, COVERAGE_NAME, OVERALL_NAME, POLICIES,
                                 RUBRIC_VERSION, STATUS_NAME, aggregate)


def cursor_all(api, path, params):
    rows, cursor = [], None
    while True:
        page = api("GET", path, params={**params, **({"cursor": cursor} if cursor else {})})
        rows.extend(page.get("data") or [])
        next_cursor = (page.get("meta") or {}).get("cursor") or (page.get("meta") or {}).get("nextCursor")
        if not next_cursor:
            return rows
        if next_cursor == cursor:
            raise RuntimeError(f"Pagination did not advance for {path}")
        cursor = next_cursor


def subject_key(score):
    subject = score.get("subject") or {}
    if str(subject.get("kind", "")).lower() != "observation":
        return None
    tid, oid = subject.get("traceId"), subject.get("id")
    return (tid, oid) if tid and oid else None


def latest_policy_scores(scores):
    """Do not substitute human labels or API scores for managed judge results."""
    latest = {}
    for s in sorted(scores, key=lambda s: (s.get("updatedAt") or s.get("timestamp") or "", s.get("id") or "")):
        key = subject_key(s)
        if key and s.get("name") in BY_NAME and s.get("source") == "EVAL":
            latest.setdefault(key, {})[BY_NAME[s["name"]].id] = s
    return latest


def score_id(tid, oid, name):
    return str(uuid5(NAMESPACE_URL, f"northwind/{RUBRIC_VERSION}/{tid}/{oid}/{name}"))


def derived_scores(tid, oid, scores, environment="production"):
    verdicts = {pid: (s.get("value") if s.get("dataType") == "CATEGORICAL" else "invalid")
                for pid, s in scores.items()}
    result = aggregate(verdicts)
    signature = json.dumps([(p.id, scores.get(p.id, {}).get("id"), verdicts.get(p.id),
                             scores.get(p.id, {}).get("updatedAt")) for p in POLICIES], sort_keys=True)
    metadata = {"rubric_version": RUBRIC_VERSION, "aggregation": "worst-policy",
                "fingerprint": hashlib.sha256(signature.encode()).hexdigest(),
                "policy_score_ids": {k: s["id"] for k, s in scores.items()}}
    common = {"traceId": tid, "observationId": oid, "environment": environment,
              "comment": result.comment(), "metadata": metadata}
    values = [(STATUS_NAME, result.status, "CATEGORICAL"), (COVERAGE_NAME, result.coverage, "NUMERIC")]
    if result.score is not None:
        values.append((OVERALL_NAME, result.score, "NUMERIC"))
    return [{**common, "id": score_id(tid, oid, name), "name": name, "value": value, "dataType": kind}
            for name, value, kind in values]


def sync_recent(api, *, hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()
    filters = [
        {"type": "stringOptions", "column": "traceName", "operator": "any of",
         "value": ["northwind-assistant", "northwind-voice-call"]},
        {"type": "stringOptions", "column": "name", "operator": "any of", "value": ["northwind-assistant"]},
        {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
        {"type": "datetime", "column": "startTime", "operator": ">=", "value": since},
        {"type": "datetime", "column": "startTime", "operator": "<=", "value": now.isoformat()},
    ]
    observations = cursor_all(api, "/api/public/v2/observations",
                              {"filter": json.dumps(filters), "fields": "core,basic", "limit": 100})
    candidates = {(o["traceId"], o["id"]): o for o in observations if o.get("endTime")}
    trace_ids = sorted({key[0] for key in candidates})
    all_scores = []
    for offset in range(0, len(trace_ids), 25):
        all_scores.extend(cursor_all(api, "/api/public/v3/scores", {
            "traceId": ",".join(trace_ids[offset:offset + 25]),
            "name": ",".join([*BY_NAME, OVERALL_NAME, STATUS_NAME, COVERAGE_NAME]),
            "fields": "subject,details", "limit": 100}))
    by_id = {s["id"]: s for s in all_scores}
    latest = latest_policy_scores(all_scores)
    written, pending = 0, 0
    for key, obs in candidates.items():
        # Preserve history from before the split was installed. A turn with no
        # policy scores is unscored, not compliant; it has no derived headline.
        if key not in latest:
            pending += 1
            continue
        payloads = derived_scores(*key, latest[key], environment=obs.get("environment") or "production")
        numeric_id = score_id(*key, OVERALL_NAME)
        if not any(p["name"] == OVERALL_NAME for p in payloads) and numeric_id in by_id:
            # Only retract a score owned by THIS reducer when re-evaluation makes
            # it unresolved. Never delete an original/human evaluator score.
            api("DELETE", f"/api/public/scores/{numeric_id}")
        for payload in payloads:
            previous = by_id.get(payload["id"])
            if (previous and previous.get("value") == payload["value"]
                    and (previous.get("metadata") or {}).get("fingerprint") == payload["metadata"]["fingerprint"]):
                continue
            api("POST", "/api/public/scores", payload)
            written += 1
    return {"observations": len(candidates), "with_policy_scores": len(set(latest) & set(candidates)),
            "unscored": pending, "scores_written": written}
