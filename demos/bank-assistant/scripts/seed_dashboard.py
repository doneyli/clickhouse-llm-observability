"""Seed the "Northwind — AI quality, risk and cost" dashboard (EVA-07, OBS-05) as code.

Uses the (unstable) dashboards API. Idempotent by dashboard name: re-running
deletes and re-creates the dashboard's widgets.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

U = "/api/public/unstable"
NAME = "Northwind — AI quality, risk and cost"
ROOT = [{"column": "name", "operator": "any of", "value": ["northwind-assistant"], "type": "stringOptions"}]

WIDGETS = [  # (name, description, view, chartType, dimensions, metrics, filters, x, y, w, h)
    ("Judge scores over time", "Average faithfulness and banking-compliance (LLM judges, production)",
     "scores-numeric", "LINE_TIME_SERIES", [{"field": "name"}], [{"measure": "value", "agg": "avg"}],
     [{"column": "name", "operator": "any of", "value": ["faithfulness", "banking-compliance", "manipulation-resistance"],
       "type": "stringOptions"}], 0, 0, 6, 5),
    ("Customer feedback", "Thumbs up / down from the app", "scores-boolean", "PIE",
     [{"field": "booleanValue"}], [{"measure": "count", "agg": "count"}],
     [{"column": "name", "operator": "any of", "value": ["user-feedback"], "type": "stringOptions"}], 6, 0, 3, 5),
    ("Security risk mix", "Guardrail classification of every customer message", "scores-categorical", "HORIZONTAL_BAR",
     [{"field": "stringValue"}], [{"measure": "count", "agg": "count"}],
     [{"column": "name", "operator": "any of", "value": ["security-risk"], "type": "stringOptions"}], 9, 0, 3, 5),
    ("LLM cost by model", "Total cost of generations per model", "observations", "BAR_TIME_SERIES",
     [{"field": "providedModelName"}], [{"measure": "totalCost", "agg": "sum"}],
     [{"column": "type", "operator": "any of", "value": ["GENERATION"], "type": "stringOptions"}], 0, 5, 6, 5),
    ("Turn latency p95", "End-to-end latency of the assistant turn (root observation)", "observations",
     "LINE_TIME_SERIES", [], [{"measure": "latency", "agg": "p95"}], ROOT, 6, 5, 3, 5),
    ("Turns by environment", "Assistant turns per environment", "observations", "PIE",
     [{"field": "environment"}], [{"measure": "count", "agg": "count"}], ROOT, 9, 5, 3, 5),
]


def main():
    dashboards = config.api("GET", f"{U}/dashboards", params={"limit": 100}).get("data", [])
    existing = next((d for d in dashboards if d.get("name") == NAME), None)
    if existing:
        config.api("DELETE", f"{U}/dashboards/{existing['id']}")
        print(f"- removed old dashboard {existing['id']}")
    dash = config.api("POST", f"{U}/dashboards", {"name": NAME, "description":
                      "Online quality (LLM judges, customer feedback), security risk, cost and latency of the Northwind assistant"})
    print(f"+ dashboard {dash['id']}")
    for name, desc, view, chart, dims, metrics, filters, x, y, w, h in WIDGETS:
        body = {"name": f"NW · {name}", "description": desc, "view": view, "chartType": chart,
                "dimensions": dims, "metrics": metrics, "filters": filters, "chartConfig": {"type": chart}}
        try:
            wid = config.api("POST", f"{U}/dashboard-widgets", body)["id"]
            config.api("POST", f"{U}/dashboards/{dash['id']}/placements",
                       {"type": "widget", "widgetId": wid, "x": x, "y": y, "width": w, "height": h})
            print(f"  + {name}")
        except RuntimeError as e:  # one bad widget must not abort the dashboard
            print(f"  ! {name}: {str(e)[:300]}")
    print(f"Dashboard: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/dashboards/{dash['id']}")


if __name__ == "__main__":
    main()
