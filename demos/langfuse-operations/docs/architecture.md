# Implementation and compatibility contract

## Components

Browser forms → loopback Python server → named Bash script → Python workflow. Public REST operations run through the vendored Langfuse CLI wrapper. Two absent CLI surfaces use `scripts/platform-api.mjs`: instance REST with Bearer authentication, and native UI RPC with an ephemeral real user's cookie session. Native signup is separate demo preparation.

The portal accepts only loopback Host/Origin values, a custom request header, JSON, bounded request sizes, named workflows, and validated parameters. One server worker and a filesystem lock serialize operations. This is a local sample, not an authenticated production administration service. Production adaptation needs identity, authorization, tenancy, secret management, job persistence, approvals, and observability designed for that deployment.

| Surface | Credential loader | Supported contract |
| --- | --- | --- |
| Public project / org / SCIM REST | CLI `--env` via vendored wrapper | `api-operations.json`, CLI 1.2.4 rehearsal |
| Instance management REST | Node `--env-file`, `ADMIN_API_KEY` | `/api/admin/organizations`, single org, org apiKeys; GET/POST/DELETE allowlist |
| Native UI governance/audit | Node `--env-file`, synthetic email/password, CSRF and login, in-memory cookie jar | `prompts.addProtectedLabel`, `getProtectedLabels`, `setLabels`; `auditLogs.all` |

Native UI RPC is an internal implementation detail, **not a supported Public API**. Adapter health check requires exactly 4.76.0. Tested against source revision `246b892eae98860e6475940cae593bdb95c2ec95`. On upgrades, verify request schemas, pagination, transformer, role scopes, entitlement checks, auth/session flow, and negative checks, then explicitly change the pin. Prefer a supported API if one becomes available. The local instance create route returned HTTP 201 despite the downloaded instance schema advertising 200, so both successful creation statuses are accepted.

The inspector identifies each surface and credential type. Public operations have method/path/source mappings; native calls display procedure input and their version pin. A denied request passes a governance check only when its exact expected HTTP status occurs, and a separate read proves production was unchanged.

## Resource lifecycle

`applications` records main-organization projects and their generated key IDs, prompt/dataset/dashboard names, and user grants. `governance` identifies one dedicated neutral project, two synthetic login personas, and two prompt versions. `organizations` records instance-created org IDs, org key IDs, starter project IDs, and optional presenter grant. Credential contents live only in private files; browser projections omit private paths.

Reset binds current manifest plus live inventory to a SHA-256 fingerprint. It refuses stale previews, changed project ownership markers, unrelated role grants, unrecorded isolated-org projects, extra org memberships, or extra organization keys. It rechecks isolated-org scope before deletion, deletes the starter project, waits for absence, deletes the now-empty org, verifies 404, then removes generated files/records. A project can already be hidden from inventory while its background purge is still running. Organization DELETE then returns 400; reset retains its records/keys, continues other eligible cleanup, and reports pending organization IDs for a later retry. Main-org projects and eligible SCIM memberships follow the same existing reviewed cleanup. Global accounts cannot be removed with this org-scoped SCIM API.

Known limits: remote mutations and manifest writes are not transactional; crashes between them may need manual reconciliation. No persistent remote job queue exists. Reset cannot prevent another administrator racing a mutation after the last scope check; use an isolated local demo. Event logs are sanitized presentation records, not a substitute for native audit logs or an external tamper-resistant evidence store. No production SSO/password controls are bypassed.

## Verification contract

- Governance: real Member ordinary-label success → protected-label HTTP 403 → unchanged baseline → real Admin promotion → API resolves candidate.
- Audit: actual role transition → native record with matching user, before/after role, and timestamp within the run → role restored in `finally`.
- Instance: record created org → bootstrap key → create recorded project → list/read verifies ownership; rerun does not duplicate.
- Cleanup: missing markers, extra org resources, stale fingerprints, failed/pending deletion, and untrusted private paths must preserve objects or stop cleanup.

Sources: [instance API](https://langfuse.com/self-hosting/administration/instance-management-api), [org API](https://langfuse.com/docs/administration/scim-and-org-api), [audit logs](https://langfuse.com/docs/administration/audit-logs), [protected labels](https://langfuse.com/faq/all/who-can-edit-prompts-and-labels), [pinned audit router](https://github.com/langfuse/langfuse/blob/246b892eae98860e6475940cae593bdb95c2ec95/web/src/server/api/routers/auditLogs.ts), [pinned prompt router](https://github.com/langfuse/langfuse/blob/246b892eae98860e6475940cae593bdb95c2ec95/web/src/features/prompts/server/routers/promptRouter.ts).
