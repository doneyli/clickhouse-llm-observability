"""
Seed the claims dashboards as code (unstable dashboards API). Idempotent by dashboard name: an existing
dashboard keeps its layout and only gets the widgets it is missing (matched by widget name). A widget the
API rejects is reported and skipped, never aborting the rest.

  "Claims — value & quality"   the business owner's view: claims processed, examiner decisions, value vs
                               LLM spend (labelled assumptions, claims/business.py), quality by trace version
                               (release · prompt version) — how a canary or a bad release shows up
  "Claims — agent report card" the platform view: per-agent latency, cost by model, errors, and the
                               platform-level checks (PII in letters, prompt injection, judges)

    .venv/bin/python scripts/seed_dashboards.py [--delete]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import config  # noqa: E402

U = "/api/public/unstable"
ROOT = "claims-orchestrator"


def score(name: str) -> list[dict]:
    """Scores of PRODUCTION claims only. App scores written during experiments carry the client's environment,
    so environment cannot separate them; the trace name and the `experiment` tag can."""
    return [{"column": "name", "operator": "any of", "value": [name], "type": "stringOptions"},
            {"column": "traceName", "operator": "any of", "value": [ROOT], "type": "stringOptions"},
            {"column": "tags", "operator": "none of", "value": ["experiment"], "type": "arrayOptions"}]


def service_score(name: str) -> list[dict]:
    return [{"column": "name", "operator": "any of", "value": [name], "type": "stringOptions"},
            {"column": "traceName", "operator": "any of", "value": ["annuity-service"], "type": "stringOptions"},
            {"column": "tags", "operator": "none of", "value": ["experiment"], "type": "arrayOptions"}]


def tag(*values: str) -> list[dict]:
    return [{"column": "tags", "operator": "any of", "value": list(values), "type": "arrayOptions"}]


PROD = [{"column": "environment", "operator": "any of", "value": ["production"], "type": "stringOptions"}]
GEN = [{"column": "type", "operator": "any of", "value": ["GENERATION"], "type": "stringOptions"}] + PROD
RECS = [{"column": "name", "operator": "any of", "value": [ROOT], "type": "stringOptions"}] + PROD + \
    tag("event:documents_received")
TV = [{"field": "traceVersion"}]
AVG = [{"measure": "value", "agg": "avg"}]
COUNT = [{"measure": "count", "agg": "count"}]
STEPS = ["doc-intake", "policy-coverage", "sop-rules", "adjudication", "correspondence", "examiner-assistant"]

VALUE = ("Claims — value & quality", "Business view of the death-claims agents. Value figures use labelled demo "
         "assumptions (claims/business.py); replace them with measured baselines in a POC.", [
    ("Claims processed", "Recommendations produced (production)", "observations", "NUMBER", [], COUNT, RECS, 0, 0, 3, 3),
    ("LLM spend (USD)", "All model calls of the claims agents in production (judges excluded)", "observations", "NUMBER",
     [], [{"measure": "totalCost", "agg": "sum"}], GEN, 3, 0, 3, 3),
    ("Examiner time saved (min)", "Sum of examiner-minutes-saved — demo assumption", "scores-numeric", "NUMBER", [],
     [{"measure": "value", "agg": "sum"}], score("examiner-minutes-saved"), 6, 0, 3, 3),
    ("Value delivered (USD)", "Sum of value-usd — examiner time at a loaded rate; demo assumption", "scores-numeric",
     "NUMBER", [], [{"measure": "value", "agg": "sum"}], score("value-usd"), 9, 0, 3, 3),
    ("Examiner decisions", "Approved as recommended (false) vs overridden (true)", "scores-boolean", "PIE",
     [{"field": "booleanValue"}], COUNT, score("examiner-override"), 0, 3, 4, 5),
    ("Why examiners override", "override-reason — the error-analysis taxonomy", "scores-categorical", "HORIZONTAL_BAR",
     [{"field": "stringValue"}], COUNT, score("override-reason"), 4, 3, 4, 5),
    ("Recommendation mix", "PAY / PEND / DENY / REFER_SIU", "scores-categorical", "PIE", [{"field": "stringValue"}],
     COUNT, score("decision"), 8, 3, 4, 5),
    ("Critical rules covered, by version", "critical-rules-covered (codified guard) per release · prompt version",
     "scores-boolean", "PIVOT_TABLE", TV + [{"field": "booleanValue"}], COUNT, score("critical-rules-covered"),
     0, 8, 6, 5),
    ("Rule completeness (judge), by version", "rule-completeness LLM judge per release · prompt version",
     "scores-boolean", "PIVOT_TABLE", TV + [{"field": "booleanValue"}], COUNT, score("rule-completeness"), 6, 8, 6, 5),
    ("Retrieval coverage by version", "Share of tagged SOPs the retriever returned (1.0 = all)", "scores-numeric",
     "VERTICAL_BAR", TV, AVG, score("retrieval-coverage"), 0, 13, 4, 5),
    ("Required notices in letters, by version", "disclosures-present (state and plan notices)", "scores-numeric",
     "VERTICAL_BAR", TV, AVG, score("disclosures-present"), 4, 13, 4, 5),
    ("Cost per claim by version (USD)", "claim-cost-usd — orchestrator-side LLM calls at list price", "scores-numeric",
     "VERTICAL_BAR", TV, AVG, score("claim-cost-usd"), 8, 13, 4, 5),
    ("Value delivered over time", "Value delivered (USD, assumption) per hour — spend is the LLM spend tile", "scores-numeric", "BAR_TIME_SERIES", [],
     [{"measure": "value", "agg": "sum"}], score("value-usd"), 0, 18, 6, 5),
    ("Guard interventions over time", "Claims where the critical-rule guard added a rule the agent missed",
     "scores-boolean", "BAR_TIME_SERIES", [{"field": "booleanValue"}], COUNT, score("guard-triggered"), 6, 18, 6, 5),
    ("Annuity service requests by decision", "Retirement / annuities: APPROVE / PEND / REFER_REVIEW",
     "scores-categorical", "PIE", [{"field": "stringValue"}], COUNT, service_score("decision"), 0, 23, 4, 5),
    ("Annuity servicing: critical rules covered", "Replacement in the surrender period, contract under 36 months, "
     "horizon shorter than the new surrender period", "scores-boolean", "PIE", [{"field": "booleanValue"}], COUNT,
     service_score("critical-rules-covered"), 4, 23, 4, 5),
    ("Annuity servicing: rule completeness (judge)", "rule-completeness judge on service requests", "scores-boolean",
     "PIE", [{"field": "booleanValue"}], COUNT, service_score("rule-completeness"), 8, 23, 4, 5),
])

REPORT = ("Claims — agent report card", "Platform view: the same score names in every project make this the agent "
          "report card — latency, cost, errors and the platform-level checks per agent.", [
    ("p95 latency by agent (s)", "Per step observation", "observations", "HORIZONTAL_BAR", [{"field": "name"}],
     [{"measure": "latency", "agg": "p95"}],
     [{"column": "name", "operator": "any of", "value": STEPS, "type": "stringOptions"}] + PROD, 0, 0, 6, 5),
    ("LLM cost by model (USD)", "Production generations", "observations", "PIE", [{"field": "providedModelName"}],
     [{"measure": "totalCost", "agg": "sum"}], GEN, 6, 0, 6, 5),
    ("Warnings and errors by step", "Observations at WARNING or ERROR level", "observations", "HORIZONTAL_BAR",
     [{"field": "name"}], COUNT, [{"column": "level", "operator": "any of", "value": ["WARNING", "ERROR"],
                                   "type": "stringOptions"}] + PROD, 0, 5, 6, 5),
    ("Tokens by agent", "Total tokens per LLM call site (run name)", "observations", "HORIZONTAL_BAR",
     [{"field": "name"}], [{"measure": "totalTokens", "agg": "sum"}], GEN, 6, 5, 6, 5),
    ("Citation validity", "Cited rules exist, are in scope and in effect (avg)", "scores-numeric", "NUMBER", [], AVG,
     score("citation-validity"), 0, 10, 3, 3),
    ("Policy agent tool trajectory", "Required MCP tools called (avg)", "scores-numeric", "NUMBER", [], AVG,
     score("tool-trajectory"), 3, 10, 3, 3),
    ("Adjudication faithfulness (judge)", "avg 0-1", "scores-numeric", "NUMBER", [], AVG,
     score("adjudication-faithfulness"), 6, 10, 3, 3),
    ("Letter quality (judge)", "avg 0-1 — platform check on customer-facing text", "scores-numeric", "NUMBER", [], AVG,
     score("letter-quality"), 9, 10, 3, 3),
    ("No PII in letters", "Platform check: SSN, DOB, bank, card patterns in claimant letters", "scores-boolean", "PIE",
     [{"field": "booleanValue"}], COUNT, score("no-pii-in-letter"), 0, 13, 4, 5),
    ("Embedded instructions resisted (judge)", "Claims whose documents addressed the system", "scores-boolean", "PIE",
     [{"field": "booleanValue"}], COUNT, score("instruction-resistance"), 4, 13, 4, 5),
    ("Certificate extraction (multi-modal judge)", "Intake fields match the scanned certificate", "scores-boolean",
     "PIE", [{"field": "booleanValue"}], COUNT, score("certificate-extraction"), 8, 13, 4, 5),
    ("Letter reading level", "Flesch-Kincaid grade (target ≤ 9)", "scores-numeric", "NUMBER", [], AVG,
     score("reading-level"), 0, 18, 3, 3),
    ("Claim latency p95 (s)", "End to end, recommendation", "scores-numeric", "NUMBER", [],
     [{"measure": "value", "agg": "p95"}], score("claim-latency-s"), 3, 18, 3, 3),
])


def paginate(path: str) -> list[dict]:
    out, page = [], 1
    while True:
        d = config.api("GET", path, params={"page": page, "limit": 100})
        out += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return out
        page += 1


def seed(board) -> None:
    name, desc, widgets = board
    dashes = {d["name"]: d for d in paginate(f"{U}/dashboards")}
    dash = dashes.get(name) or config.api("POST", f"{U}/dashboards", {"name": name, "description": desc})
    print(("= " if name in dashes else "+ ") + f"dashboard {name}")
    existing = {w["name"]: w for w in paginate(f"{U}/dashboard-widgets")}
    detail = config.api("GET", f"{U}/dashboards/{dash['id']}")
    placed = {p.get("widgetId") for p in (detail.get("definition") or detail).get("widgets", []) or []} \
        if isinstance(detail, dict) else set()
    for (title, wdesc, view, chart, dims, metrics, filters, x, y, w, h) in widgets:
        try:
            body = {"name": title, "description": wdesc, "view": view, "chartType": chart, "dimensions": dims,
                    "metrics": metrics, "filters": filters, "chartConfig": {"type": chart}}
            cur = existing.get(title)
            if cur is None:
                wid = config.api("POST", f"{U}/dashboard-widgets", body)["id"]
            else:
                wid = cur["id"]
                if any(cur.get(k) != body[k] for k in ("description", "view", "chartType", "dimensions", "metrics",
                                                         "filters")):
                    config.api("PATCH", f"{U}/dashboard-widgets/{wid}", body)  # reconcile, never skip a drifted widget
                    print(f"  ~ {title} (definition updated)")
            if wid not in placed:
                config.api("POST", f"{U}/dashboards/{dash['id']}/placements",
                           {"type": "widget", "widgetId": wid, "x": x, "y": y, "width": w, "height": h})
                print(f"  + {title}")
            else:
                print(f"  = {title}")
        except Exception as exc:  # noqa: BLE001 — one bad widget must not abort the dashboard
            print(f"  ! {title}: {str(exc)[:220]}")
    print(f"  {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/dashboards/{dash['id']}")


def main() -> int:
    if "--delete" in sys.argv:
        for d in paginate(f"{U}/dashboards"):
            if d["name"] in (VALUE[0], REPORT[0]):
                config.api("DELETE", f"{U}/dashboards/{d['id']}")
                print("deleted", d["name"])
        return 0
    for board in (VALUE, REPORT):
        seed(board)
    return 0


if __name__ == "__main__":
    sys.exit(main())
