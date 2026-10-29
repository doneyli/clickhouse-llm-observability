# Enterprise security

**What this covers:** exact configuration and step lists for SSO with Microsoft Entra ID, RBAC, audit logs, data protection and retention, and export and portability on self-hosted Langfuse v4. Every feature carries its tier.
**Capability IDs:** ENT-01 (SSO Entra ID & RBAC), ENT-02 (Audit logs), ENT-03 (Data protection & retention), ENT-04 (Export & portability). Evidence feeds GATE-01 and GATE-02 in [PATH_TO_PRODUCTION.md](PATH_TO_PRODUCTION.md).

Checked against the Langfuse docs on 2026-10-07. Claims we could not confirm are marked **UNVERIFIED**.

## Tier legend

- **OSS**: MIT-licensed self-hosted. "All core Langfuse features and APIs are available in Langfuse OSS." ([license key](https://langfuse.com/self-hosting/license-key))
- **EE**: self-hosted with `LANGFUSE_EE_LICENSE_KEY` set on **both** web and worker.
- **Cloud**: Langfuse Cloud plan (Hobby / Core / Pro / Enterprise) ([pricing](https://langfuse.com/pricing)).

| Feature | Self-hosted tier | Cloud plan |
|---|---|---|
| SSO (Entra ID, Okta, custom OIDC) + SSO enforcement | OSS | Pro + Teams add-on, Enterprise |
| Organization-level RBAC (Owner/Admin/Member/Viewer/None) | OSS | All plans |
| **Project-level roles** | **EE** | Pro + Teams add-on, Enterprise |
| Auto-provisioning to a default org/project (`LANGFUSE_DEFAULT_*`) | OSS | n/a |
| **SCIM + Organization Management API** | **EE** | Enterprise |
| **Instance Management API** (`ADMIN_API_KEY`) | **EE** | n/a (blocked on Cloud) |
| **Organization creators allowlist, UI customization** | **EE** | n/a |
| **Protected prompt labels** | **EE** | Pro + Teams add-on, Enterprise |
| **Audit log viewer + UI export** | **EE** (the `audit_logs` table is written on every plan) | Enterprise |
| Client-side masking (`mask_otel_spans`) | OSS | All plans |
| **Server-side ingestion masking** | **EE** | n/a |
| **Data retention policies** | **EE** | Pro, Enterprise |
| UI batch export (CSV/JSON) | OSS | All plans |
| Scheduled blob-storage export | OSS | Pro + Teams add-on, Enterprise |
| Public API (Observations v2, Metrics v2, Scores v3, Experiments) | OSS (v4+) | All plans |
| Alerts | OSS (v4+, no limit) | All plans (2 / 20 / 50 / 100 alerts) |

---

## ENT-01: SSO with Microsoft Entra ID (OSS)

Langfuse uses Auth.js. Entra ID is the built-in `AZURE_AD` provider. **SAML is not supported**: enterprise SSO is OIDC only ([Cloud SSO](https://langfuse.com/docs/administration/authentication-and-sso), [self-hosted SSO](https://langfuse.com/self-hosting/security/authentication-and-sso)).

### Steps in Entra ID

1. **App registrations → New registration.** Name `Langfuse (<env>)`, single tenant. Redirect URI (Web) = `https://langfuse.<internal-domain>/api/auth/callback/azure-ad`, which is `<NEXTAUTH_URL>/api/auth/callback/azure-ad`.
2. **Certificates & secrets → New client secret.** Copy the secret **Value**, not the Secret ID, into AWS Secrets Manager.
3. **Token configuration → Add optional claim → ID token → `email`.** Langfuse identifies users by email, so every user needs the Email attribute populated ([discussion #4764](https://github.com/orgs/langfuse/discussions/4764)).
4. **API permissions:** Microsoft Graph delegated `openid`, `profile`, `email`. Grant admin consent.
5. **Enterprise applications → the app → Properties → Assignment required = Yes**, then assign only the POC Entra groups. This is the gate for who can sign in at all.
6. Optional, for IdP-initiated sign-in from My Apps: set the app's home page to `https://langfuse.<internal-domain>/auth/sso-initiate?provider=AZURE_AD`.

### Configuration on langfuse-web

```bash
NEXTAUTH_URL=https://langfuse.<internal-domain>         # must be exact; SSO breaks otherwise
AUTH_AZURE_AD_CLIENT_ID=<application (client) id>
AUTH_AZURE_AD_CLIENT_SECRET=<client secret VALUE>        # from Secrets Manager
AUTH_AZURE_AD_TENANT_ID=<directory (tenant) id>
AUTH_DISABLE_USERNAME_PASSWORD=true                      # SSO only
AUTH_DOMAINS_WITH_SSO_ENFORCEMENT=<bank-email-domain>    # also blocks the password-reset path for that domain
AUTH_AZURE_AD_ALLOW_ACCOUNT_LINKING=true                 # only while migrating existing local users; Entra emails are verified
AUTH_SESSION_MAX_AGE=480                                 # minutes; default 43200 (30 days). Align with bank policy (>5)
AUTH_HTTPS_PROXY=http://<egress-proxy>:<port>            # web -> login.microsoftonline.com (token, JWKS) in a no-egress VPC
LANGFUSE_CSP_ENFORCE_HTTPS=true
# Auto-provisioning on first sign-in (OSS)
LANGFUSE_DEFAULT_ORG_ID=<org-id>
LANGFUSE_DEFAULT_ORG_ROLE=NONE        # NONE + explicit project roles (EE); use VIEWER if OSS only
# LANGFUSE_DEFAULT_PROJECT_ID / LANGFUSE_DEFAULT_PROJECT_ROLE optionally land new users in a sandbox project
```

The demo has these lines commented out in `../docker-compose.yml`. When enabling them, rename `AUTH_AZURE_ALLOW_ACCOUNT_LINKING` to the documented `AUTH_AZURE_AD_ALLOW_ACCOUNT_LINKING` (the pattern is `AUTH_<PROVIDER>_ALLOW_ACCOUNT_LINKING`, where the provider is `AZURE_AD`).

Use generic OIDC instead of the Entra provider if the bank fronts Entra with another broker: `AUTH_CUSTOM_CLIENT_ID`, `AUTH_CUSTOM_CLIENT_SECRET`, `AUTH_CUSTOM_ISSUER`, `AUTH_CUSTOM_NAME`, optional `AUTH_CUSTOM_SCOPE` (default `openid email profile`). Redirect: `/api/auth/callback/custom`.

### Provisioning and joiner/mover/leaver

- **Roles do not come from the IdP token.** Langfuse documents no OIDC group-claim → role mapping, and enterprise SSO "does not automatically provision roles" ([docs](https://langfuse.com/docs/administration/authentication-and-sso)). Roles come from `LANGFUSE_DEFAULT_*`, the UI, the Org Management API, or the SCIM `roles` attribute.
- **SCIM (EE):** base URI `https://langfuse.<internal-domain>/api/public/scim`, authenticated with an **organization-scoped API key** via HTTP Basic. Endpoints: `/Users` (GET/POST), `/Users/{id}` (GET/DELETE), `/Schemas`, `/ResourceTypes`, `/ServiceProviderConfig`. `DELETE /Users/{id}` removes the org membership, not the user ([SCIM](https://langfuse.com/docs/administration/scim-and-org-api)). Set the correct `roles` value **before** enabling SCIM, because re-provisioning overwrites the org role (default `NONE`).
- **UNVERIFIED: Entra ID SCIM against Langfuse.** The documented vendor guide is Okta. Entra non-gallery provisioning normally authenticates with a bearer secret token, while Langfuse documents Basic auth. Test this in week 1. Fallback: a small job that reads Entra group membership through Microsoft Graph and calls `PUT /api/public/organizations/memberships` and `PUT /api/public/projects/{projectId}/memberships`.
- **Break-glass:** with password login disabled there is no local admin. Keep the EE Instance Management API (`ADMIN_API_KEY`, ≥ 32 random bytes, reachable only internally) as the documented recovery path. Re-enabling password login temporarily is the alternative, as a change-controlled config flip.

### RBAC ([docs](https://langfuse.com/docs/administration/rbac))

| Role | Can do | Cannot do |
|---|---|---|
| Owner | Everything, including deleting the project/org and org API keys | |
| Admin | Project settings, members, API keys, LLM connections, integrations, deleting traces, moving protected labels | Delete the project |
| Member | View everything; create scores, datasets, prompts, evaluators; annotate; export | Configure the project, manage keys, move protected labels |
| Viewer | Read-only | Create anything |
| None | No org-wide access. Used with a project role (EE) | |

The effective role in a project is the project role if one is set, otherwise the org role. Users can grant only roles at or below their own.

Recommended mapping for the bank:

| Persona | Org role | Project role (EE) |
|---|---|---|
| AI platform team | Owner (2–3 named people), Admin | n/a |
| Application team | None | Member on their own projects. Admin for one tech lead |
| Risk / compliance / audit | Viewer | n/a (read across projects) |
| SMEs / annotators | None | Member on the projects whose queues they work (no annotation-only role exists) |
| CI pipelines / apps | n/a | **Project API keys** (not user identities), one pair per project per environment |

API keys ([docs](https://langfuse.com/docs/administration/rbac#api-keys)): project keys (`pk-lf-…`/`sk-lf-…`) are not tied to a user. The secret is shown **once** and stored hashed with `SALT`. Set an **expiry**, watch **Last used**, and rotate by creating the new key, switching the app, then deleting the old one. Organization keys (for the Org API and SCIM) are Owner-only.

### Hardening checklist ([hardening](https://langfuse.com/self-hosting/configuration/hardening))

- [ ] `AUTH_DISABLE_USERNAME_PASSWORD=true`, `AUTH_DOMAINS_WITH_SSO_ENFORCEMENT`, Entra "Assignment required".
- [ ] Consider `AUTH_DISABLE_SIGNUP=true` only if every user is pre-provisioned (SCIM/API). It blocks **all** new accounts.
- [ ] `AUTH_SESSION_MAX_AGE` set to the bank's session policy.
- [ ] Outbound allowlists kept minimal: `LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST`, `LANGFUSE_WEBHOOK_WHITELISTED_HOST`, `LANGFUSE_BLOB_STORAGE_ENDPOINT_WHITELISTED_HOST`. Each entry is an SSRF exemption.
- [ ] Code evaluators: leave `LANGFUSE_CODE_EVAL_DISPATCHER` unset, or use `aws-lambda` with no network route back. Never `insecure-local` in a shared instance.
- [ ] `ADMIN_API_KEY` treated as a root credential and reachable only internally.
- [ ] Only web is exposed. Worker, Postgres, Redis, ClickHouse and S3 are never reachable from outside their security groups.

---

## ENT-02: Audit logs (EE)

- **Viewer:** Organization/Project settings → Audit logs. Requires `auditLogs:read` (typically Owner/Admin). Filter by time and project, and **export from the UI** ([audit logs](https://langfuse.com/docs/administration/audit-logs)).
- **Each entry records:** actor type `USER` or `API_KEY`, user or API-key ID, org/project, timestamp, action, the actor's roles at the time, and the **before/after JSON** for updates.
- **Audited resources include:** prompts (create, update, delete, **promote**, **setLabel**, updateTags), protected labels, API keys, LLM API keys, org/project memberships and invitations, projects (incl. transfer), datasets and items, evaluator jobs and templates, scores and score configs, models, integrations (blob storage, PostHog), annotation queues and items, batch exports, comments, traces (delete, publish, bookmark), and sessions (publish).
- **Not covered by retention:** project data retention does not delete audit logs.
- **SIEM integration:** the audit-log page documents no public REST endpoint. The license-key page mentions an audit-log "read API" without documenting it (**UNVERIFIED**; ask Langfuse). The `audit_logs` **Postgres table is written regardless of plan** ([hardening](https://langfuse.com/self-hosting/configuration/hardening#audit-logs)). For continuous SIEM feed, read it from an RDS read replica with a dedicated read-only role and forward to the SIEM. Treat the table schema as internal and re-validate it on upgrades.
- **Platform-level logs** (sign-ins, HTTP access) come from the ALB access logs, `LANGFUSE_LOG_FORMAT=json` container logs, and Entra sign-in logs. They complement the in-app audit log.

---

## ENT-03: Data protection and retention

### Layered PII protection

| Layer | Tier | Where | Notes |
|---|---|---|---|
| 1. Client-side masking (`Langfuse(mask_otel_spans=fn)`, Python SDK ≥ 4.9) | OSS | In the app, before data leaves the pod | Runs at export over raw OTel attributes, **including third-party spans** ([masking](https://langfuse.com/docs/observability/features/masking)). Demo: `../northwind/masking.py` (Luhn-gated card numbers, account numbers, IDs, OTPs; names and addresses need NER) |
| 2. Payload stripping for non-Langfuse exporters | n/a (app code) | The app's APM exporter | Masking applies to the Langfuse copy only. The demo strips prompt/completion attributes from the APM copy (`../northwind/config.py`, see [DYNATRACE.md](DYNATRACE.md)) |
| 3. Server-side ingestion masking | **EE** | Worker → HTTP callback you operate | One policy for every team ([data masking](https://langfuse.com/self-hosting/security/data-masking)). Applies to the **OTel endpoint only** (`/api/public/otel`), not legacy `/api/public/ingestion` |
| 4. Retention | **EE** | Nightly job | Expires what was kept (below) |

Server-side masking config (worker):

```bash
LANGFUSE_INGESTION_MASKING_CALLBACK_URL=https://pii-masker.<ns>.svc.cluster.local/mask
LANGFUSE_INGESTION_MASKING_CALLBACK_FAIL_CLOSED=true   # drop the event if the masker fails (default false = store unmasked)
LANGFUSE_INGESTION_MASKING_CALLBACK_TIMEOUT_MS=500     # default
LANGFUSE_INGESTION_MASKING_MAX_RETRIES=1               # default
# web: LANGFUSE_INGESTION_MASKING_PROPAGATED_HEADERS=<headers to forward to the callback>
```

The callback receives the OTel trace object (headers `X-Langfuse-Org-Id`, `X-Langfuse-Project-Id`) and must return it **in the exact same schema**. Masking runs asynchronously in the worker, so **raw events sit unmasked in the S3 events bucket until processed**. Encrypt that bucket with KMS, restrict access to the Langfuse role, and set a short lifecycle rule on it.

### Encryption

| Data | Control |
|---|---|
| In transit | TLS at the ALB (Langfuse does not terminate TLS itself). `REDIS_TLS_ENABLED=true`. ClickHouse `https://…:8443` + `CLICKHOUSE_MIGRATION_SSL=true`. RDS `sslmode`. Optional service-mesh mTLS to the pod ([encryption](https://langfuse.com/self-hosting/configuration/encryption)) |
| At rest | RDS / ElastiCache KMS. S3 `LANGFUSE_S3_{EVENT_UPLOAD,MEDIA_UPLOAD,BATCH_EXPORT}_SSE=aws:kms` + `…_SSE_KMS_KEY_ID` (grant `kms:GenerateDataKey`, `kms:Decrypt`). ClickHouse BYOC storage encryption, or the encrypted-disk policy on the operator |
| Application level | API keys hashed with `SALT`. Session JWTs encrypted with `NEXTAUTH_SECRET`. LLM-connection and integration credentials encrypted with `ENCRYPTION_KEY` |

### Retention (EE) ([data retention](https://langfuse.com/docs/administration/data-retention))

- Set per project, minimum **3 days**. Without a policy, self-hosted data is kept **indefinitely**.
- A **nightly** job deletes traces (by `timestamp`), observations (`start_time`), scores (`timestamp`) and media (`created_at`) older than N days. **Deleted assets cannot be recovered.**
- **Not deleted:** audit logs, datasets and dataset items (their media too), and dataset-run records. Their linked traces and scores do expire.
- Configure it in **Project Settings → Data Retention** (Owner/Admin), with the API `PUT /api/public/projects/{projectId}` with `retention` (org-scoped key), or at bootstrap with `LANGFUSE_INIT_PROJECT_RETENTION`.
- Self-hosted prerequisite: grant `s3:DeleteObject` on all buckets. With versioned buckets, expire delete markers and non-current versions with a lifecycle rule.
- **Separate S3 events lifecycle:** raw events are re-read for retries and replay, so keep them for your replay window (30 days on Langfuse Cloud). Set a matching TTL on the ClickHouse `blob_storage_file_log` table. **Never put a lifecycle rule on the media bucket.** That breaks references, so let retention handle media ([blob storage](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage#bucket-lifecycle-policies)).
- **Right to erasure:** deleting traces needs Admin (`traces:delete`). For erasure by customer, look up traces by `user_id` and delete them. Confirm the v4 bulk trace-deletion endpoint in the [API reference](https://api.reference.langfuse.com) during the POC (**UNVERIFIED** for v4).

### Telemetry: flag for infosec

With an **EE license, the instance always sends telemetry**, and `TELEMETRY_ENABLED=false` does not stop it ([hardening](https://langfuse.com/self-hosting/configuration/hardening#telemetry)). The demo sets that variable, but it does not take effect with a license key. Per the [telemetry page](https://langfuse.com/self-hosting/security/telemetry), the web container sends **aggregated counts only**, at most every 12 hours, to `eu.posthog.com`: version, license key, up to 30 email **domains** with user counts, and counts of projects, traces, observations, scores, datasets and runs. No traces, prompts or dataset contents are sent. A blocked host fails gracefully. **Open question for Langfuse/ClickHouse:** is a no-egress deployment (telemetry blocked) acceptable under the license terms, or is an offline or alternative reporting arrangement available?

---

## ENT-04: Export and portability

| Path | Tier / role | Use for | Details |
|---|---|---|---|
| **Public REST API** | OSS. Project key | Programmatic reads | Observations API v2 `/api/public/v2/observations` (field groups, cursor paging), Metrics API v2, Scores API v3, Experiments API. The OpenAPI spec is served at `/api/openapi.yaml` on v4.36+ ([public API](https://langfuse.com/docs/api-and-data-platform/features/public-api)) |
| **Scheduled blob-storage export** | OSS. Member+ | Data-platform / warehouse feed | Below |
| **UI batch export** | OSS. Member+ | Ad-hoc analysis | CSV/JSON, honours table filters. Needs `LANGFUSE_S3_BATCH_EXPORT_ENABLED=true` (on in the demo). Cap `BATCH_EXPORT_ROW_LIMIT` (default 1.5M) |
| **Direct ClickHouse** | Self-hosted | Internal dashboards, forensics | Read-only user or compute group. **The schema is not a stable API**: re-validate on every upgrade ([docs](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse#direct-clickhouse-access)) |

### Scheduled blob-storage export: steps ([docs](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage))

1. Create a dedicated S3 bucket for the data platform (KMS, gateway endpoint). On self-hosted, leave the access keys blank to use the pod's IAM role (Amazon S3 provider only).
2. **Project Settings → Integrations → Blob Storage**: provider, bucket, prefix. Click **Validate**.
3. Format **Parquet** (default; also CSV, JSON, JSONL). Schedule: **every 20 min**, hourly, daily or weekly. Mode: full history, from setup date, or from a custom date.
4. **Field groups:** `core` is required. Untick `io` and `metadata` to give analytics a **payload-free** copy (cost, latency, scores, tags, prompt versions) with no customer text.
5. Enable. Choose the **Enriched observations** source (the legacy source stops producing data in v4 `events_only` mode).
6. Downstream: trigger on objects under `{prefix}{project-id}/manifests/`, read `files[]`, load each key, and dedupe by `id` (adjacent windows share a boundary). Make processing idempotent. Langfuse has no native warehouse connector, so the pattern is Parquet to S3, then load.
7. Automate it with `GET`/`PUT /api/public/integrations/blob-storage`.

### Portability

- **Ingest is OpenTelemetry-native.** The same spans can go to Langfuse and any other OTLP backend, so instrumentation is not proprietary.
- **The data stays in the bank's stores** (RDS, S3, ClickHouse BYOC). The code is MIT. Migrating between Langfuse instances or ClickHouse services is documented ([ClickHouse migration](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse#migrating-to-a-new-instance)).
- Prompts, datasets and evaluator definitions can be exported and seeded as code through the API, as the demo's seeders in `../scripts/` do.
