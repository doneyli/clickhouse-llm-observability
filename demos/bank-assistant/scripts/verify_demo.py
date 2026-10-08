"""End-to-end readiness check for the workshop: every artifact the demo script relies on.

Checks the Langfuse project through the public API (prompts, datasets, runs,
evaluators, rules, recent judge executions, score configs, annotation queue,
dashboard, traces per channel) plus the local services (MCP server, portal,
APM stand-in, n8n). Exit 1 if any REQUIRED check fails.

Run: .venv/bin/python scripts/verify_demo.py [--hours 24]
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

FAIL, WARN = [], []


def check(ok: bool, name: str, detail: str = "", required: bool = True):
    tag = "PASS" if ok else ("FAIL" if required else "WARN")
    print(f"[{tag}] {name}{' — ' + detail if detail else ''}", flush=True)
    if not ok:
        (FAIL if required else WARN).append(name)


def get(path, **params):
    return config.api("GET", path, params=params or None)


def cursor_all(path, **params):
    out, cur = [], None
    while True:
        d = get(path, limit=100, **params, **({"cursor": cur} if cur else {}))
        out += d.get("data") or []
        meta = d.get("meta") or {}
        cur = meta.get("cursor") or meta.get("nextCursor")
        if not cur or not d.get("data"):
            return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    a = ap.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(hours=a.hours)).isoformat()
    print(f"Langfuse {config.LANGFUSE_BASE_URL} · project {config.project_id()} · window {a.hours}h\n")

    # ── prompts ──
    labels = {}
    meta = get("/api/public/v2/prompts", name="northwind-assistant-system")["data"]
    for v in (meta[0]["versions"] if meta else []):
        for l in get(f"/api/public/v2/prompts/northwind-assistant-system", version=v).get("labels", []):
            labels[l] = v
    check("production" in labels, f"prompt production → v{labels.get('production')}", f"labels={labels}")
    check(labels.get("baseline") == 1, "prompt baseline → v1 (the version with the four business issues)",
          required=False)
    check(labels.get("staging") == 5, "prompt staging → v5 (release candidate)", required=False)
    check("staging" in labels and "development" in labels, "prompt staging + development labels exist")

    # ── datasets + runs ──
    expect = {"northwind-golden-qa-v1": 16, "northwind-golden-qa-es-v1": 10, "northwind-redteam-v1": 10,
              "judge-calibration/faithfulness": 15}
    for ds, n in expect.items():
        q = quote(ds, safe="")
        try:
            d = get(f"/api/public/v2/datasets/{q}")
            items = get("/api/public/dataset-items", datasetName=ds, limit=50)["data"]
            check(len(items) >= n, f"dataset {ds}", f"{len(items)} items")
            runs = [r["name"] for r in get(f"/api/public/datasets/{q}/runs", limit=50)["data"]]
            check(bool(runs), f"  runs on {ds}", ", ".join(sorted(runs))[:300])
        except RuntimeError as e:
            check(False, f"dataset {ds}", str(e)[:120])
    golden_runs = [r["name"] for r in get("/api/public/datasets/northwind-golden-qa-v1/runs", limit=50)["data"]]
    for want in ["production · claude-sonnet-4-6", "staging · claude-sonnet-4-6", "production · gpt-4.1"]:
        check(want in golden_runs, f"  golden run '{want}'")

    # ── evaluators + rules ──
    rules = cursor_all("/api/public/v2/evaluation-rules")
    by = {r["name"]: r for r in rules}
    for name in ["faithfulness", "banking-compliance", "manipulation-resistance"]:
        r = by.get(name)
        check(bool(r) and r.get("enabled") is True, f"rule {name} enabled",
              f"sampling={r.get('sampling') if r else None}")
    check(len(get("/api/public/score-configs", limit=50)["data"]) >= 4, "score configs registered")

    # ── recent scores: are judges executing? ──
    names = {}
    for s in cursor_all("/api/public/v3/scores", fromTimestamp=since, fields="core"):
        names.setdefault(s["name"], [0, None])
        names[s["name"]][0] += 1
        names[s["name"]][1] = max(names[s["name"]][1] or "", s.get("timestamp") or "")
    for n in ["faithfulness", "banking-compliance", "manipulation-resistance"]:
        check(n in names, f"judge scores: {n}", f"n={names.get(n, [0])[0]} latest={names.get(n, [0, None])[1]}")
    for n in ["security-risk", "guardrail-blocked", "pii-in-input", "language-match", "formal-register",
              "user-feedback", "cites-sources", "task-outcome", "contained", "value-usd", "failure-mode",
              "unsolicited-upsell", "advisor-offered", "pii-education"]:
        check(n in names, f"app scores: {n}", f"n={names.get(n, [0])[0]}")

    # ── annotation queue ──
    qs = get("/api/public/annotation-queues", limit=50)["data"]
    q = next((x for x in qs if x["name"].startswith("SME review")), None)
    n_items = len(get(f"/api/public/annotation-queues/{q['id']}/items", limit=100)["data"]) if q else 0
    check(n_items >= 10, "SME annotation queue", f"{n_items} items")

    # ── dashboard ──
    try:
        dashes = get("/api/public/unstable/dashboards", limit=50).get("data", [])
        check(any(d.get("name", "").startswith("Northwind — AI quality") for d in dashes), "quality dashboard")
        check(any(d.get("name", "").startswith("Northwind — Business value") for d in dashes), "business value dashboard")
    except RuntimeError as e:
        check(False, "Northwind dashboard", str(e)[:100], required=False)

    # ── traces per channel ──
    for trace_name in ["northwind-assistant", "northwind-voice-call", "n8n-complaint-triage"]:
        obs = get("/api/public/v2/observations", traceName=trace_name, fromStartTime=since, limit=50,
                  fields="core")["data"]
        check(len(obs) > 0, f"traces '{trace_name}' in window", f"{len(obs)}+ observations")

    # ── local services ──
    for name, url, req in [("portal", "http://localhost:8090/", True), ("MCP server", "http://localhost:8765/mcp", True),
                           ("APM stand-in (Jaeger)", "http://localhost:16686/", True), ("n8n", "http://localhost:5678/", True)]:
        try:
            code = httpx.get(url, timeout=5).status_code
            check(code < 500 or name == "MCP server", f"{name} up", f"HTTP {code}", required=req)
        except Exception as e:  # noqa: BLE001
            check(False, f"{name} up", type(e).__name__, required=req)

    print(f"\n{len(FAIL)} required failure(s), {len(WARN)} warning(s)")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)
    print("READY")


if __name__ == "__main__":
    main()
