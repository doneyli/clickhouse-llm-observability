# Future add-ons and build specifications

These are planned extensions, not working buttons. Assessment refreshed 2026-11-10 against official documentation via the Langfuse docs MCP, plus local 4.76.0 API contracts. The current portal covers the three initial additions: native audit evidence, protected prompt labels, and instance organization provisioning. The rest of the [self-hosted Enterprise feature list](https://langfuse.com/self-hosting/license-key) is mapped below. Recheck entitlements and the installed schema before building; Cloud plan labels differ from self-hosted licensing.

## Priorities

| Order | Add-on | Value / build size | Paid boundary |
| --- | --- | --- | --- |
| 1 | Central server-side masking | High governance value; deployment + callback service | EE ingestion callback; client-side masking is OSS |
| 2 | LLM connection blueprint | Consistent provider/gateway setup across a project fleet; medium | Org-scoped administration; basic project connections are not universally EE |
| 3 | Scheduled export blueprint | Standardized export onboarding; medium with external storage | Org API automation / dedicated IAM assume-role authentication; self-hosted scheduled exports themselves are included |
| 4 | External media storage | Keep customer-controlled assets in their bucket; medium | EE external S3 resolution; ordinary multimodal tracing is OSS |
| 5 | AWS IAM assume-role flow | Avoid stored long-lived integration access keys; medium/high with AWS prerequisites | EE integration role authentication; ordinary deployment workload IAM is a separate concern |
| 6 | Organization-creator controls | Demonstrate centrally controlled organization sprawl; small deployment change | EE allowlist |
| 7 | UI customization | Fast visual proof of platform standardization; small deployment change | EE environment configuration |
| 8 | Policy reconciliation and expanded lifecycle | Strong scale narrative; medium/high | Existing EE org/instance APIs; orchestration is portal code |

These priorities favor value for a platform administrator. A shorter visual demo could put UI customization ahead of the storage integrations; it is easy to recognize but demonstrates less lifecycle automation.

## 1. Server-side masking

**Scenario:** an application submits synthetic email/account strings without SDK masking. A centrally managed callback masks them before they appear in observation views. Show success and an intentional callback failure with the configured fail mode.

**Inputs and implementation:** add a local callback service and an optional Compose override. Use `LANGFUSE_INGESTION_MASKING_CALLBACK_URL`, timeout, retry count, and fail-closed choice on Worker; propagated-header configuration on Web when needed. Inputs select a synthetic policy, project scope, and explicit failure behavior. Preserve the OTEL schema, modify only approved values, and maintain request authentication/network boundaries. Keep callback URL/config changes in deployment automation rather than pretending they are Admin API calls.

**Proof:** ingest a uniquely identified OTEL event, wait with a bounded timeout, read the persisted observation, and show masked values plus policy result. Inject timeout/HTTP/invalid-JSON failures, proving drop versus unmasked processing as configured. Test legacy-ingestion exclusion separately. Do not log the raw payload in the callback.

**Consideration and rollback:** server-side masking acts after events land in raw blob storage. It does not ensure raw sensitive data never reaches infrastructure; use client-side masking for that requirement. Fail-closed drops telemetry during callback outages; fail-open preserves telemetry but can persist unmasked values. Remove the override to disable callback use, with the behavior change explicitly reviewed. Demo payloads must remain synthetic.

[Source: official masking contract](https://langfuse.com/self-hosting/security/data-masking). Feature/configuration confidence: high. Proposed portal design: planning only.

## 2. LLM connection blueprint

**Scenario:** apply an approved internal gateway connection to selected manifest-owned projects, showing provider/model settings and per-project verification without exposing a provider key.

**Inputs and implementation:** project IDs, adapter, HTTPS base URL, connection name, allowed models, and a server-side secret reference. Discover the installed org-scoped LLM-connection endpoints and schemas first; CLI 1.2.4 only has project-scoped connection commands. Add a narrow REST adapter if the CLI lacks the org endpoint, with explicit surface/auth labels. Save IDs and non-secret settings; secret values stay in private storage. Refuse arbitrary outbound URLs and unowned projects.

**Proof:** inventory before/after, repeat without duplicates, show the connection in each project, and optionally run one explicitly budgeted synthetic request. An inventory check alone must not claim provider connectivity. Test invalid gateway credentials and partial fleet failures.

**Cleanup/decision:** delete only connection IDs created by the portal; restore changed pre-existing configurations only from an explicit saved non-secret snapshot and valid secret reference. A project-by-project setup is simpler for a small demo; org automation better demonstrates consistent platform onboarding. Provider calls are optional and may cost money.

[Source: official org administration](https://langfuse.com/docs/administration/scim-and-org-api), [Public API reference](https://api.reference.langfuse.com). Discovery gap: pin exact org routes, credential schema, and installed-version support before implementation; do not infer them from project commands.

## 3. Scheduled export blueprint

**Scenario:** onboard a project's observations/scores into a dedicated local object-storage prefix, show schedule/configuration read-back, then prove a complete export from its manifest.

**Inputs and implementation:** owned project, provider, bucket, prefix, secret reference or role ARN, schedule, format, compression, export mode, and field groups. Discover the installed `GET/PUT /api/public/integrations/blob-storage` contract and organization-key requirements. Include required `core` fields. Start with local MinIO and a dedicated synthetic prefix; do not use the platform event bucket.

**Proof:** validate storage access, create config, read back, ingest a uniquely identified synthetic observation, and wait for a complete run manifest. Fetch its listed objects and verify the observation ID; a saved configuration or an individual data file is not completed-export proof. Bound polling and show Pending/Error honestly. Test denied bucket access, retry, and repeat configuration.

**Cleanup/decision:** disable the created integration and remove only manifest-owned demo objects after a separate preview. Retain external data by default. Small local JSONL exports are easiest to explain; Parquet helps downstream analytics but adds version/format dependencies. Basic scheduled exports are included for self-hosting, so the purple badge belongs to org-level automation or EE IAM integration authentication, not the entire feature.

[Source: official export configuration and completion protocol](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage). Feature confidence: high; exact org auth/version coverage must be verified against the installed schema.

## 4. External media storage

**Scenario:** display a synthetic image/PDF already in a dedicated bucket through an `s3://` reference, without copying it into Langfuse-managed media storage.

**Inputs and implementation:** owned project, S3/S3-compatible endpoint, bucket, optional prefix, region, path style, and server-side credential reference. Each project supports one integration, so refuse to overwrite an existing unrelated one. Prepare bucket CORS for the browser origin and read-only prefix-scoped access. The tested Public API spec does not expose configuration; first check for a newer supported route, otherwise use documented native setup or a separately pinned UI adapter.

**Proof:** integration Test succeeds for an allowed URI, a traced reference renders in the native UI, and an outside-prefix URI fails. Confirm the source object remains external. Never show signed URLs or credentials in inspector logs.

**Cleanup/decision:** remove the portal-created integration; keep source objects unless separately reviewed. Static prefix-scoped credentials simplify a local demo; AWS role authentication avoids long-lived keys but adds the prerequisites below. Ordinary uploaded multimodal attachments remain a separate included feature.

[Source: official External S3 media guide](https://langfuse.com/docs/observability/features/multi-modality#external-s3-media). Feature confidence: high; programmatic configuration is a build-time discovery gap.

## 5. AWS IAM integration role authentication

**Scenario:** export data or resolve external media through a dedicated customer IAM role, showing trust-policy/external-ID setup and removal without a stored long-lived access key.

**Inputs and implementation:** AWS account, approved role ARN, integration identity, bucket/prefix, and the deployment's intermediate cross-account role. Set `LANGFUSE_CROSS_ACCOUNT_ROLE_ARN` through deployment configuration. Use `authType: ROLE_ARN` plus `roleArn` where the installed integration API supports them; obtain the returned intermediate role ARN and external ID and render an exact trust-policy draft. Apply it only through the user's approved AWS process. Never imply ordinary container-role access to platform buckets is this paid integration feature.

**Proof:** successful prefix-limited operation, rejection outside the prefix, failure with a wrong external ID, and no static integration secret required. Verify both the trust relationship and resource policy. MinIO cannot prove AWS STS assume-role behavior; use an isolated AWS account or clearly mark the AWS portion as a configuration walkthrough.

**Cleanup/decision:** remove the created integration and only the dedicated demo role/policy after inventory review. Avoid changing a shared intermediate role during reset. Compare short-lived role credentials (more setup, stronger isolation) with dedicated static credentials (simpler, rotation obligation).

[Source: official IAM integration authentication](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage#iam-role-auth), [self-hosted blob-storage configuration](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage). Feature confidence: high; live AWS proof requires external infrastructure.

## 6. Organization creator allowlist

**Scenario:** an ordinary demo account cannot create an organization interactively; an allowlisted operator can. Keep this separate from the privileged instance API demonstration.

**Inputs and implementation:** explicit operator email list, deployment target, and reviewed env diff for `LANGFUSE_ALLOWED_ORGANIZATION_CREATORS`. Use a separate Compose override, preserve the previous value, and ensure a working administrator remains available. This is deployment configuration, not a runtime org API operation.

**Proof:** two real user sessions, forbidden/hidden creation for the unlisted account and allowed creation for the listed account, including a direct native request check when practical. Re-verify instance API behavior separately instead of assuming the allowlist governs Bearer administrators.

**Cleanup/decision:** remove the demo-created organization using tracked instance ownership; restore the previous environment and recreate only the affected service. Allowlisting centralizes provisioning but adds operator dependency; open creation is convenient for an isolated development stack.

[Source: official organization creator control](https://langfuse.com/self-hosting/administration/organization-creators). Feature confidence: high; interaction with the instance API remains an explicit acceptance check.

## 7. UI customization

**Scenario:** make the native UI visibly fit an internal platform: neutral logo, internal docs/support links, an instance switcher, and a focused module menu.

**Inputs and implementation:** use a reviewed deployment override for `LANGFUSE_UI_*` link/logo variables, `LANGFUSE_UI_INSTANCE_LINKS`, and visible/hidden module settings. Validate HTTPS/HTTP URLs, unique instance names, public logo assets, and module IDs. Show exact env changes plus before/after screenshots, rather than presenting them as runtime Admin API calls.

**Proof:** light/dark logo and links render, switcher targets are correct, invalid configuration fails visibly in the demo's validation, and module visibility follows documented precedence. Instance switcher links do not share login sessions. Hiding navigation is presentation, not authorization; prove authorization separately through RBAC.

**Cleanup/decision:** restore previous deployment configuration and restart only affected services. A small branding preset is quick and low effort; per-environment presets tell a stronger platform story but require separate deployments or clearly labeled link-only examples.

[Source: official UI configuration](https://langfuse.com/self-hosting/administration/ui-customization). Feature confidence: high.

## 8. Expand policy and lifecycle coverage

**Scope:** desired-state inventory/diff across recorded projects; retention updates and drift checks; org-role changes through SCIM PUT; organization rename/update and API-key expiry/rotation; instance project transfer when supported; export of native audit evidence to an external evidence store when an approved supported read contract exists.

**Implementation:** JSON blueprint with schema version, object IDs, desired settings, and explicit ownership. Read-only plan first, field-level differences, approval token bound to the plan fingerprint, sequential execution with per-object outcomes, retries, and final read-back. Use installed REST schemas; distinguish the CLI's bundled snapshot from server support. Project transfer must verify source and destination inventory and all inherited permissions. Root instance keys stay outside project rotation workflows.

**Proof:** idempotence, intentional drift detection, a bounded fleet, partial failure/resume, missing entitlement, no change to unowned objects, last-owner protections, and expiry/revocation. Retention acceptance must explain that policy changes can delete data asynchronously and cannot recover previously purged data. Never accelerate a retention demo against real telemetry.

**Cleanup/decision:** reconcile only portal-owned resources; preserve original org roles and key references. Inventory-only is safer and cheap; automatic reconciliation provides more operational value but needs a production approval and secret-management model. A true scale/performance claim requires explicit project/user counts, concurrency limits, and API rate-limit measurements beyond this local demo.

[Sources: org and SCIM API](https://langfuse.com/docs/administration/scim-and-org-api), [instance API](https://langfuse.com/self-hosting/administration/instance-management-api), [retention](https://langfuse.com/docs/administration/data-retention). Proposed automation remains unimplemented.

## Explicit exclusions and future build gate

SSO can be configured on self-hosted OSS. Standard dashboards, prompts, datasets, evaluations, uploaded media, and project key permission presets are not blanket paid-only claims. Support/SLA is a commercial service rather than a portal mutation. FIPS and no-telemetry Government capabilities belong to the separate Government license, not ordinary Enterprise. Native SCIM Groups and a custom-role designer are not verified capabilities; do not turn portal rosters into claims about them.

Before implementing any add-on, pin the installed version and API/CLI schemas, record the entitlement/source, identify configuration versus supported REST versus internal UI operations, define private secret loading, use neutral fixtures, add ownership records and reset behavior, and prove both success and a meaningful failure. Provide source/request/response inspection using the existing event model. Include deployment changes as reviewable overrides with a restore path. No button should report completion from a local simulated response.
