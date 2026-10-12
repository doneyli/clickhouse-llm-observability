"""Seed "Northwind — Business value & failure modes" (GATE-05, EVA-07) as code.

The business owner's view of the assistant: value delivered vs LLM spend,
containment, outcomes, failure modes — and the same metrics split by TRACE
VERSION (release + prompt version), which is how a canary of a new prompt is
compared with production on live traffic. Every number drills down to traces.

Idempotent by dashboard name: a new dashboard gets every widget; an EXISTING one
keeps its widgets and layout and only gets the widgets it is missing (matched by
widget name), placed in free rows below — so a story arc's widgets can be added
without re-creating a dashboard a presenter has already arranged. Nothing is
deleted. Unstable dashboards API.
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

# Story arcs 5 (disputes leak to humans) and 6 (cost regression after a "recall"
# release) — rows below the original layout (which ends at y=18).
TV = [{"field": "traceVersion"}]
AVG = [{"measure": "value", "agg": "avg"}]
# Each arc compares releases on the SAME replayed customer journeys: arc 5 replays the
# dispute conversations (tag scenario:disputes), arc 6 the core + Spanish ones
# (scripts/run_cost_arc.sh). Without the tag filter the bars mix in the other arc's
# traffic — dispute turns make more LLM calls per turn; core turns that stop to
# confirm a dispute lower the per-turn dispute rate — and the traffic mix, not the
# release, sets the bar height.
SAME_MIX = [{"column": "tags", "operator": "any of", "value": ["scenario:core", "scenario:es"], "type": "arrayOptions"}]
DISPUTE_MIX = [{"column": "tags", "operator": "any of", "value": ["scenario:disputes"], "type": "arrayOptions"}]
MIX_NOTE = " Same replayed journeys per release (tags scenario:core/es), so traffic mix does not confound it — arc 6"
WIDGETS += [
    ("Dispute self-service rate by trace version",
     "Dispute requests where the assistant actually opened the dispute in the turn (avg of `dispute-resolved`). "
     "Arc 5's replayed dispute journeys (tag scenario:disputes), the same in every release — arc 5",
     "scores-numeric", "VERTICAL_BAR", TV, AVG, f("dispute-resolved") + DISPUTE_MIX, 0, 18, 3, 5),
    ("Cost per turn (USD) by trace version",
     "List-price estimate from each turn's token usage (avg of `turn-cost-usd`); Langfuse-computed model cost "
     "is in LLM spend." + MIX_NOTE,
     "scores-numeric", "VERTICAL_BAR", TV, AVG, f("turn-cost-usd") + SAME_MIX, 3, 18, 3, 5),
    ("LLM calls per turn by trace version", "Model calls per turn (avg of `llm-calls`)." + MIX_NOTE,
     "scores-numeric", "VERTICAL_BAR", TV, AVG, f("llm-calls") + SAME_MIX, 6, 18, 3, 5),
    ("Turn latency (s) by trace version", "End-to-end seconds per turn (avg of `turn-latency-s`)." + MIX_NOTE,
     "scores-numeric", "VERTICAL_BAR", TV, AVG, f("turn-latency-s") + SAME_MIX, 9, 18, 3, 5),
    ("Cost per turn over time", "Avg `turn-cost-usd`, one line per trace version (release + prompt)." + MIX_NOTE,
     "scores-numeric", "LINE_TIME_SERIES", TV, AVG, f("turn-cost-usd") + SAME_MIX, 0, 23, 12, 5),
]


def _overlaps(a, b):
    return a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"] and \
        a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"]


def _free_slot(x, y, w, h, taken):
    """The declared slot, pushed down below anything it would overlap."""
    rect = {"x": x, "y": y, "width": w, "height": h}
    while True:
        hit = [t for t in taken if _overlaps(rect, t)]
        if not hit:
            return rect
        rect["y"] = max(t["y"] + t["height"] for t in hit)


def _body(title, desc, view, chart, dims, metrics, filters):
    return {"name": f"NW-BIZ · {title}", "description": desc, "view": view, "chartType": chart,
            "dimensions": dims, "metrics": metrics, "filters": filters, "chartConfig": {"type": chart}}


def _create_widget(title, desc, view, chart, dims, metrics, filters, reuse):
    """Create the widget (or reuse an unplaced one with the same name); falls back to one dimension."""
    name = f"NW-BIZ · {title}"
    if name in reuse:
        return reuse[name]
    body = _body(title, desc, view, chart, dims, metrics, filters)
    try:
        return config.api("POST", f"{U}/dashboard-widgets", body)["id"]
    except RuntimeError as e:
        if not dims:
            print(f"  ! {title}: {str(e)[:200]}"); return None
        body["dimensions"] = dims[:-1]  # a view may reject the (last) dimension — keep the chart
        try:
            wid = config.api("POST", f"{U}/dashboard-widgets", body)["id"]
            print(f"  ~ {title}: created without dimension {dims[-1]['field']} ({str(e)[:120]})")
            return wid
        except RuntimeError as e2:
            print(f"  ! {title}: {str(e2)[:200]}"); return None


def main():
    dashes = config.api("GET", f"{U}/dashboards", params={"limit": 100}).get("data", [])
    dash = next((d for d in dashes if d.get("name") == NAME), None)
    taken, have = [], set()
    if dash:
        placements = (config.api("GET", f"{U}/dashboards/{dash['id']}").get("definition") or {}).get("widgets", [])
        spec = {f"NW-BIZ · {w[0]}": w for w in WIDGETS}
        for p in placements:
            taken.append({k: p[k] for k in ("x", "y", "width", "height")})
            if not p.get("widgetId"):
                continue
            cur = config.api("GET", f"{U}/dashboard-widgets/{p['widgetId']}")
            have.add(cur.get("name"))
            want = spec.get(cur.get("name"))
            if want:  # keep a placed widget's definition in sync with this file — in place, never deleted
                body = _body(*want[:7])
                drift = [k for k in ("description", "view", "chartType", "dimensions", "metrics", "filters")
                         if cur.get(k) != body[k]]
                if drift:
                    config.api("PATCH", f"{U}/dashboard-widgets/{p['widgetId']}", body)
                    print(f"  ~ {want[0]}: updated {', '.join(drift)}")
        print(f"= dashboard exists ({dash['id']}) with {len(placements)} widgets — adding the missing ones")
    else:
        dash = config.api("POST", f"{U}/dashboards", {"name": NAME, "description":
                          "Value vs spend, containment, outcomes, failure modes — and each split by trace version"})
        print(f"+ dashboard {dash['id']}")
    # Widgets created by an earlier, interrupted run (exist, but not on this dashboard).
    listed = config.api("GET", f"{U}/dashboard-widgets", params={"limit": 100}).get("data", [])
    reuse = {w["name"]: w["id"] for w in listed if w.get("name", "").startswith("NW-BIZ · ") and w["name"] not in have}
    added = 0
    for title, desc, view, chart, dims, metrics, filters, x, y, w, h in WIDGETS:
        if f"NW-BIZ · {title}" in have:
            continue
        wid = _create_widget(title, desc, view, chart, dims, metrics, filters, reuse)
        if not wid:
            continue
        slot = _free_slot(x, y, w, h, taken)
        config.api("POST", f"{U}/dashboards/{dash['id']}/placements", {"type": "widget", "widgetId": wid, **slot})
        taken.append(slot)
        added += 1
        print(f"  + {title}  (x={slot['x']} y={slot['y']} {slot['width']}x{slot['height']})")
    print(f"{added} widget(s) added; {len(have)} already present")
    print(f"Dashboard: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/dashboards/{dash['id']}")


if __name__ == "__main__":
    main()
