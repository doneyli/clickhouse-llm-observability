#!/usr/bin/env python3
"""Seed the demo's custom dashboards into Langfuse — dashboards as code.

Three dashboards, each mapping to a capability a customer asks about:

  1. Production Health          — the Monitor node: volume, latency, cost,
                                  score drift over time, failure modes.
  2. Evaluation Coverage        — who scored what: code vs judge vs human,
     & Judge Calibration          and the machine-vs-human agreement table.
  3. Cost & Chargeback          — spend by model, user, prompt version, tag.

Uses the unstable dashboards API (`/api/public/unstable/{dashboards,
dashboard-widgets}`), which is what the Langfuse CLI and MCP server drive too.
Every widget query is validated against the Metrics API *before* the widget is
created, so a dashboard is never published pointing at an invalid query.

Idempotent: re-running matches dashboards and widgets by name and updates them
in place, so the layout and URLs stay stable across runs.

    ./.venv/bin/python scripts/seed_dashboards.py            # create/update
    ./.venv/bin/python scripts/seed_dashboards.py --dry-run  # validate only
    ./.venv/bin/python scripts/seed_dashboards.py --delete   # remove them
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- env + client


def load_env() -> tuple[str, str, str]:
    """Read keys from .env only — never from the shell.

    A shell-exported LANGFUSE_* key for another project would silently publish
    these dashboards there; see the note in the repo's CLAUDE.md.
    """
    env: dict[str, str] = {}
    env_file = REPO_DIR / ".env"
    if not env_file.exists():
        sys.exit(f"missing {env_file}")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")

    host = env.get("LANGFUSE_HOST", "http://localhost:3001").rstrip("/")
    pk, sk = env.get("LANGFUSE_PUBLIC_KEY", ""), env.get("LANGFUSE_SECRET_KEY", "")
    if not pk or not sk:
        sys.exit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY missing from .env")
    return host, pk, sk


class Client:
    def __init__(self, host: str, pk: str, sk: str) -> None:
        self.host = host
        self.auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    def _call(self, method: str, path: str, body: dict | None = None,
              query: dict | None = None) -> dict:
        url = f"{self.host}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Basic {self.auth}")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:600]
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from None

    get = lambda self, p, **kw: self._call("GET", p, **kw)          # noqa: E731
    post = lambda self, p, body: self._call("POST", p, body=body)   # noqa: E731
    patch = lambda self, p, body: self._call("PATCH", p, body=body)  # noqa: E731
    delete = lambda self, p: self._call("DELETE", p)                # noqa: E731

    # -- metrics: used to validate a widget query before publishing it -------
    def validate_query(self, view: str, metrics: list[dict], dimensions: list[dict],
                       filters: list[dict]) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        query = {
            "view": view,
            "metrics": [{"measure": m["measure"], "aggregation": m["agg"]} for m in metrics],
            "dimensions": dimensions,
            "filters": filters,
            "fromTimestamp": (now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            "toTimestamp": now.isoformat().replace("+00:00", "Z"),
            "config": {"row_limit": 100},
        }
        # High-cardinality dimensions require an explicit desc orderBy.
        hi_card = {"userId", "sessionId", "traceId", "id", "observationId",
                   "experimentName", "experimentId"}
        if any(d["field"] in hi_card for d in dimensions):
            m0 = metrics[0]
            query["orderBy"] = [{"field": f"{m0['agg']}_{m0['measure']}", "direction": "desc"}]
        try:
            res = self.get("/api/public/v2/metrics",
                           query={"query": json.dumps(query)})
            return True, f"{len(res.get('data', []))} rows"
        except RuntimeError as exc:
            return False, str(exc)


# ------------------------------------------------------------------- widgets
# Each widget: name, description, view, chartType, metrics, dimensions, filters.
# `w`/`h` are grid units (12 wide). Langfuse adds the time axis itself for the
# *_TIME_SERIES chart types, so `dimensions` there is the *breakdown*, not time.

REAL_ESTATE_TAG = {"column": "tags", "operator": "any of",
                   "value": ["real-estate"], "type": "arrayOptions"}
ROOT_ONLY = {"column": "isRootObservation", "operator": "=",
             "value": True, "type": "boolean"}
GENERATIONS_ONLY = {"column": "type", "operator": "=",
                    "value": "GENERATION", "type": "string"}

# Graded judges on a 0-1 scale. Charting these together is meaningful; mixing in
# counts like `turns-to-resolution` is not.
GRADED_JUDGES = ["groundedness", "relevance", "helpfulness",
                 "context-retention", "user-feedback"]

# Boolean pass/fail checks worth a bar each. `reached-done` sits near 0.3 — the
# honest failure the demo is built to surface.
PASS_FAIL_CHECKS = ["grounded-listings", "language-match", "used-search-tool",
                    "stated-constraint-respected", "reference-resolved",
                    "no-redundant-questions", "reached-done"]

# Score names graded by more than one source — the only rows where a
# machine-vs-human comparison exists. `stated-constraint-respected` and
# `reference-resolved` are deliberately shared across code, judge and human;
# `helpfulness`/`Helpfulness` are our SDK judge vs the Langfuse managed one.
CALIBRATION_PAIRS = ["stated-constraint-respected", "reference-resolved",
                     "expert-usefulness", "reviewer-verdict",
                     "conversation-outcome",
                     "helpfulness", "Helpfulness",
                     "relevance", "Relevance",
                     "groundedness", "user-feedback"]

DASHBOARDS: list[dict] = [
    {
        "name": "Production Health — Property Concierge",
        "description": (
            "The Monitor node: is the agent healthy right now? Volume, latency, "
            "cost and — the part a generic APM cannot give you — quality score "
            "trends and pass rates per automated check."
        ),
        "widgets": [
            {
                "name": "Agent turns (total)",
                "description": "Root observations = one user turn each.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [], "filters": [ROOT_ONLY],
                "w": 3, "h": 4,
            },
            {
                "name": "LLM spend (total)",
                "description": "Summed cost of every generation, priced per model.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "totalCost", "agg": "sum"}],
                "dimensions": [], "filters": [],
                "w": 3, "h": 4,
            },
            {
                "name": "Turn latency p95",
                "description": "95th-percentile end-to-end latency of an agent turn.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "latency", "agg": "p95"}],
                "dimensions": [], "filters": [ROOT_ONLY],
                "w": 3, "h": 4,
            },
            {
                "name": "Scores recorded (total)",
                "description": "Every evaluation result — code, judge and human.",
                "view": "scores-numeric", "chartType": "NUMBER",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [], "filters": [],
                "w": 3, "h": 4,
            },
            {
                "name": "Quality drift — score trend by evaluator",
                "description": (
                    "THE drift chart. Each line is one evaluator's rolling average. "
                    "A line bending down is a regression you can see before users "
                    "complain — this is what a monitor threshold watches."
                ),
                "view": "scores-numeric", "chartType": "LINE_TIME_SERIES",
                "metrics": [{"measure": "value", "agg": "avg"}],
                "dimensions": [{"field": "name"}],
                # Restricted to the graded 0-1 judges on purpose: unfiltered,
                # 59 score names share one y-axis and `turns-to-resolution`
                # (a count, not a rate) flattens everything else.
                "filters": [{"column": "name", "operator": "any of",
                             "value": GRADED_JUDGES, "type": "stringOptions"}],
                "w": 8, "h": 6,
            },
            {
                "name": "Turn volume over time",
                "description": "Traffic shape — context for every other chart here.",
                "view": "observations", "chartType": "BAR_TIME_SERIES",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [], "filters": [ROOT_ONLY],
                "w": 4, "h": 6,
            },
            {
                "name": "Failure modes — pass rate per check",
                "description": (
                    "Boolean code evaluators: grounded listings, budget adherence, "
                    "language match, search-tool use. Anything below 1.0 is a real "
                    "failure with a trace behind it."
                ),
                "view": "scores-boolean", "chartType": "HORIZONTAL_BAR",
                "metrics": [{"measure": "value", "agg": "avg"}],
                "dimensions": [{"field": "name"}],
                "filters": [{"column": "name", "operator": "any of",
                             "value": PASS_FAIL_CHECKS, "type": "stringOptions"}],
                "w": 6, "h": 6,
            },
            {
                "name": "Latency p95 over time",
                "description": "Latency regressions, separated from quality ones.",
                "view": "observations", "chartType": "LINE_TIME_SERIES",
                "metrics": [{"measure": "latency", "agg": "p95"}],
                "dimensions": [], "filters": [ROOT_ONLY],
                "w": 6, "h": 6,
            },
            {
                "name": "Tool call mix",
                "description": (
                    "Which tools the agent actually reaches for. A collapse in "
                    "search_listings is the signature of the `no_search` failure."
                ),
                "view": "observations", "chartType": "PIE",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [{"field": "name"}],
                "filters": [{"column": "type", "operator": "=",
                             "value": "TOOL", "type": "string"}],
                "w": 4, "h": 6,
            },
            {
                "name": "Turn latency distribution",
                "description": "The long tail — where the slow turns actually sit.",
                "view": "observations", "chartType": "HISTOGRAM",
                "metrics": [{"measure": "latency", "agg": "histogram"}],
                "dimensions": [], "filters": [ROOT_ONLY],
                "w": 4, "h": 6,
            },
            {
                "name": "Cost per day by model",
                "description": "Spend trend, split by model — the migration story.",
                "view": "observations", "chartType": "AREA_TIME_SERIES",
                "metrics": [{"measure": "totalCost", "agg": "sum"}],
                "dimensions": [{"field": "providedModelName"}],
                "filters": [GENERATIONS_ONLY],
                "w": 4, "h": 6,
            },
        ],
    },
    {
        "name": "Evaluation Coverage & Judge Calibration",
        "description": (
            "Who scored what, and can you trust the judge? Code evaluators, "
            "managed LLM judges and human annotations side by side — including "
            "the machine-vs-human agreement table that makes a judge auditable."
        ),
        "widgets": [
            {
                "name": "Scores by source",
                "description": (
                    "API = our code evaluators + SDK judges, EVAL = Langfuse "
                    "managed judges, ANNOTATION = a human in the queue. Three "
                    "independent signals on the same traffic."
                ),
                "view": "scores-numeric", "chartType": "PIE",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [{"field": "source"}], "filters": [],
                "w": 4, "h": 6,
            },
            {
                "name": "Calibration table — average score by evaluator × source",
                "description": (
                    "The calibration read. Where a score name appears under both "
                    "API/EVAL and ANNOTATION, the two columns are the machine and "
                    "the human grading the same thing — the gap is judge drift. "
                    "For the statistical version (Cohen's kappa, confusion matrix) "
                    "use Scores → Analytics."
                ),
                "view": "scores-numeric", "chartType": "PIVOT_TABLE",
                "metrics": [{"measure": "value", "agg": "avg"},
                            {"measure": "count", "agg": "count"}],
                "dimensions": [{"field": "name"}, {"field": "source"}],
                # Only the names that actually have more than one grader.
                # Unfiltered, 59 names (including the per-run `avg-*` rollups)
                # bury the two rows that make the point.
                "filters": [{"column": "name", "operator": "any of",
                             "value": CALIBRATION_PAIRS, "type": "stringOptions"}],
                "w": 8, "h": 6,
            },
            {
                "name": "Judge vs. judge — average by evaluator",
                "description": (
                    "Our SDK judges (lowercase) next to the Langfuse managed "
                    "judges (capitalised) on the same dimension. Two judges "
                    "disagreeing is a judge problem, not a model problem."
                ),
                "view": "scores-numeric", "chartType": "HORIZONTAL_BAR",
                "metrics": [{"measure": "value", "agg": "avg"}],
                "dimensions": [{"field": "name"}],
                "filters": [{"column": "name", "operator": "any of",
                             "value": ["helpfulness", "Helpfulness",
                                       "relevance", "Relevance",
                                       "groundedness", "tone"],
                             "type": "stringOptions"}],
                "w": 6, "h": 6,
            },
            {
                "name": "Human review throughput",
                "description": (
                    "Annotation volume over time — the SME review loop actually "
                    "running. Flat means the queue is starved and the calibration "
                    "set is going stale."
                ),
                "view": "scores-numeric", "chartType": "BAR_TIME_SERIES",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [],
                "filters": [{"column": "source", "operator": "=",
                             "value": "ANNOTATION", "type": "string"}],
                "w": 6, "h": 6,
            },
            {
                # Note: `experimentName` / `datasetRunId` are queryable in the
                # Metrics API but NOT available as widget dimensions, so offline
                # experiment comparison stays in the Experiments UI. Live quality
                # per prompt version is the dashboard-side equivalent.
                "name": "Quality by prompt version (live traffic)",
                "description": (
                    "Average score per deployed prompt version. Promote a label "
                    "and the new version's bar appears — the Deploy node measured "
                    "on real traffic, not just on the golden set."
                ),
                "view": "scores-numeric", "chartType": "PIVOT_TABLE",
                "metrics": [{"measure": "value", "agg": "avg"},
                            {"measure": "count", "agg": "count"}],
                "dimensions": [{"field": "observationPromptName"},
                               {"field": "observationPromptVersion"}],
                "filters": [],
                "w": 6, "h": 6,
            },
            {
                "name": "Score coverage by trace name",
                "description": (
                    "Which surfaces are evaluated at all. A busy trace name with "
                    "no scores is an unmonitored code path."
                ),
                "view": "scores-numeric", "chartType": "VERTICAL_BAR",
                "metrics": [{"measure": "count", "agg": "count"}],
                "dimensions": [{"field": "traceName"}], "filters": [],
                "w": 6, "h": 6,
            },
        ],
    },
    {
        "name": "Cost & Chargeback",
        "description": (
            "Spend attribution: by model, by end user, by prompt version and by "
            "tag. The numbers a platform team bills an internal customer with — "
            "same ClickHouse-backed query engine, no export."
        ),
        "widgets": [
            {
                "name": "Total spend",
                "description": "All generations in the window, priced per model.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "totalCost", "agg": "sum"}],
                "dimensions": [], "filters": [],
                "w": 3, "h": 4,
            },
            {
                "name": "Total tokens",
                "description": "Input + output tokens across every model.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "totalTokens", "agg": "sum"}],
                "dimensions": [], "filters": [],
                "w": 3, "h": 4,
            },
            {
                "name": "Billable users",
                "description": "Distinct end users seen — the chargeback denominator.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "uniqueUserIds", "agg": "uniq"}],
                "dimensions": [], "filters": [],
                "w": 3, "h": 4,
            },
            {
                "name": "Conversations",
                "description": "Distinct sessions — a conversation, not a turn.",
                "view": "observations", "chartType": "NUMBER",
                "metrics": [{"measure": "uniqueSessionIds", "agg": "uniq"}],
                "dimensions": [], "filters": [],
                "w": 3, "h": 4,
            },
            {
                "name": "Spend by model",
                "description": (
                    "The model-choice lever. Same agent, same evals — this is the "
                    "denominator of the 'is the cheaper model good enough' question."
                ),
                "view": "observations", "chartType": "PIE",
                "metrics": [{"measure": "totalCost", "agg": "sum"}],
                "dimensions": [{"field": "providedModelName"}],
                "filters": [GENERATIONS_ONLY],
                "w": 4, "h": 6,
            },
            {
                "name": "Spend by end user (chargeback)",
                "description": (
                    "Cost attributed per `userId` propagated from the app. Swap "
                    "userId for a tenant or cost-centre id and this is the invoice."
                ),
                "view": "observations", "chartType": "HORIZONTAL_BAR",
                "metrics": [{"measure": "totalCost", "agg": "sum"}],
                "dimensions": [{"field": "userId"}],
                "filters": [REAL_ESTATE_TAG],
                "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
                "w": 4, "h": 6,
            },
            {
                "name": "Spend by tag (business line)",
                "description": (
                    "Tags carry the app's own taxonomy — surface, campaign, "
                    "experiment. Attribution without a separate cost system."
                ),
                "view": "observations", "chartType": "HORIZONTAL_BAR",
                "metrics": [{"measure": "totalCost", "agg": "sum"}],
                "dimensions": [{"field": "tags"}],
                "filters": [REAL_ESTATE_TAG],
                "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 15},
                "w": 4, "h": 6,
            },
            {
                "name": "Cost per prompt version",
                "description": (
                    "Prompt versions are linked to every generation, so a prompt "
                    "that got more verbose shows up as a cost line, not a surprise "
                    "on the invoice."
                ),
                "view": "observations", "chartType": "PIVOT_TABLE",
                "metrics": [{"measure": "totalCost", "agg": "sum"},
                            {"measure": "totalTokens", "agg": "sum"}],
                "dimensions": [{"field": "promptName"},
                               {"field": "promptVersion"}],
                "filters": [GENERATIONS_ONLY],
                "w": 6, "h": 6,
            },
            {
                "name": "Token usage by type",
                "description": (
                    "Input / output / cached / reasoning tokens split out — where "
                    "caching and reasoning budgets actually land."
                ),
                "view": "observations", "chartType": "AREA_TIME_SERIES",
                "metrics": [{"measure": "usageByType", "agg": "sum"}],
                "dimensions": [{"field": "usageType"}], "filters": [],
                "w": 6, "h": 6,
            },
        ],
    },
]


# --------------------------------------------------------------------- runner


def upsert(client: Client, dry_run: bool = False) -> list[tuple[str, str]]:
    existing_dash = {d["name"]: d for d in
                     client.get("/api/public/unstable/dashboards",
                                query={"limit": 100}).get("data", [])}
    existing_widgets = {w["name"]: w for w in
                        client.get("/api/public/unstable/dashboard-widgets",
                                   query={"limit": 100}).get("data", [])}
    results = []
    failures = 0

    for spec in DASHBOARDS:
        print(f"\n\033[1m{spec['name']}\033[0m")
        widget_ids: list[tuple[str, dict]] = []

        for w in spec["widgets"]:
            ok, note = client.validate_query(w["view"], w["metrics"],
                                             w["dimensions"], w["filters"])
            if not ok:
                print(f"  \033[31m✗\033[0m {w['name']}: {note}")
                failures += 1
                continue
            print(f"  \033[32m✓\033[0m {w['name']}  ({note})")
            if dry_run:
                continue

            body = {
                "name": w["name"], "description": w["description"],
                "view": w["view"], "chartType": w["chartType"],
                "dimensions": w["dimensions"], "metrics": w["metrics"],
                "filters": w["filters"],
            }
            if "chartConfig" in w:
                body["chartConfig"] = w["chartConfig"]

            prior = existing_widgets.get(w["name"])
            try:
                if prior:
                    res = client.patch(
                        f"/api/public/unstable/dashboard-widgets/{prior['id']}", body)
                else:
                    res = client.post("/api/public/unstable/dashboard-widgets", body)
            except RuntimeError as exc:
                # A query the Metrics API accepts can still be rejected by the
                # widget builder (it exposes a narrower set of dimensions).
                # Skip the widget rather than abandoning the whole dashboard.
                print(f"    \033[31mwidget rejected:\033[0m {exc}")
                failures += 1
                continue
            widget_ids.append((res["id"], w))

        if dry_run:
            continue

        dash = existing_dash.get(spec["name"])
        if dash:
            dash = client.patch(f"/api/public/unstable/dashboards/{dash['id']}",
                              {"name": spec["name"],
                               "description": spec["description"]})
        else:
            dash = client.post("/api/public/unstable/dashboards",
                               {"name": spec["name"],
                                "description": spec["description"]})
        dash_id = dash["id"]

        # Rebuild the layout from scratch so re-runs stay deterministic.
        for p in (client.get(f"/api/public/unstable/dashboards/{dash_id}")
                  .get("definition", {}).get("widgets", [])):
            try:
                client.delete(
                    f"/api/public/unstable/dashboards/{dash_id}/placements/{p['id']}")
            except RuntimeError:
                pass

        x = y = row_h = 0
        for wid, w in widget_ids:
            if x + w["w"] > 12:
                x, y = 0, y + row_h
                row_h = 0
            client.post(f"/api/public/unstable/dashboards/{dash_id}/placements",
                        {"type": "widget", "widgetId": wid,
                         "x": x, "y": y, "width": w["w"], "height": w["h"]})
            x += w["w"]
            row_h = max(row_h, w["h"])

        url = f"{client.host}/project/{PROJECT_ID}/dashboards/{dash_id}"
        print(f"  → {url}")
        results.append((spec["name"], url))

    if failures:
        print(f"\n\033[31m{failures} widget query/queries failed validation "
              f"and were skipped.\033[0m")
    return results


def delete_all(client: Client) -> None:
    want_dash = {d["name"] for d in DASHBOARDS}
    want_widgets = {w["name"] for d in DASHBOARDS for w in d["widgets"]}
    for d in client.get("/api/public/unstable/dashboards",
                        query={"limit": 100}).get("data", []):
        if d["name"] in want_dash:
            client.delete(f"/api/public/unstable/dashboards/{d['id']}")
            print(f"deleted dashboard {d['name']}")
    for w in client.get("/api/public/unstable/dashboard-widgets",
                        query={"limit": 100}).get("data", []):
        if w["name"] in want_widgets:
            client.delete(f"/api/public/unstable/dashboard-widgets/{w['id']}")
            print(f"deleted widget {w['name']}")


PROJECT_ID = ""

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate every widget query, create nothing")
    ap.add_argument("--delete", action="store_true",
                    help="delete the dashboards and widgets this script creates")
    args = ap.parse_args()

    host, pk, sk = load_env()
    client = Client(host, pk, sk)

    projects = client.get("/api/public/projects").get("data", [])
    if not projects:
        sys.exit("no project resolves for these keys")
    PROJECT_ID = projects[0]["id"]
    print(f"project: {projects[0]['name']} ({PROJECT_ID}) on {host}")

    if args.delete:
        delete_all(client)
        sys.exit(0)

    urls = upsert(client, dry_run=args.dry_run)
    if urls:
        print("\n\033[1mDashboards\033[0m")
        for name, url in urls:
            print(f"  {name}\n    {url}")
