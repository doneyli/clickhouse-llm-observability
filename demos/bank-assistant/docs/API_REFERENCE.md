# Langfuse v4 API Reference

Use `northwind.config.api(...)` for authenticated reads against the selected
bank-demo project. Configure the target through the [quick start](../README.md#quick-start).
Paths below are relative to `/api/public`.

Langfuse Cloud removes the deprecated read endpoints on **2026-11-16**.
The demo's self-hosted v4 server already returns 404 for them. See the
[official migration guide](https://langfuse.com/faq/all/deprecated-api-migration)
for the full endpoint and SDK mappings.

## Read Endpoints

| Read | Endpoint | Notes |
|---|---|---|
| One trace | `GET /v2/observations?traceId=<id>` | Request `fields=core,basic,io,metadata`. The root observation (`isRootObservation: true`) holds the turn's input/output. |
| Traces by name or tag | `GET /v2/observations` | Use `filter` conditions on `traceName` or `tags`, plus `isRootObservation = true` for one row per trace. |
| One observation | `GET /v2/observations` | Use a `filter` condition on `id`; the result includes `traceId`. |
| Session turns | `GET /v2/observations?sessionId=<id>&isRootObservation=true` | One root observation per turn. |
| Scores | `GET /v3/scores?fields=core,subject` | The target is in `subject`. Filtering by `observationId` also requires `traceId`. |
| Experiments | `GET /v2/datasets/{name}`, then `GET /experiments?datasetId=<id>` | Pass `fromStartTime`; run names are not unique. URL-encode dataset names. |
| Experiment items | `GET /experiment-items?experimentId=<id>&fields=core,io,scores` | Pass `fromStartTime`. |
| Metrics | `GET /v2/metrics?query=<json>` | Use the `observations` view with an `isRootObservation = true` filter to count traces. |

## Filters and Pagination

- Unknown query parameters are silently ignored. Put `traceName` and `tags` in
  `filter`, a JSON list of `type`, `column`, `operator`, and `value` conditions.
- `filter` overrides query-parameter filters. Include the time range in the
  filter when using it.
- Fields are opt-in. Observations default to `core,basic`; request `io`,
  `metadata`, `usage`, or `trace_context` when needed.
- Follow `meta.cursor` until it is absent. Do not use `page` for these read APIs.
- Bound reads with start and end times. Request only the fields you need.

## Deleting Experiment Data

The legacy dataset-run delete endpoint is unavailable on v4. The demo's
`scripts/run_all_experiments.sh --fresh` lists experiment items, collects their
trace IDs, and calls `DELETE /traces` with `{"traceIds": [...]}` (at most 1,000
IDs per request). This deletes the observations and scores attached to those
traces. Use it only when you intend to replace the existing experiment evidence.

## Code Examples

- [verify_demo.py](../scripts/verify_demo.py): observation filters, cursor pagination, experiments.
- [run_cost_arc.sh](../scripts/run_cost_arc.sh): observations, scores, and experiment items.
- [run_all_experiments.sh](../scripts/run_all_experiments.sh): experiment cleanup and regeneration.
