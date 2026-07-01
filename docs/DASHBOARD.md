# LLM Observatory Dashboard

A custom analytics dashboard (FastAPI + Alpine.js) that queries **Langfuse's
ClickHouse tables directly** — no Langfuse API in the read path. It exists to prove
the central claim of this stack: your LLM trace data is open ClickHouse tables, so you
can build any analytics you want on top of it.

## Running it

```bash
docker compose --profile langfuse --profile dashboard up -d
```

Open **http://localhost:8005** (port configurable via `DASHBOARD_PORT` in `.env`).
Health check: `curl http://localhost:8005/health`.

## What it shows

| View | API | Backed by |
|------|-----|-----------|
| KPI summary (traces, cost, tokens, latency) | `/api/kpi` | Aggregations over Langfuse trace/observation tables |
| Session list and detail | `/api/sessions`, `/api/sessions/{id}` | Session-grouped traces |
| Activity by hour + heatmap | `/api/activity/by-hour`, `/api/activity/heatmap` | Time-bucketed trace counts |
| Top sessions by cost/volume | `/api/top-sessions` | Ranked aggregation |
| Tool usage breakdown | `/api/tools` | Span/observation analysis |
| Evaluation score trends | `/api/scores` | Langfuse scores table |
| CSV export | `/api/export/csv` | Any of the above, for spreadsheets |
| Project filter | `/api/projects` | Multi-project support |

## Why it matters in a demo

This is the "no vendor silo" proof point ([use case 9](USE_CASES.md#9-sql-analytics-directly-on-trace-data)):

- **The data layer is open.** Langfuse provides the product UX; ClickHouse holds the
  data in queryable tables. This app is ~10 small Python files
  ([`dashboard/`](../dashboard/)) doing plain SQL.
- **Custom analytics are an afternoon, not a roadmap item.** Cost per team, SLO
  dashboards, joins against business data — anything SQL can express.
- **Performance is the demo.** Every panel is a live analytical query over the same
  ClickHouse instance ingesting traces in real time; the page still renders instantly.

Suggested beat (≈5 min, usually after the cost section of the
[platform demo](LANGFUSE_DEMO_RUNBOOK.md)): show the dashboard → reveal it's direct
SQL on Langfuse's tables → open `dashboard/routes/kpi.py` to show how small the query
code is.

## Extending it

Each view is one route module in `dashboard/routes/` plus a panel in
`dashboard/static/`. To add a panel: copy a route module, write your SQL against the
Langfuse schema (see `dashboard/clickhouse_client.py` for the connection and
`dashboard/data_models.py` for response shapes), register it in `dashboard/main.py`,
and add the front-end panel. Rebuild with
`docker compose --profile dashboard up -d --build`.
