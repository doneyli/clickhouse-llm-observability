"""Seed "Northwind — Business value & failure modes" (GATE-05, EVA-07) as code.

The business owner's view of the assistant: value delivered vs LLM spend,
containment, outcomes, failure modes — and the same metrics split by TRACE
VERSION (release + prompt version), which is how a canary of a new prompt is
compared with production on live traffic. Every number drills down to traces.

Idempotent by dashboard name (re-creates it). Unstable dashboards API.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

U = "/api/public/unstable"
NAME = "Northwind — Business value & failure modes"


def f(name):
    return [{"column": "name", "operator": "any of", "value": [name], "type": "stringOptions"}]


GEN = [{"column": "type", "operator": "any of", "value": ["GENERATION"], "type": "stringOptions"}]
NUM = lambda: {"type": "NUMBER"}  # noqa: E731

# (title, description, view, chart, dimensions, metrics, filters, x, y, w, h)
WIDGETS = [
    ("Value delivered (USD)", "Avoided contact-centre cost + advisor leads (demo assumptions — see northwind/business.py)",
     "scores-numeric", "NUMBER", [], [{"measure": "value", "agg": "sum"}], f("value-usd"), 0, 0, 3, 3),
    ("LLM spend (USD)", "Model cost of the assistant, judges excluded by environment filter",
     "observations", "NUMBER", [], [{"measure": "totalCost", "agg": "sum"}],
     GEN + [{"column": "environment", "operator": "any of", "value": ["production"], "type": "stringOptions"}], 3, 0, 3, 3),
    ("Containment rate", "Share of turns handled without a human (avg of `contained`)",
     "scores-numeric", "NUMBER", [], [{"measure": "value", "agg": "avg"}], f("contained"), 6, 0, 3, 3),
    ("Unsolicited upsell rate", "Share of turns that recommended a product the customer did not ask for (conduct risk)",
     "scores-numeric", "NUMBER", [], [{"measure": "value", "agg": "avg"}], f("unsolicited-upsell"), 9, 0, 3, 3),
    ("Outcome mix", "What happened for the customer, per turn", "scores-categorical", "HORIZONTAL_BAR",
     [{"field": "stringValue"}], [{"measure": "count", "agg": "count"}], f("task-outcome"), 0, 3, 4, 5),
    ("Failure modes", "Primary failure per turn (excluding 'none')", "scores-categorical", "HORIZONTAL_BAR",
     [{"field": "stringValue"}], [{"measure": "count", "agg": "count"}],
     f("failure-mode") + [{"column": "stringValue", "operator": "none of", "value": ["none"], "type": "stringOptions"}],
     4, 3, 4, 5),
    ("Intents", "What customers came for", "scores-categorical", "PIE",
     [{"field": "stringValue"}], [{"measure": "count", "agg": "count"}], f("intent"), 8, 3, 4, 5),
    ("Upsell rate by prompt version", "Production vs canary on live traffic", "scores-numeric", "VERTICAL_BAR",
     [{"field": "traceVersion"}], [{"measure": "value", "agg": "avg"}], f("unsolicited-upsell"), 0, 8, 3, 5),
    ("Compliance judge by prompt version", "banking-compliance (LLM judge) average", "scores-numeric", "VERTICAL_BAR",
     [{"field": "traceVersion"}], [{"measure": "value", "agg": "avg"}], f("banking-compliance"), 3, 8, 3, 5),
    ("Advisor offered by prompt version", "Investment questions: share offered/booked a licensed advisor",
     "scores-numeric", "VERTICAL_BAR", [{"field": "traceVersion"}], [{"measure": "value", "agg": "avg"}],
     f("advisor-offered"), 6, 8, 3, 5),
    ("Customer warned after sharing card data", "Share of turns with pasted card/ID data where the customer was told not to share it",
     "scores-numeric", "VERTICAL_BAR", [{"field": "traceVersion"}], [{"measure": "value", "agg": "avg"}],
     f("pii-education"), 9, 8, 3, 5),
    ("Formal Spanish (usted) by prompt version", "formal-register on Spanish turns: true vs false",
     "scores-boolean", "PIVOT_TABLE", [{"field": "traceVersion"}, {"field": "booleanValue"}],
     [{"measure": "count", "agg": "count"}], f("formal-register"), 0, 13, 6, 5),
    ("Value delivered over time", "Daily value delivered (USD)", "scores-numeric", "BAR_TIME_SERIES",
     [], [{"measure": "value", "agg": "sum"}], f("value-usd"), 6, 13, 6, 5),
]


def main():
    dashes = config.api("GET", f"{U}/dashboards", params={"limit": 100}).get("data", [])
    old = next((d for d in dashes if d.get("name") == NAME), None)
    if old:
        print(f"= dashboard exists ({old['id']}) — adding a fresh copy is not needed; delete it in the UI to re-seed")
        print(f"Dashboard: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/dashboards/{old['id']}")
        return
    dash = config.api("POST", f"{U}/dashboards", {"name": NAME, "description":
                      "Value vs spend, containment, outcomes, failure modes — and each split by prompt version"})
    print(f"+ dashboard {dash['id']}")
    for title, desc, view, chart, dims, metrics, filters, x, y, w, h in WIDGETS:
        body = {"name": f"NW-BIZ · {title}", "description": desc, "view": view, "chartType": chart,
                "dimensions": dims, "metrics": metrics, "filters": filters, "chartConfig": {"type": chart}}
        try:
            wid = config.api("POST", f"{U}/dashboard-widgets", body)["id"]
        except RuntimeError as e:
            if len(dims) > 1:  # fall back to one dimension if the view rejects two
                body["dimensions"] = dims[:1]
                try:
                    wid = config.api("POST", f"{U}/dashboard-widgets", body)["id"]
                except RuntimeError as e2:
                    print(f"  ! {title}: {str(e2)[:200]}"); continue
            else:
                print(f"  ! {title}: {str(e)[:200]}"); continue
        config.api("POST", f"{U}/dashboards/{dash['id']}/placements",
                   {"type": "widget", "widgetId": wid, "x": x, "y": y, "width": w, "height": h})
        print(f"  + {title}")
    print(f"Dashboard: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/dashboards/{dash['id']}")


if __name__ == "__main__":
    main()
