# Operations

**What this covers:** running self-hosted Langfuse v4 in production. Sizing and HA, scaling signals, probes, backups and DR, upgrades, monitoring the platform itself, and a symptom → check → fix runbook.
**Capability IDs:** GATE-04 (operation & resilience); supports OBS-06 (platform telemetry into the APM) and ENT-03 (retention operations).

Checked against the Langfuse docs on 2026-10-07. The topology is in [ARCHITECTURE.md](ARCHITECTURE.md); the platform-to-Dynatrace wiring is in [DYNATRACE.md](DYNATRACE.md).

## 1. Components and sizing

The web container (`langfuse/langfuse`) accepts requests and writes raw events to S3, plus a pointer onto the Redis queue. The worker (`langfuse/langfuse-worker`) drains the queue, loads events into ClickHouse, and runs judges, exports and retention. Because ingestion is queued, a ClickHouse slowdown shows up as queue depth, not as dropped data.

| Component | Documented minimum ([scaling](https://langfuse.com/self-hosting/configuration/scaling#minimum-infrastructure-requirements)) | Production starting point (validate with the POC load test) |
|---|---|---|
| langfuse-web | 2 CPU / 4 GiB | ≥ 2 replicas across AZs. HPA on CPU. Optionally split into an ingestion deployment and a UI deployment (§3) |
| langfuse-worker | 2 CPU / 4 GiB | ≥ 2 replicas. HPA/KEDA on CPU > 50% or queue depth |
| PostgreSQL | 2 CPU / 4 GiB | RDS Multi-AZ. Size connections for (web + worker replicas) × pool |
| Redis / Valkey | 1 CPU / 1.5 GiB | ~**1 GB per 100k events/min** of peak ([cache](https://langfuse.com/self-hosting/deployment/infrastructure/cache#sizing-recommendations)). ≥ 4 vCPU if engine CPU runs hot |
| ClickHouse | 2 CPU / 8 GiB | ≥ 16 GiB RAM for larger deployments. 3 replicas + 3 Keeper. Volume expansion enabled |
| S3 | Serverless | Separate events / media / exports buckets or prefixes |

Set `NODE_OPTIONS=--max-old-space-size=<container memory MiB>` on web and worker so the orchestrator, not V8, enforces the limit ([FAQ](https://langfuse.com/faq/all/self-hosting-javascript-heap-out-of-memory)).

Platform invariants: **Redis `maxmemory-policy noeviction`**. **ClickHouse and Postgres in UTC.** Redis ACL `on ~* +@all` for the Langfuse user.

## 2. High availability

| Component | HA design | Failure behaviour |
|---|---|---|
| web | Stateless, ≥ 2 replicas behind the ALB, PodDisruptionBudget, spread across AZs | ALB drains failed pods. Readiness `/api/public/ready` returns 500 after SIGTERM, which gives a graceful drain |
| worker | ≥ 2 replicas. Scheduled jobs run on one replica each | The queue persists in Redis. Another replica picks up the work |
| Postgres | RDS Multi-AZ | Automatic failover. Apps reconnect |
| Redis | ElastiCache with a replica and automatic failover, or cluster mode (3 primaries + 3 replicas recommended) | After a failover, flush, or eviction (never with noeviction), BullMQ locks can be lost. The worker liveness probe catches a stalled consumer |
| ClickHouse | BYOC (managed), or operator: **3 replicas + 3 Keeper, single shard** (multi-shard is not supported) | Replica loss is tolerated. Replicas cannot be added at runtime without intervention |
| S3 | Regional service | Raw events in S3 are the replay source for failed ingestion jobs |

## 3. Scaling

| Signal | Where | Action |
|---|---|---|
| Worker CPU > 50% (2-CPU pod) | Container metrics | Add worker replicas |
| Queue depth `langfuse.queue.ingestion.depth` tag `type:waiting` | StatsD, or CloudWatch with `ENABLE_AWS_CLOUDWATCH_METRIC_PUBLISHING=true` (published as `langfuse.queue.ingestion.depth.type_waiting` in the `Langfuse` namespace) | Primary autoscaling metric for workers |
| UI slow under heavy ingestion | ALB latency per path | Run a second web Deployment and route `/api/public/ingestion*`, `/api/public/media*`, `/api/public/otel*` to it with **ALB listener path rules** |
| Slow UI/API queries | ClickHouse CPU/memory | Add time filters, then scale ClickHouse vertically. On BYOC, use a read-only compute group via `CLICKHOUSE_READ_ONLY_URL` |
| `socket usage at capacity` in web logs | Web memory rising | Raise `LANGFUSE_S3_CONCURRENT_WRITES` above 50, gradually |
| Redis engine CPU > 90% | ElastiCache metrics | Cluster mode, then queue sharding: `LANGFUSE_INGESTION_QUEUE_SHARD_COUNT`, `LANGFUSE_TRACE_UPSERT_QUEUE_SHARD_COUNT` (2–3× Redis shards; **never decrease**) |
| OTel-only projects | n/a | `LANGFUSE_SKIP_FINAL_FOR_OTEL_PROJECTS=true` skips `FINAL` on observation reads |

## 4. Probes (Kubernetes)

| Container | Probe | Endpoint |
|---|---|---|
| web | readiness | `GET :3000/api/public/ready` (500 once SIGTERM is received) |
| web | liveness | `GET :3000/api/public/health` |
| web | deep check (monitor, not liveness) | `GET :3000/api/public/health?failIfDatabaseUnavailable=true` |
| worker | liveness | `GET :3030/api/health?failIfQueueConsumptionStuck=true`: 503 if no job was picked up or completed for `LANGFUSE_QUEUE_CONSUMPTION_STUCK_THRESHOLD_MINUTES` (default 60) |
| worker | during a v3→v4 dual write only | add `failIfEventPropagationStuck=true` (`initialDelaySeconds` ≥ 60) |

Source: [health and readiness](https://langfuse.com/self-hosting/configuration/health-readiness-endpoints). The demo checks `/api/public/health` and `/api/public/ready` in `../scripts/up.sh`.

Reference snippet (adapt to the chart's `langfuse.web` / `langfuse.worker` values, or to your own manifests):

```yaml
# Ingress (AWS Load Balancer Controller) - internal ALB, plain HTTP to the pod
metadata:
  annotations:
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/certificate-arn: <acm-arn>
    alb.ingress.kubernetes.io/healthcheck-path: /api/public/health
    alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=60
---
# langfuse-web container
env:
  - { name: KEEP_ALIVE_TIMEOUT, value: "<ALB idle timeout + >=5 s>" }   # unit (s vs ms) UNVERIFIED; check your release
  - { name: NODE_OPTIONS, value: "--max-old-space-size=4096" }
readinessProbe: { httpGet: { path: /api/public/ready,  port: 3000 }, periodSeconds: 5 }
livenessProbe:  { httpGet: { path: /api/public/health, port: 3000 }, periodSeconds: 10, failureThreshold: 6 }
lifecycle: { preStop: { exec: { command: ["sleep", "20"] } } }   # let the ALB deregister before exit
---
# langfuse-worker container
livenessProbe:
  httpGet: { path: "/api/health?failIfQueueConsumptionStuck=true", port: 3030 }
  periodSeconds: 30
  failureThreshold: 3
```

### Configuration and secrets

| Item | Where it lives | Rotation |
|---|---|---|
| `SALT`, `NEXTAUTH_SECRET`, `ENCRYPTION_KEY` (64 hex) | Secrets Manager → External Secrets → web + worker | `SALT` and `ENCRYPTION_KEY` are not casual rotations: they hash API keys and encrypt stored credentials. Follow the [encryption guide](https://langfuse.com/self-hosting/configuration/encryption) |
| `LANGFUSE_EE_LICENSE_KEY` | Secrets Manager → web + worker | On renewal |
| DB / Redis / ClickHouse credentials | Secrets Manager (RDS/ElastiCache native rotation where possible) | Per bank policy, with a rolling restart |
| Entra client secret | Secrets Manager → web | Before Entra expiry. Track the expiry date |
| Project / org API keys | Secrets Manager per app and environment | Create new → switch app → delete old. Set an expiry on every key |

## 5. Backups and DR ([backups](https://langfuse.com/self-hosting/configuration/backups))

| Store | Method | Note |
|---|---|---|
| Postgres | RDS automated backups + PITR, plus snapshots before every upgrade | Holds prompts, datasets, configs, users and API-key hashes. **The most critical store** |
| ClickHouse (BYOC) | Managed continuous incremental backups | Backups stay in the bank's account |
| ClickHouse (operator) | `BACKUP DATABASE default TO S3(...)` / `RESTORE`, or VolumeSnapshots (include Keeper volumes), or Velero | Rehearse a restore in the POC |
| S3 | Versioning or replication per bank policy | The events bucket enables replay. The media bucket must not have expiry rules |
| Redis | No backup needed | Transient queue. Events are durable in S3 first |

DR drill in the POC: restore Postgres to a point in time and ClickHouse from backup into a parallel namespace, start web/worker pinned to the **same image version**, and check that the prompts, datasets and last N days of traces are all present. Record the measured RTO.

## 6. Upgrades

- **Versioning:** semver. **Minor and patch releases run their migrations automatically on start.** Majors come with an upgrade guide ([upgrade](https://langfuse.com/self-hosting/upgrade)). There is **no automatic downgrade**.
- **Procedure (minor/patch):** mirror the new tags into ECR → read the release notes → snapshot RDS (and ClickHouse on the operator) → upgrade staging → run smoke tests (ingest a trace, fetch a prompt by label, run an experiment, check judge scores) → roll production. Use the same version for web and worker.
- **Helm:** chart v1 → v2 is a major change (Bitnami sub-charts replaced, ClickHouse via operator). External-store releases upgrade in place ([chart guide](https://langfuse.com/self-hosting/deployment/kubernetes-helm#chart-v1-to-v2)).
- **Existing v3 installs → v4** ([guide](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4)):
  1. Upgrade to the latest v3 (3.225.x) and wait until `SELECT name FROM background_migrations WHERE finished_at IS NULL` returns zero rows.
  2. Upgrade the infrastructure: ClickHouse ≥ 25.12 (26.4 recommended), Postgres ≥ 15, Redis ≥ 7. Apply the new ClickHouse grants. Plan ~3× disk if you backfill.
  3. Back up Postgres and ClickHouse.
  4. Deploy v4 with `LANGFUSE_MIGRATION_V4_WRITE_MODE=legacy|dual` (the default `events_only` rejects old SDKs).
  5. Migrate SDKs (Python ≥ 4.7, JS ≥ 5.4), API consumers, trace-level judges (to observation-level) and exports (to the enriched source).
  6. Backfill (`LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL=true`) or wait for one retention window to roll over.
  7. Cut over to `events_only`. This is the point of commitment.
- **New POC installs start on v4** and skip all of the above.

## 7. Monitoring the platform

| What | How |
|---|---|
| Traces of Langfuse itself | `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_TRACE_SAMPLING_RATIO` on web and worker → in-cluster collector → Dynatrace ([observability](https://langfuse.com/self-hosting/configuration/observability)) |
| Logs | `LANGFUSE_LOG_FORMAT=json` → log pipeline. Alert on repeated `Socket timeout`, `Not enough privileges`, `Dirty database version`, 5xx bursts |
| Queue | `langfuse.queue.ingestion.depth` (waiting), DLQ age metrics ending in `.dlq_oldest_age` |
| Synthetic checks | Health endpoints above. An end-to-end canary: send one trace per minute and read it back through the Observations API v2 within N seconds |
| ClickHouse | Disk (alert at 80%), merges, parts count, memory. Disable or cap system log tables (`trace_log`, `text_log`, …) that grow without TTL ([scaling](https://langfuse.com/self-hosting/configuration/scaling#clickhouse-system-log-tables)) |
| Redis | Memory % (with noeviction, full memory means write errors rather than eviction), CPU, connections |
| RDS | Connections, CPU, storage, replica lag |

## 8. Runbook

| Symptom | Check | Fix |
|---|---|---|
| **Traces accepted (2xx) but never appear in the UI, however long you wait.** Worker logs repeat `Socket timeout. Expecting data, but didn't receive any in 30000ms` for many queues while Redis answers `PING` | `docker compose logs langfuse-worker --since 15m` (demo) or `kubectl logs deploy/langfuse-worker`. Redis health. Worker CPU throttling | **Restart the worker** (`docker compose restart langfuse-worker`, or `kubectl rollout restart deploy/langfuse-worker`). The queue drains within seconds and no data is lost. *Known lesson from long-running demo stacks:* the worker's Redis connections went stale after ~12 days of uptime. Prevent it with the `failIfQueueConsumptionStuck` liveness probe and enough CPU (CPU starvation is the most common cause of this watchdog error) |
| Traces missing, no worker errors | Is the SDK sending to **this** project? Shell-exported `LANGFUSE_*` keys outrank `.env`, which `../scripts/up.sh` guards against. `GET /api/public/projects` with the key. Then web logs, the S3 event object, worker logs, ClickHouse | Unset stray keys. Fix the S3/Redis config the logs point to ([FAQ](https://langfuse.com/faq/all/self-hosting-missing-events-after-ingestion)) |
| 401 on ingestion | Key pair, base URL, project | Recreate the key and update the secret |
| Intermittent 502/504 behind the ALB | ALB idle timeout vs `KEEP_ALIVE_TIMEOUT` | `KEEP_ALIVE_TIMEOUT` ≥ ALB idle timeout + 5 s |
| 5xx during deploys | Readiness probe, `preStop`, deregistration delay | Use `/api/public/ready` for readiness. Deregistration delay longer than in-flight requests |
| Queue depth keeps growing | Worker CPU, ClickHouse insert latency | Scale workers, then ClickHouse. Check that S3 is reachable |
| UI or API slow | Missing time filters, ClickHouse memory | Add time and project filters. Give ClickHouse more RAM. Route reads to a read-only compute group |
| ClickHouse disk growing fast, or `NOT_ENOUGH_SPACE` | `system.parts` size by table, system log tables | Set project retention. Remove or cap system logs. Expand PVCs and roll restart (operator) |
| Retention or deletion jobs time out | Worker logs | Raise `LANGFUSE_CLICKHOUSE_DELETION_TIMEOUT_MS`. Use lightweight deletes (`CLICKHOUSE_LIGHTWEIGHT_DELETE_MODE=lightweight_update`) |
| Empty or odd results, wrong times | `SELECT timezone()` and `SHOW timezone;` | Run both databases in UTC |
| Web won't start: `Dirty database version N` | Migration logs | Fix the cause, then `migrate … force N-1` ([FAQ](https://langfuse.com/faq/all/self-hosting-clickhouse-handling-failed-migrations)) |
| `Not enough privileges` after an upgrade | ClickHouse grants | Apply the v4 grant set |
| `JavaScript heap out of memory` | Pod memory vs heap | Set `NODE_OPTIONS=--max-old-space-size=<MiB>` |
| Judges produce no scores | Evaluator filter (root observation, environment, name or metadata), sampling %, LLM connection test, worker logs | Fix the filter. Test the connection. `Blocked IP address detected` means an internal endpoint is missing from `LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST` |
| Alerts stopped reaching Dynatrace | Automations page: is the trigger disabled? | The trigger is auto-disabled after 5 consecutive failures. Fix the endpoint, then re-enable. Webhook targets must use ports 80/443 and be allowlisted (`LANGFUSE_WEBHOOK_WHITELISTED_HOST`) |
| Audio or media doesn't load in the browser | Presigned URL host reachable from the user's desktop? | Point `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` at an S3 endpoint reachable from the corporate network (see [ARCHITECTURE.md](ARCHITECTURE.md)) |
| Data from some apps appears ~15 min late (v3→v4 dual write only) | SDK versions | Upgrade to Python ≥ 4.7 / JS ≥ 5.4, or send `x-langfuse-ingestion-version: 4` |

## 9. Routine operations calendar

| Cadence | Task |
|---|---|
| Daily | Review platform alerts. Check queue depth trend, ClickHouse disk %, judge failures, blob-export status badge (Error/Active) |
| Weekly | Check the release notes for security fixes. Review API keys by **Last used** and remove stale ones. Check that ClickHouse system log tables stay bounded |
| Monthly | Minor-version upgrade through staging. Restore test of one backup. Capacity review (ingest rate, storage per project vs retention, Redis memory peak). Audit-log export to the evidence store |
| Quarterly | DR drill (full restore into a parallel namespace). Access recertification: org and project memberships against Entra groups. Rotate integration credentials per policy. Re-run the TCO worksheet with actuals ([PATH_TO_PRODUCTION.md](PATH_TO_PRODUCTION.md)) |
