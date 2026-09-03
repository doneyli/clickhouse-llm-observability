#!/usr/bin/env python3
"""
Dashboards as code — a minimal, runnable walkthrough of the Langfuse API.

Read this one on screen. It builds ONE dashboard with TWO widgets and prints
every request and response as it goes, so the API contract is visible rather
than described. `scripts/seed_dashboards.py` is the same thing at production
scale (3 dashboards, 26 widgets, idempotent) — start here, graduate to that.

Deliberately self-contained: stdlib only, no imports from this demo, so it
lifts straight into another repo. The only local dependency is reading keys
out of `.env`.

    ./.venv/bin/python scripts/dashboard_api_example.py            # build it
    ./.venv/bin/python scripts/dashboard_api_example.py --delete   # clean up

Everything it creates is prefixed `[example]`, so it is obvious what to throw
away afterwards. Deliberately NOT idempotent — running it twice creates a
second copy, which keeps the code short enough to read aloud. `--delete`
between runs; `seed_dashboards.py` is the version that upserts by name.

---------------------------------------------------------------------------
THE SHAPE OF IT

Three endpoints, all under `/api/public/unstable`, and the order matters:

    1. POST /dashboard-widgets            -> a chart definition (reusable)
    2. POST /dashboards                   -> an empty container
    3. POST /dashboards/{id}/placements   -> put widget #1 on dashboard #2

Widgets are standalone on purpose: one person defines "p95 latency, filtered
to production" and every dashboard references that single definition instead
of six teams re-deriving it slightly differently.

The same endpoints back the Langfuse CLI and MCP server, so "dashboards as
code" and "ask the assistant for a dashboard" are the same API underneath.

These endpoints are **unstable** — the contract may change. Pin your SDK and
re-run your seeder in CI if you depend on it.

---------------------------------------------------------------------------
WHAT THE DOCS DON'T TELL YOU (all four cost real debugging time)

  * **Updates are PATCH, not PUT.** PUT returns 405.
  * **Placement sizes are `width`/`height`.** `x_size`/`y_size` — the names in
    the stored dashboard definition — are rejected as unrecognized keys. The
    placement body also needs `"type": "widget"`; without it you get an
    unhelpful "No matching discriminator".
  * **A widget sees fewer dimensions than the Metrics API does.** A query the
    Metrics API happily answers can still be refused at widget creation:
    `experimentName` and `datasetRunId` are queryable but not chartable (so
    experiment comparison stays in the Experiments UI), and `promptName` works
    as a *dimension* but not as a *filter column*. Validate against the Metrics
    API first — then be ready for the widget endpoint to say no anyway.
  * **`limit` on the list endpoints caps at 100.** Asking for 200 is a 400.
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DASHBOARD_NAME = "[example] Dashboards as code"
UNSTABLE = "/api/public/unstable"

BOLD, DIM, GREEN, RED, CYAN, OFF = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[0m")


# --------------------------------------------------------------- tiny client

def load_keys() -> "tuple[str, str, str]":
    """Read host + keys from this folder's .env, ignoring the shell.

    Shell-exported LANGFUSE_* keys outrank .env in most tooling, and a key for
    another project will happily create these dashboards over there.
    """
    env: "dict[str, str]" = {}
    path = Path(__file__).resolve().parent.parent / ".env"
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("\"'")
    host = (env.get("LANGFUSE_HOST") or "http://localhost:3001").rstrip("/")
    return host, env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"]


HOST, PUBLIC_KEY, SECRET_KEY = load_keys()
AUTH = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()


class ApiError(RuntimeError):
    """An HTTP error that keeps the response body.

    Worth copying: Langfuse's 4xx bodies are the most useful documentation
    there is — a rejected field name comes back with every accepted one — and
    a client that stringifies or truncates them throws that away.
    """

    def __init__(self, method: str, path: str, status: int, raw: str) -> None:
        try:
            self.body = json.loads(raw)
        except ValueError:
            self.body = {"message": raw}
        self.status = status
        super().__init__(f"{method} {path} -> {status}: "
                         f"{self.body.get('message', raw)}")


def call(method: str, path: str, body: dict = None, query: dict = None,
         *, show: bool = True):
    """One HTTP call, printed. This is the whole client — there is no SDK here."""
    url = f"{HOST}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    if show:
        print(f"\n  {CYAN}{method} {path}{OFF}")
        if body is not None:
            for line in json.dumps(body, indent=2).splitlines():
                print(f"  {DIM}│ {line}{OFF}")

    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers={"Authorization": f"Basic {AUTH}",
                                "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode()
            result = json.loads(payload) if payload else {}
            if show:
                print(f"  {GREEN}← {response.status}{OFF} "
                      f"{DIM}{json.dumps(result)[:150]}{OFF}")
            return result
    except urllib.error.HTTPError as error:
        raised = ApiError(method, path, error.code, error.read().decode())
        if show:
            print(f"  {RED}← {error.code}{OFF} "
                  f"{raised.body.get('message', '')[:300]}")
        raise raised from None


# ------------------------------------------------------- the two widgets
#
# A widget is: one `view`, one or more `metrics` (measure + aggregation), zero
# or more `dimensions` to break down by, `filters`, and a `chartType`.
#
# For a *_TIME_SERIES chart, Langfuse supplies the time axis itself — so
# `dimensions` there is the breakdown (one line per value), NOT time.

WIDGETS = [
    {
        "name": "[example] Agent turns",
        "description": "One root observation per user turn.",
        "view": "observations",
        "chartType": "NUMBER",
        "metrics": [{"measure": "count", "agg": "count"}],
        "dimensions": [],
        # `isRootObservation` is the v4 way to count application entry points.
        # It catches app roots whose infrastructure parent was filtered out of
        # the export, which `parentObservationId is null` would miss.
        "filters": [{"column": "isRootObservation", "operator": "=",
                     "value": True, "type": "boolean"}],
        "layout": {"width": 4, "height": 4},
    },
    {
        "name": "[example] Score trend by evaluator",
        "description": "Average score over time, one line per evaluator.",
        # A different view: scores live in `scores-numeric`, not `observations`.
        "view": "scores-numeric",
        "chartType": "LINE_TIME_SERIES",
        "metrics": [{"measure": "value", "agg": "avg"}],
        "dimensions": [{"field": "name"}],
        # Scoped on purpose. Unfiltered, every score name in the project shares
        # one y-axis, and a score that is a count rather than a 0-1 rate
        # flattens every other line into the floor.
        "filters": [{"column": "name", "operator": "any of",
                     "value": ["groundedness", "relevance", "helpfulness"],
                     "type": "stringOptions"}],
        "layout": {"width": 8, "height": 4},
    },
]


# ------------------------------------------------------------------- steps

def _wrap(items: list, per_row: int) -> list:
    """Chunk a long field list so it reads on a projector."""
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def discover_fields() -> None:
    """Step 0 — ask the API what it accepts instead of guessing.

    Send a field name that cannot exist and the 400 comes back carrying every
    valid one for that view. Faster than the reference docs, and it is the
    truth for the version you are actually talking to.
    """
    print(f"\n{BOLD}0. Discover the valid fields{OFF}")
    print(f"   {DIM}A deliberately bogus dimension makes the error list them all.{OFF}")
    for view in ("observations", "scores-numeric"):
        try:
            metrics_query({"view": view,
                           "metrics": [{"measure": "count", "aggregation": "count"}],
                           "dimensions": [{"field": "__does_not_exist__"}],
                           "filters": []}, show=False)
        except ApiError as error:
            # The whole point: the rejection body carries the allowed values.
            fields = error.body.get("message", "").split("Must be one of ", 1)[-1]
            print(f"\n   {CYAN}{view}{OFF} dimensions:")
            for chunk in _wrap(fields.split(","), 6):
                print(f"   {DIM}{', '.join(chunk)}{OFF}")


def metrics_query(query: dict, *, show: bool = True):
    """Run a widget's query through the Metrics API before creating the widget.

    Cheap insurance: it is the same query engine the widget will use, so a
    dashboard never gets published pointing at something that cannot resolve.
    """
    now = datetime.now(timezone.utc)
    query = {**query,
             "fromTimestamp": (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "toTimestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "config": {"row_limit": 100}}
    return call("GET", "/api/public/v2/metrics",
                query={"query": json.dumps(query)}, show=show)


def build() -> None:
    discover_fields()

    print(f"\n{BOLD}1. Validate each widget's query{OFF}")
    for widget in WIDGETS:
        result = metrics_query({
            "view": widget["view"],
            # NOTE the key change: the Metrics API wants `aggregation`, the
            # widget endpoint wants `agg`. Same concept, two spellings.
            "metrics": [{"measure": m["measure"], "aggregation": m["agg"]}
                        for m in widget["metrics"]],
            "dimensions": widget["dimensions"],
            "filters": widget["filters"],
        }, show=False)
        rows = len(result.get("data", []))
        print(f"   {GREEN}✓{OFF} {widget['name']} — {rows} row(s) of real data")

    print(f"\n{BOLD}2. Create the widgets{OFF}")
    widget_ids = []
    for widget in WIDGETS:
        body = {k: widget[k] for k in
                ("name", "description", "view", "chartType",
                 "dimensions", "metrics", "filters")}
        created = call("POST", f"{UNSTABLE}/dashboard-widgets", body)
        widget_ids.append((created["id"], widget["layout"]))

    print(f"\n{BOLD}3. Create the dashboard{OFF}")
    dashboard = call("POST", f"{UNSTABLE}/dashboards",
                     {"name": DASHBOARD_NAME,
                      "description": "Built by scripts/dashboard_api_example.py"})

    print(f"\n{BOLD}4. Place the widgets on its grid{OFF}")
    print(f"   {DIM}12 columns wide. `type` is a required discriminator; sizes are{OFF}")
    print(f"   {DIM}`width`/`height`. Omit x/y entirely and the server appends.{OFF}")
    x = 0
    for widget_id, layout in widget_ids:
        call("POST", f"{UNSTABLE}/dashboards/{dashboard['id']}/placements",
             {"type": "widget", "widgetId": widget_id,
              "x": x, "y": 0, **layout})
        x += layout["width"]

    project_id = call("GET", "/api/public/projects",
                      show=False)["data"][0]["id"]
    print(f"\n{BOLD}{GREEN}Done.{OFF} "
          f"{HOST}/project/{project_id}/dashboards/{dashboard['id']}")
    print(f"\n{DIM}The same three calls as curl:{OFF}")
    print(f"""{DIM}  curl -u "$PK:$SK" -H 'Content-Type: application/json' \\
    -d '{{"name":"my widget","description":"","view":"observations",
         "chartType":"NUMBER","dimensions":[],"filters":[],
         "metrics":[{{"measure":"count","agg":"count"}}]}}' \\
    {HOST}{UNSTABLE}/dashboard-widgets

  curl -u "$PK:$SK" -H 'Content-Type: application/json' \\
    -d '{{"name":"my dashboard","description":""}}' \\
    {HOST}{UNSTABLE}/dashboards

  curl -u "$PK:$SK" -H 'Content-Type: application/json' \\
    -d '{{"type":"widget","widgetId":"<WID>","x":0,"y":0,"width":6,"height":6}}' \\
    {HOST}{UNSTABLE}/dashboards/<DID>/placements{OFF}""")
    print(f"\n{DIM}Clean up with: scripts/dashboard_api_example.py --delete{OFF}")


def delete() -> None:
    """Remove everything this script creates. Matched by the [example] prefix."""
    print(f"\n{BOLD}Deleting{OFF}")
    for dash in call("GET", f"{UNSTABLE}/dashboards", query={"limit": 100},
                     show=False).get("data", []):
        if dash["name"] == DASHBOARD_NAME:
            call("DELETE", f"{UNSTABLE}/dashboards/{dash['id']}", show=False)
            print(f"  {GREEN}✓{OFF} dashboard {dash['name']}")
    wanted = {w["name"] for w in WIDGETS}
    for widget in call("GET", f"{UNSTABLE}/dashboard-widgets",
                       query={"limit": 100}, show=False).get("data", []):
        if widget["name"] in wanted:
            call("DELETE", f"{UNSTABLE}/dashboard-widgets/{widget['id']}",
                 show=False)
            print(f"  {GREEN}✓{OFF} widget {widget['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--delete", action="store_true",
                        help="remove the dashboard and widgets this script creates")
    args = parser.parse_args()

    print(f"{BOLD}Langfuse dashboards via the API{OFF}  {DIM}{HOST}{OFF}")
    try:
        delete() if args.delete else build()
    except ApiError as error:
        print(f"\n{RED}failed:{OFF} {error}", file=sys.stderr)
        sys.exit(1)
