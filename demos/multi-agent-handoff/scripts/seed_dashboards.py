"""
Seed the demo's two Langfuse dashboards as code (SPEC §12), unstable dashboards API.

    .venv/bin/python scripts/seed_dashboards.py            # create or reconcile both dashboards
    .venv/bin/python scripts/seed_dashboards.py --check    # run every widget's query (Metrics API v2): EMPTY / ERR
    .venv/bin/python scripts/seed_dashboards.py --delete

  "Handoffs — quality"   the loop's Monitor step: was each handoff right (judge), split by the prompt
                         versions the turn ran, so a label move shows up as a new row; the F1 blind spot
                         (customer-resolved stays green) beside it; payload, loops, failed handoffs, and
                         the F4 tripwire (production spans without a session)
  "Handoffs — agents"    cost, tokens and latency per agent; handoff latency; errors; model mix

Idempotent by name: an existing dashboard keeps its layout and gets only the widgets it is
missing; a widget whose definition drifted is PATCHed. A widget the API rejects is reported
and skipped, never aborting the rest. Everything is scoped to environment `production`, so
experiments (`sdk-experiment`) and the judges' own calls (`langfuse-llm-as-a-judge`) stay out.

What a widget cannot do (Metrics API v2, checked 2026-11-21): group by a metadata key. So
"handoffs by target" is one widget per target (a metadata *filter* works), and "by prompt
version" uses the trace version, which the orchestrator sets to the prompt versions the turn
ran (prompts.version_tag).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff.lfapi import LangfuseAPIError, api, paginate, ui  # noqa: E402

U = "/api/public/unstable"
PROD = {"column": "environment", "operator": "any of", "value": ["production"], "type": "stringOptions"}


def md(key: str, value: str) -> dict:
    return {"column": "metadata", "type": "stringObject", "key": key, "operator": "=", "value": value}


def score(name: str) -> list[dict]:
    return [{"column": "name", "operator": "any of", "value": [name], "type": "stringOptions"}, PROD]


def obs_type(t: str) -> dict:
    return {"column": "type", "operator": "any of", "value": [t], "type": "stringOptions"}


TURNS = [PROD, md("role", "orchestrator")]
HANDOFFS = [PROD, md("role", "handoff")]
GEN = [PROD, obs_type("GENERATION")]
AGENT_SPANS = [PROD, md("role", "agent")]
COUNT = [{"measure": "count", "agg": "count"}]
AVG = [{"measure": "value", "agg": "avg"}]
COST = [{"measure": "totalCost", "agg": "sum"}]
P95 = [{"measure": "latency", "agg": "p95"}]
VERSION = [{"field": "traceVersion"}]
BOOL = [{"field": "booleanValue"}]
NAME = [{"field": "name"}]

# (title, description, view, chart, dimensions, metrics, filters, x, y, width, height)
QUALITY = ("Handoffs — quality", "multi-agent-handoff: was each handoff right, and did the customer get "
           "resolved? Split by the prompt versions each turn ran, so a label move (a bug shipped, a fix "
           "promoted) is a new row. Production traffic only.", [
    ("Customer turns", "One support-turn trace per customer message", "observations", "NUMBER", [], COUNT,
     TURNS, 0, 0, 3, 3),
    ("Handoffs", "Every delegate, transfer, continue and peer hop", "observations", "NUMBER", [], COUNT,
     HANDOFFS, 3, 0, 3, 3),
    ("Failed handoffs", "Remote agent down, timed out or errored (fault F5)", "observations", "NUMBER", [],
     COUNT, HANDOFFS + [{"column": "level", "operator": "any of", "value": ["ERROR"], "type": "stringOptions"}],
     6, 0, 3, 3),
    ("Customer resolved (judge)", "customer-resolved, mean 0–1, on every turn", "scores-numeric", "NUMBER", [],
     AVG, score("customer-resolved"), 9, 0, 3, 3),
    ("Handoff judged right, by prompt versions", "handoff-appropriate (LLM judge, every handoff) per "
     "concierge / tech-support / billing prompt version. The F1 bug is a row with many false.",
     "scores-boolean", "PIVOT_TABLE", VERSION + BOOL, COUNT, score("handoff-appropriate"), 0, 3, 6, 5),
    ("Handoff judged right over time", "handoff-appropriate true vs false. Misroutes rise when a bad prompt "
     "ships and fall when the fix is promoted.", "scores-boolean", "BAR_TIME_SERIES", BOOL, COUNT,
     score("handoff-appropriate"), 6, 3, 6, 5),
    ("Customer resolved, by prompt versions", "The F1 blind spot: billing passes the credit on, so the "
     "end-to-end judge stays high while the handoff judge drops.", "scores-numeric", "HORIZONTAL_BAR",
     VERSION, AVG, score("customer-resolved"), 0, 8, 6, 5),
    ("Handoffs to billing over time", "Under F1 every money question goes to billing first",
     "observations", "BAR_TIME_SERIES", [], COUNT, HANDOFFS + [md("handoff_to", "billing")], 6, 8, 3, 5),
    ("Handoffs to tech-support over time", "Outage credits belong here first (tech-support confirms the "
     "outage)", "observations", "BAR_TIME_SERIES", [], COUNT, HANDOFFS + [md("handoff_to", "tech-support")],
     9, 8, 3, 5),
    ("Handoff payload complete", "handoff-context-complete (code evaluator): the target's required fields "
     "were in the payload (fault F2)", "scores-boolean", "PIE", BOOL, COUNT, score("handoff-context-complete"),
     0, 13, 4, 5),
    ("Turns that looped", "handoff-loop (code evaluator): ping-pong, a repeated hop, or the hop cap "
     "(fault F3)", "scores-boolean", "PIE", BOOL, COUNT, score("handoff-loop"), 4, 13, 4, 5),
    ("Handoff answered", "handoff-succeeded (code evaluator): the remote agent completed the task (fault F5)",
     "scores-boolean", "PIE", BOOL, COUNT, score("handoff-succeeded"), 8, 13, 4, 5),
    ("Spans without a session (F4 tripwire)", "Production observations with no sessionId: a remote hop lost "
     "the baggage header, so its spans fall out of the conversation", "observations", "BAR_TIME_SERIES",
     [], COUNT, [PROD, {"column": "sessionId", "type": "null", "operator": "is null", "value": ""}],
     0, 18, 6, 5),
    ("Handoffs per turn, by prompt versions", "handoff-count (code evaluator), mean", "scores-numeric",
     "HORIZONTAL_BAR", VERSION, AVG, score("handoff-count"), 6, 18, 6, 5),
])

AGENTS = ("Handoffs — agents", "multi-agent-handoff: cost, tokens and latency per agent, and what the "
          "handoffs themselves cost in time. Production traffic only.", [
    ("LLM cost by agent (USD)", "Model calls per agent (<agent>-llm generations)", "observations",
     "HORIZONTAL_BAR", NAME, COST, GEN, 0, 0, 6, 5),
    ("p95 LLM latency by agent (ms)", "One model call", "observations", "HORIZONTAL_BAR", NAME, P95, GEN,
     6, 0, 6, 5),
    ("p95 agent latency (ms)", "An agent's whole turn, tools and onward handoffs included", "observations",
     "HORIZONTAL_BAR", NAME, P95, AGENT_SPANS, 0, 5, 6, 5),
    ("Tokens by agent", "Input + output tokens per agent", "observations", "HORIZONTAL_BAR", NAME,
     [{"measure": "totalTokens", "agg": "sum"}], GEN, 6, 5, 6, 5),
    ("p95 handoff latency (ms)", "From handing off to the answer coming back (A2A round trip included)",
     "observations", "NUMBER", [], P95, HANDOFFS, 0, 10, 3, 3),
    ("p95 turn latency (ms)", "Customer message to reply", "observations", "NUMBER", [], P95, TURNS, 3, 10, 3, 3),
    ("LLM spend (USD)", "All agents' model calls (judges excluded)", "observations", "NUMBER", [], COST, GEN,
     6, 10, 3, 3),
    ("Cost by model", "claude-haiku-5-5 routes; claude-sonnet-5-5 does the specialist work", "observations",
     "PIE", [{"field": "providedModelName"}], COST, GEN, 9, 10, 3, 3),
    ("LLM spend over time (USD)", "", "observations", "BAR_TIME_SERIES", [], COST, GEN, 0, 13, 6, 5),
    ("Warnings and errors by observation", "Observations at WARNING or ERROR level", "observations",
     "HORIZONTAL_BAR", NAME, COUNT,
     [PROD, {"column": "level", "operator": "any of", "value": ["WARNING", "ERROR"], "type": "stringOptions"}],
     6, 13, 6, 5),
])

BOARDS = (QUALITY, AGENTS)


def query(view, chart, dims, metrics, filters) -> list[dict]:
    q = {"view": view, "dimensions": dims, "filters": filters,
         "metrics": [{"measure": m["measure"], "aggregation": m["agg"]} for m in metrics],
         "fromTimestamp": "2026-11-01T00:00:00Z", "toTimestamp": "2027-01-01T00:00:00Z"}
    if chart.endswith("TIME_SERIES"):
        q["timeDimension"] = {"granularity": "day"}
    return api("GET", "/api/public/v2/metrics", params={"query": json.dumps(q)}).get("data") or []


def check() -> int:
    bad = 0
    for name, _, widgets in BOARDS:
        print("==", name)
        for (title, _, view, chart, dims, metrics, filters, *_) in widgets:
            try:
                rows = query(view, chart, dims, metrics, filters)
                vals = [v for r in rows for k, v in r.items() if k not in ("time_dimension",) and isinstance(v, (int, float))]
                empty = not rows or not any(vals)
                bad += empty
                print(f"  {'EMPTY' if empty else 'OK   '} {title:<44} rows={len(rows):<3} {json.dumps(rows[:2])[:110]}")
            except LangfuseAPIError as e:
                bad += 1
                print(f"  ERR   {title:<44} {str(e)[:200]}")
    return 1 if bad else 0


def seed(board) -> None:
    name, desc, widgets = board
    dashes = {d["name"]: d for d in paginate(f"{U}/dashboards")}
    dash = dashes.get(name) or api("POST", f"{U}/dashboards", {"name": name, "description": desc})
    print(("= " if name in dashes else "+ ") + f"dashboard {name}")
    existing = {w["name"]: w for w in paginate(f"{U}/dashboard-widgets")}
    detail = api("GET", f"{U}/dashboards/{dash['id']}")
    placed = {p.get("widgetId") for p in ((detail.get("definition") or {}).get("widgets") or [])}
    for (title, wdesc, view, chart, dims, metrics, filters, x, y, w, h) in widgets:
        body = {"name": title, "description": wdesc, "view": view, "chartType": chart, "dimensions": dims,
                "metrics": metrics, "filters": filters, "chartConfig": {"type": chart}}
        try:
            cur = existing.get(title)
            if cur is None:
                wid = api("POST", f"{U}/dashboard-widgets", body)["id"]
            else:
                wid = cur["id"]
                if any(cur.get(k) != body[k] for k in ("description", "view", "chartType", "dimensions", "metrics",
                                                         "filters")):
                    api("PATCH", f"{U}/dashboard-widgets/{wid}", body)  # reconcile, never skip a drifted widget
                    print(f"  ~ {title} (definition updated)")
            if wid not in placed:
                api("POST", f"{U}/dashboards/{dash['id']}/placements",
                    {"type": "widget", "widgetId": wid, "x": x, "y": y, "width": w, "height": h})
                print(f"  + {title}")
            else:
                print(f"  = {title}")
        except LangfuseAPIError as exc:  # one bad widget must not abort the dashboard
            print(f"  ! {title}: {str(exc)[:220]}")
    print(f"  {ui('/dashboards/' + dash['id'])}")


def delete() -> None:
    names = {b[0] for b in BOARDS}
    titles = {w[0] for b in BOARDS for w in b[2]}
    for d in paginate(f"{U}/dashboards"):
        if d["name"] in names:
            api("DELETE", f"{U}/dashboards/{d['id']}")
            print("deleted dashboard", d["name"])
    for w in paginate(f"{U}/dashboard-widgets"):  # placements are gone with the dashboards
        if w["name"] in titles:
            api("DELETE", f"{U}/dashboard-widgets/{w['id']}")
            print("deleted widget", w["name"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run every widget query; exit 1 on EMPTY or ERR")
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.delete:
        delete()
        return 0
    for board in BOARDS:
        seed(board)
    return 0


if __name__ == "__main__":
    sys.exit(main())
