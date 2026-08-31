# Demo Symptoms — calibrated worker-count ranges

The planner LLM decides the fan-out at runtime, so worker counts are
**non-deterministic by design** — but the phrasings below are calibrated to land
in the ranges shown reliably. This is the analog of agentic-rag's
`DEMO_QUESTIONS.md`: pre-run your headline symptoms before a live demo and pin
the traces (the planner will not always produce the same count).

The 8 symptoms are also the golden `cluster-health/plan-quality` dataset items
(`scripts/seed_datasets.py`).

| # | Symptom | Expected worker range | Expected analyses (typical) |
|---|---------|:---------------------:|-----------------------------|
| 1 | *"One Grafana dashboard query got slow this afternoon; everything else feels fine."* | **1–2** | `slow_queries` (+ maybe `settings_audit`) |
| 2 | *"We're seeing occasional query exceptions in the last hour but throughput looks ok."* | **1–2** | `query_errors` |
| 3 | *"Mutations look stuck on one table and ALTERs never seem to finish."* | **2–3** | `mutation_status`, `merge_backlog` — **re-plan trigger** (see below) |
| 4 | *"Ingest latency crept up over the last day and a couple of merges seem to hang."* | **3–4** | `insert_profile`, `merge_backlog`, `parts_pressure` |
| 5 | *"Inserts got slow after last night's deploy, CPU is pinned and disk is filling."* | **5–6** | `insert_profile`, `parts_pressure`, `merge_backlog`, `memory_pressure`, `disk_usage`, `slow_queries` |
| 6 | *"The whole cluster feels unhealthy since yesterday — slow, erroring, and bloated."* | **5–6** | broad sweep incl. `table_growth` |
| 7 | *"Is my cluster healthy?"* | **3–6** | planner's judgment call — a bounded broad sweep |
| 8 | *"Queries that touch one big table are slow and it keeps growing."* | **2–4** | `slow_queries`, `table_growth`/`parts_pressure` |

## The money shot (Act 2)

Run **#1** (narrow) and **#5/#6** (broad) back-to-back:

- #1 → Agent Graph **Aggregated view**: `worker (2/2)`
- #5 → same graph, same code: `worker (6/6)`

Two traces, one program, different topology — **the LLM decided the fan-out**.

## The reliable re-plan trigger (Act 3)

**Symptom #3** — *"Mutations look stuck on one table and ALTERs never seem to
finish."* — is the calibrated re-plan trigger. In round 1 the planner tends to
reach for the "stuck work" analyses (`merge_backlog`, `mutation_status`); if it
picks `merge_backlog` but omits `mutation_status`, the **re-plan gate** returns
`sufficient=false, missing=[mutation_status]`, the orchestrator is re-visited,
and a **second worker wave** runs the delta. The trace then shows **two
`orchestrator` visits** and a `replan-gate` output with `sufficient=false` on
round 1.

Because the planner is non-deterministic, **pre-verify before a live demo**: run
#3 a few times and pin a trace that shows the two-round shape. If you need to
force it, `--fault overplan` is deterministic (always max fan-out) but does not
exercise the re-plan path.

## The Monitor trigger (Act 4)

```bash
python main.py --fault overplan
```

`--fault overplan` fetches the planner prompt labeled `fault-overplan` (scaling
rules removed) and additionally fills the plan to the cap deterministically, so
`worker_count == MAX_WORKERS_TOTAL` (8) **every time** and the trace is tagged
`fault:overplan`. Watch the `worker_count` score jump to 8 and the Monitor
breach.
