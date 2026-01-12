# HyperDX Dashboard API Reference

This document describes how to programmatically create dashboards in HyperDX/ClickStack.

## Two Dashboard Formats

HyperDX has **two different internal formats** for dashboard tiles:

### 1. External API v2 Format (Legacy `series`)

Used by the `/api/v2/dashboards` REST API. **Limited to logs and metrics tables only.**

```json
{
  "name": "Dashboard Name",
  "tiles": [{
    "name": "Tile Name",
    "x": 0, "y": 0, "w": 6, "h": 3,
    "series": [{
      "type": "time",
      "dataSource": "events",
      "aggFn": "count",
      "field": "duration",
      "where": "service:api",
      "whereLanguage": "lucene",
      "groupBy": ["service"]
    }]
  }],
  "tags": ["tag1", "tag2"]
}
```

**Limitations:**
- `dataSource: "events"` → stored as `table: "logs"`
- `dataSource: "metrics"` → stored as `table: "metrics"`
- **Cannot access traces data** (otel_traces table)

### 2. Internal/UI Format (New `config`)

Used by the HyperDX UI. **Supports all data sources including traces.**

```json
{
  "name": "Dashboard Name",
  "tiles": [{
    "id": "unique-tile-id",
    "x": 0, "y": 0, "w": 6, "h": 3,
    "config": {
      "name": "Tile Name",
      "source": "696018e0111b88a75f8b3677",
      "select": [{
        "aggFn": "count",
        "aggCondition": "",
        "aggConditionLanguage": "sql",
        "valueExpression": ""
      }],
      "where": "SpanAttributes['gen_ai.request.model'] != ''",
      "whereLanguage": "sql",
      "displayType": "line",
      "granularity": "auto"
    }
  }],
  "tags": ["tag1"]
}
```

## Source IDs

Get source IDs from MongoDB:
```bash
docker exec clickstack mongo --quiet --eval '
db = db.getSiblingDB("hyperdx");
db.sources.find({}, {_id: 1, name: 1, kind: 1}).forEach(function(s) {
  print(s.kind + ": " + s._id.str + " (" + s.name + ")");
});
'
```

Default ClickStack source IDs:
- **Logs**: `696018e0111b88a75f8b3675` (otel_logs)
- **Traces**: `696018e0111b88a75f8b3677` (otel_traces)
- **Metrics**: `696018e0111b88a75f8b3679`
- **Sessions**: `696018e0111b88a75f8b367c`

## Config Format Reference

### Tile Structure

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique tile identifier |
| `x`, `y` | number | Grid position (0-based) |
| `w`, `h` | number | Width and height (grid units, max 12 wide) |
| `config` | object | Tile configuration |

### Config Object

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display title |
| `source` | string | Source ID (from sources collection) |
| `select` | array | Aggregation configurations |
| `where` | string | Filter expression |
| `whereLanguage` | string | `"sql"` or `"lucene"` |
| `displayType` | string | Chart type |
| `granularity` | string | Time granularity (`"auto"`, `"1m"`, `"1h"`, etc.) |

### Select Object

| Field | Type | Description |
|-------|------|-------------|
| `aggFn` | string | Aggregation function |
| `aggCondition` | string | Condition for aggregation (optional) |
| `aggConditionLanguage` | string | `"sql"` |
| `valueExpression` | string | Field/expression to aggregate |

### Valid aggFn Values

- `count` - Count rows
- `sum` - Sum values
- `avg` - Average
- `min` - Minimum
- `max` - Maximum
- `count_distinct` - Count unique values
- `last_value` - Last value

**Note:** Percentile functions (`p50`, `p95`, `p99`) are NOT supported. Use ClickHouse's `quantile()` in `valueExpression` if needed.

### Valid displayType Values

- `number` - Single value display
- `line` - Line chart
- `stacked_bar` - Stacked bar chart
- `bar` - Bar chart
- `area` - Area chart

### whereLanguage

- `"sql"` - Use ClickHouse SQL syntax
  - Example: `SpanAttributes['gen_ai.request.model'] != ''`
- `"lucene"` - Use Lucene query syntax
  - Example: `service:api AND status:error`
  - **Warning:** Lucene doesn't properly handle SpanAttributes fields

### valueExpression Examples

For traces (otel_traces table):
```sql
-- Count (leave empty)
""

-- Token count
"SpanAttributes['gen_ai.usage.input_tokens']"

-- Duration in milliseconds
"Duration / 1000000"

-- Custom calculation
"toFloat64OrDefault(SpanAttributes['gen_ai.usage.input_tokens']) * 0.001"
```

## Creating Dashboards

### Option 1: External API v2 (logs/metrics only)

```bash
curl -X POST "http://localhost:8000/api/v2/dashboards" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Dashboard",
    "tiles": [{
      "name": "Request Count",
      "x": 0, "y": 0, "w": 6, "h": 3,
      "series": [{
        "type": "time",
        "dataSource": "events",
        "aggFn": "count",
        "where": "",
        "groupBy": []
      }]
    }],
    "tags": ["api"]
  }'
```

### Option 2: MongoDB Direct Insert (all sources including traces)

```bash
docker exec clickstack mongo --quiet --eval '
db = db.getSiblingDB("hyperdx");

var dashboard = {
  name: "LLM Observability Dashboard",
  team: ObjectId("YOUR_TEAM_ID"),
  tags: ["llm"],
  filters: [],
  tiles: [{
    id: "tile-1",
    x: 0, y: 0, w: 6, h: 3,
    config: {
      name: "LLM Request Count",
      source: "696018e0111b88a75f8b3677",
      select: [{
        aggFn: "count",
        aggCondition: "",
        aggConditionLanguage: "sql",
        valueExpression: ""
      }],
      where: "SpanAttributes['"'"'gen_ai.request.model'"'"'] != '"'"''"'"'",
      whereLanguage: "sql",
      displayType: "line",
      granularity: "auto"
    }
  }],
  createdAt: new Date(),
  updatedAt: new Date()
};

db.dashboards.insertOne(dashboard);
'
```

## Common Issues

### 1. "timestampValueExpression undefined"
**Cause:** Using External API v2 `series` format with `sourceId` field.
**Fix:** Use MongoDB direct insert with `config.source` format.

### 2. "Unknown expression identifier"
**Cause:** Using Lucene syntax for SpanAttributes fields.
**Fix:** Use `whereLanguage: "sql"` with proper ClickHouse syntax:
```sql
SpanAttributes['field.name'] != ''
```

### 3. "Function p50/p95/p99 does not exist"
**Cause:** HyperDX doesn't translate percentile aggFn to ClickHouse.
**Fix:** Use `avg` or `max`, or create custom valueExpression with `quantile()`.

### 4. "undefined in GROUP BY"
**Cause:** `groupBy` field not supported in `config` format.
**Fix:** GroupBy is not yet fully supported in the new config format. Use separate tiles or the External API v2 for logs data.

## API Authentication

Get your Personal API Key:
```bash
# From MongoDB
docker exec clickstack mongo --quiet --eval '
db = db.getSiblingDB("hyperdx");
print(db.users.findOne({}).accessKey);
'

# Or from HyperDX UI: Team Settings > API Keys
```

## Full Example: LLM Observability Dashboard

See `scripts/create-hyperdx-dashboard-mongo.sh` for a complete working example that creates an LLM observability dashboard using the MongoDB direct insert method.
