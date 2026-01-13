# HyperDX Dashboard Definitions

Pre-configured dashboards for LLM observability that can be imported into HyperDX/ClickStack.

## Available Dashboards

| Dashboard | Description |
|-----------|-------------|
| `llm-observability-dashboard.json` | Core LLM metrics: requests, tokens, latency |
| `usage-management-dashboard.json` | Trace counts, observations, scores, tokens |
| `cost-dashboard.json` | Cost tracking and analysis for LLM usage |

## Quick Start

Import all dashboards:

```bash
./dashboards/import-dashboards.sh --all
```

Import a specific dashboard:

```bash
./dashboards/import-dashboards.sh --dashboard cost-dashboard.json
```

## How It Works

The JSON files contain dashboard definitions with `{{TRACES_SOURCE_ID}}` as a placeholder. The import script automatically:

1. Detects your HyperDX traces source ID
2. Replaces the placeholder with the actual ID
3. Creates the dashboard in MongoDB

## Dashboard Features

All number tiles include formatting with:
- 2 decimal places (`mantissa: 2`)
- Thousand separators (`thousandSeparated: true`)

## Manual Import

If you prefer to import manually via MongoDB:

```bash
# Get your traces source ID
docker exec clickstack mongo --quiet --eval '
  db = db.getSiblingDB("hyperdx");
  print(db.sources.findOne({kind: "trace"})._id.str);
'

# Replace {{TRACES_SOURCE_ID}} in the JSON file with your actual ID
# Then import via mongoimport or direct insert
```

## Exporting Dashboards

To export current dashboards from HyperDX:

```bash
docker exec clickstack mongo --quiet --eval '
  db = db.getSiblingDB("hyperdx");
  var d = db.dashboards.findOne({ name: "YOUR_DASHBOARD_NAME" });
  delete d._id; delete d.team; delete d.createdAt; delete d.updatedAt;
  print(JSON.stringify(d, null, 2));
' | sed 's/YOUR_SOURCE_ID/{{TRACES_SOURCE_ID}}/g' > my-dashboard.json
```
