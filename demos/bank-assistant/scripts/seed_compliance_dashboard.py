"""Create/update a Langfuse conduct dashboard without deleting arranged widgets."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402
from northwind.compliance import COVERAGE_NAME, POLICIES, STATUS_NAME  # noqa: E402
from seed_business_dashboard import _free_slot  # noqa: E402

U = "/api/public/unstable"
NAME = "Northwind — Banking conduct compliance"
PREFIX = "NW-CONDUCT · "
PRODUCTION = {"column": "environment", "operator": "any of", "value": ["production"], "type": "stringOptions"}


def names(*values):
    return [{"column": "name", "operator": "any of", "value": list(values), "type": "stringOptions"}, PRODUCTION]


POLICY_FILTER = names(*(p.name for p in POLICIES))
COUNT = [{"measure": "count", "agg": "count"}]
STATUS = [{"field": "stringValue"}]
# Metric names are deliberately explicit about their denominators: a policy
# check is not a customer turn; completely unscored turns have no summary yet.
WIDGETS = [
    ("Conduct status", "One derived status per turn with policy results. Fully unscored turns are excluded, not passes.",
     "scores-categorical", "PIE", STATUS, COUNT, names(STATUS_NAME), 0, 0, 4, 5),
    ("Policy outcomes", "Separate P1-P5 verdicts: pass / fail / minor (P3) / unknown. Counts are policy checks, not turns.",
     "scores-categorical", "PIVOT_TABLE", [{"field": "name"}, {"field": "stringValue"}], COUNT, POLICY_FILTER, 4, 0, 8, 5),
    ("Policies with violations", "Failed checks by policy; one answer can violate multiple policies.",
     "scores-categorical", "HORIZONTAL_BAR", [{"field": "name"}], COUNT,
     POLICY_FILTER + [{"column": "stringValue", "operator": "any of", "value": ["fail"], "type": "stringOptions"}], 0, 5, 6, 5),
    ("Unknown checks", "Judges lacking the evidence needed to decide; requires review rather than a passing score.",
     "scores-categorical", "HORIZONTAL_BAR", [{"field": "name"}], COUNT,
     POLICY_FILTER + [{"column": "stringValue", "operator": "any of", "value": ["unknown"], "type": "stringOptions"}], 6, 5, 6, 5),
    ("Conduct status by prompt version", "Compare status counts per trace version. Review pending and unresolved alongside compliance.",
     "scores-categorical", "PIVOT_TABLE", [{"field": "traceVersion"}, {"field": "stringValue"}], COUNT, names(STATUS_NAME), 0, 10, 8, 5),
    ("Checks resolved per scored turn", "Mean fraction of five policies with known verdicts, among turns carrying a derived summary.",
     "scores-numeric", "NUMBER", [], [{"measure": "value", "agg": "avg"}], names(COVERAGE_NAME), 8, 10, 4, 3),
    ("Production assistant turns", "All completed or in-flight assistant observations in the selected time range; includes unscored turns.",
     "observations", "NUMBER", [], COUNT, names("northwind-assistant"), 8, 13, 4, 3),
]


def body(widget):
    title, description, view, chart, dimensions, metrics, filters, *_ = widget
    return {"name": PREFIX + title, "description": description, "view": view, "chartType": chart,
            "dimensions": dimensions, "metrics": metrics, "filters": filters, "chartConfig": {"type": chart}}


def main():
    listed = config.api("GET", f"{U}/dashboards", params={"limit": 100}).get("data", [])
    dash = next((d for d in listed if d["name"] == NAME), None)
    if dash is None:
        dash = config.api("POST", f"{U}/dashboards", {
            "name": NAME, "description": "One conduct decision, five policy verdicts, unresolved checks and evaluation coverage"})
    placed = (config.api("GET", f"{U}/dashboards/{dash['id']}").get("definition") or {}).get("widgets", [])
    taken = [{k: p[k] for k in ("x", "y", "width", "height")} for p in placed]
    have = {}
    for placement in placed:
        if placement.get("widgetId"):
            widget = config.api("GET", f"{U}/dashboard-widgets/{placement['widgetId']}")
            have[widget["name"]] = widget
    reusable = {w["name"]: w for w in config.api("GET", f"{U}/dashboard-widgets", params={"limit": 100}).get("data", [])}
    for spec in WIDGETS:
        wanted = body(spec)
        current = have.get(wanted["name"]) or reusable.get(wanted["name"])
        if current:
            wid = current["id"]
            if any(current.get(k) != v for k, v in wanted.items()):
                config.api("PATCH", f"{U}/dashboard-widgets/{wid}", wanted)
        else:
            wid = config.api("POST", f"{U}/dashboard-widgets", wanted)["id"]
        if wanted["name"] not in have:
            x, y, w, h = spec[7:]
            slot = _free_slot(x, y, w, h, taken)
            config.api("POST", f"{U}/dashboards/{dash['id']}/placements", {"type": "widget", "widgetId": wid, **slot})
            taken.append(slot)
        print("= " + wanted["name"])
    print(f"Dashboard: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/dashboards/{dash['id']}")


if __name__ == "__main__":
    main()
