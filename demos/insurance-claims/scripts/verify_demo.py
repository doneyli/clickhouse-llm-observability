"""
Demo readiness — reads Langfuse BACK and checks the state every act of DEMO_SCRIPT.md depends on.
HTTP 200 proves nothing on its own (a trace can exist with no observations yet), so every check counts
real rows.

    .venv/bin/python scripts/verify_demo.py            # Cloud (or CLAIMS_PROFILE=selfhosted)
    .venv/bin/python scripts/verify_demo.py --links    # also print fresh links for each beat

Exit 1 when a check fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import config, lfread  # noqa: E402

SINCE = "2026-10-01T00:00:00Z"
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> bool:
    RESULTS.append((ok, what))
    print(("  ✓ " if ok else "  ✗ ") + what)
    return ok


def obs(filters: list[dict], fields: str = "core,basic", cap: int = 2000) -> list[dict]:
    f = filters + [{"type": "datetime", "column": "startTime", "operator": ">=", "value": SINCE}]
    return lfread.paged("/api/public/v2/observations", {"filter": json.dumps(f), "fields": fields}, cap=cap)


def scores(name: str, cap: int = 2000) -> list[dict]:
    return lfread.paged("/api/public/v3/scores", {"name": name, "fields": "core,subject", "fromTimestamp": SINCE},
                        cap=cap)


def main() -> int:
    links = "--links" in sys.argv
    print(f"Target: {config.LANGFUSE_BASE_URL} (profile {config.PROFILE}, project {config.project_id()})")
    print("Services")
    for name, url in [("policy-admin MCP", config.MCP_URL.replace("/mcp", "/mcp")),
                      ("doc-intake A2A", config.DOC_INTAKE_URL + "/.well-known/agent-card.json"),
                      ("workbench", f"http://127.0.0.1:{config.PORTAL_PORT}/api/claims"),
                      ("APM (Jaeger)", config.APM_UI_TRACE_URL.split("/trace/")[0] + "/")]:
        try:
            r = httpx.request("POST" if "mcp" in url and "agent-card" not in url else "GET", url, timeout=4)
            check(r.status_code < 500, f"{name} reachable ({r.status_code})")
        except Exception as exc:  # noqa: BLE001
            check(False, f"{name} not reachable: {type(exc).__name__}")

    print("Prompts")
    want = ["doc-intake", "claims-policy-coverage", "claims-sop-rules", "claims-adjudication",
            "claims-correspondence", "claims-examiner-assistant"]
    have = {p["name"]: p for p in config.api("GET", "/api/public/v2/prompts", params={"limit": 50})["data"]}
    check(all(n in have for n in want), f"{len([n for n in want if n in have])}/{len(want)} agent prompts")
    labels = set((have.get("claims-sop-rules") or {}).get("labels") or [])
    check({"production", "staging", "conservative"} <= labels, f"claims-sop-rules labels: {sorted(labels)}")

    print("Evaluation setup")
    evs = {e["name"] for e in lfread.paged("/api/public/v2/evaluators", {})}
    rules = {r["name"]: r for r in lfread.paged("/api/public/v2/evaluation-rules", {})}
    judges = ["rule-completeness", "adjudication-faithfulness", "letter-quality", "instruction-resistance",
              "certificate-extraction"]
    check(all(j in evs and j in rules and rules[j].get("enabled") for j in judges), f"{len(judges)} judges + rules enabled")
    for ds, n in [("claims-golden-v1", 92), ("claims-adversarial-v1", 12), ("claims-intake-v1", 24),
                  ("annuity-service-golden-v1", 22)]:
        try:
            got = len(config.get_langfuse().get_dataset(ds).items)
        except Exception:  # noqa: BLE001
            got = 0
        check(got >= n, f"dataset {ds}: {got} items")
    exps = lfread.experiments("claims-golden-v1")
    names = [e["name"] for e in exps]
    needles = ["production v1", "staging v2"] + ([] if config.PROFILE == "selfhosted" else
                                                 ["conservative v3", "claude-sonnet-4-6", "claude-haiku-5-5",
                                                  "claims-1.4.0"])
    for needle in needles:
        check(any(needle in n for n in names), f"experiment run with '{needle}'")

    print("Production traffic")
    recs = obs([{"type": "arrayOptions", "column": "tags", "operator": "any of", "value": ["event:documents_received"]},
                {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
                {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]}])
    n = 20 if config.PROFILE != "selfhosted" else 10
    check(len(recs) >= n, f"{len(recs)} recommendations in production")
    for name, minimum in [("examiner-override", n), ("value-usd", n), ("critical-rules-covered", n),
                          ("guard-triggered", n), ("rule-completeness", n // 2), ("adjudication-faithfulness", n // 2),
                          ("letter-quality", n // 2), ("certificate-extraction", n // 2), ("instruction-resistance", 1)]:
        rows = scores(name, cap=500)
        check(len(rows) >= minimum, f"score {name}: {len(rows)}")
    guard = [s for s in scores("guard-triggered") if s.get("value") in (1, 1.0, True)]
    check(bool(guard), f"{len(guard)} claims where the critical-rule guard fired")
    sexp = [e["name"] for e in lfread.experiments("annuity-service-golden-v1")]
    check(any("production v1" in n for n in sexp) and any("staging v2" in n for n in sexp),
          f"annuity-service experiments: {len(sexp)} runs (production + staging)")
    queues = config.api("GET", "/api/public/annotation-queues", params={"limit": 50})["data"]
    qi = sum((config.api("GET", f"/api/public/annotation-queues/{q['id']}/items", params={"limit": 1}).get("meta") or {})
             .get("totalItems", 0) for q in queues)
    check(qi > 0, f"annotation queue items: {qi}")
    svc = obs([{"type": "stringOptions", "column": "traceName", "operator": "any of", "value": ["annuity-service"]},
               {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True}])
    check(len(svc) >= 10, f"{len(svc)} annuity-service traces")
    hero = obs([{"type": "stringOptions", "column": "sessionId", "operator": "any of", "value": ["CLM-2026-00042"]},
                {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True}])
    check(len(hero) >= 2, f"hero session CLM-2026-00042: {len(hero)} traces")

    if links:
        base = f"{config.LANGFUSE_BASE_URL}/project/{config.project_id()}"
        print("\nLinks")
        print("  hero session      ", config.session_url("CLM-2026-00042"))
        for h in sorted(hero, key=lambda r: r.get("startTime") or "")[-3:]:
            print("  hero trace        ", config.trace_url(h["traceId"]))
        for g in guard[:3]:
            print("  guard fired       ", config.trace_url((g.get("subject") or {}).get("traceId") or g.get("traceId")))
        for e in exps:
            print(f"  run  {e['name'][:70]:<70} {base}/datasets")
        print("  dashboards        ", f"{base}/dashboards")
        print("  evaluators        ", f"{base}/evals")
    failed = [w for ok, w in RESULTS if not ok]
    print(f"\n{'READY' if not failed else 'NOT READY'} — {len(RESULTS) - len(failed)}/{len(RESULTS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
