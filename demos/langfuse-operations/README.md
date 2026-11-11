# Langfuse operations

A local portal for real Langfuse platform operations: application and team onboarding, role changes, key rotation, dashboards, protected prompt labels, native audit evidence, organization provisioning, and reviewed cleanup. Parameters flow from the forms into executable scripts. A completed run can be reopened read-only at `/?run=<run-id>`. The inspector exposes the script, API surface, authentication type, request, response, object IDs, and edition badges. Its main action buttons execute real operations; workflow tiles select a form.

## Requirements and start

- A running local Langfuse at `http://localhost:3001`, with an Enterprise license for the paid workflows.
- Python 3.10+, Node 22+ (tested with 24.13), Bash, and Langfuse CLI 1.x (tested with 1.2.4).
- An **organization** key. Project credentials cannot provision projects, manage memberships, or create organizations.
- Native audit/protected-label adapter tested and pinned to **Langfuse 4.76.0**. It refuses other server versions. The root repository stack may pin an older server; this standalone demo does not change that pin or upgrade its databases.

```bash
cd demos/langfuse-operations
mkdir -p private
chmod 700 private
cp org.env.example private/org.env
chmod 600 private/org.env
# Fill private/org.env with your local organization key using your editor.
./scripts/run-portal.sh
```

Open [the portal](http://127.0.0.1:8091). Use `--port`, `--state-dir /absolute/private/path`, or `--org-env /absolute/path/org.env` when needed. The target is intentionally restricted to a local HTTP instance. No cloud configuration is silently substituted.

The vendored `scripts/langfuse-connect.sh` passes credential paths to the CLI's own env loader. Python never reads credential files. The wrapper uses a global CLI if present or a pinned-major npx/bunx fallback. For a fixed rehearsal environment install `npm install -g @langfuse/cli@1.2.4` first. `./scripts/langfuse-connect.sh --env-file "$PWD/private/org.env" --host http://localhost:3001 --check` verifies host and authentication.

## Enable organization creation

The instance API uses a separate high-privilege Bearer key. Organization Basic keys cannot substitute for it. This setup is optional for the other workflows.

```bash
# From demos/langfuse-operations. Refuses to overwrite an existing file.
python3 scripts/prepare-instance-key.py
# Preview, then recreate only the web service with ADMIN_API_KEY added.
./scripts/enable-instance-api.sh --dry-run
./scripts/enable-instance-api.sh
```

Do not recreate web from `../../docker-compose.yaml` by hand. The repository root may pin an older Langfuse than your running stack (on `main` it pins 3.221.1). Recreating web from it would downgrade a 4.76.0 server against its already-migrated databases. `enable-instance-api.sh` reuses the Compose project, files, and env files Docker recorded on the running `langfuse-web` container and adds only this override and `private/instance.env`. It refuses when the recreated image would differ from the running one. Pass `--instance-env /absolute/path/instance.env` when you use a different `--state-dir`. The override only adds `ADMIN_API_KEY`; it does not supply a license or upgrade Langfuse. Keep `private/instance.env` under the same state directory selected by `--state-dir`. If the server already has an instance key, place that key in the private file instead of generating another one. Do not change a shared instance key to suit the demo.

To disable the locally added key, recreate web using the original Compose file without the override. Docker Compose remembers its last file combination; include this override explicitly on subsequent web recreations while the demo is in use. Preserve the private key until any demo organizations have been reset.

## Workflows and the paid boundary

| Workflow | Real behavior | Surface / edition |
| --- | --- | --- |
| Application | Create 1–5 projects with metadata and retention, issue explicitly scoped keys, seed prompt/dataset/example, read back | Organization API: Enterprise; project assets: OSS |
| Team / Permissions / Offboarding | SCIM-provision synthetic identities; grant/change/remove individual project roles; verify organization role None before isolated access checks | Organization API: Enterprise |
| Keys | Select Full, Viewer, Ingestion, or Scores ingestion; verify replacement permissions before switching the portal integration, revoke old key and verify rejection | Organization key lifecycle API: Enterprise; project key permission presets also exist in OSS |
| Dashboards | Create a dashboard, latency widget, and placement; read back | Unstable project API: OSS |
| Protected labels | Fresh governance project; real native Member/Admin sessions; Member can change staging, gets HTTP 403 for production; production stays unchanged; Admin promotes v2 | Enterprise governance; **native UI RPC adapter**, not a supported Public API endpoint |
| Audit evidence | Temporarily change the synthetic Member to Viewer, find the native record by user, roles, and timestamp, display actor/time/before/after, restore Member in `finally` | Enterprise audit logs; **native UI RPC read**, separate from portal execution history |
| Organizations | Create isolated organization, issue org key, create starter project, verify inventory; give the existing local `demo@example.com` presenter Admin access when present | Enterprise **Instance Management REST API**, then organization API |
| Reset | Preview live ownership and manifest, bind to a fingerprint, require `RESET DEMO`, delete recorded projects and isolated organizations, remove eligible memberships and local generated credentials | Destructive, reviewed Enterprise administration |

[Source: official license/edition list](https://langfuse.com/self-hosting/license-key), [organization API](https://langfuse.com/docs/administration/scim-and-org-api), [instance API](https://langfuse.com/self-hosting/administration/instance-management-api), [audit logs](https://langfuse.com/docs/administration/audit-logs), [protected-label permissions](https://langfuse.com/faq/all/who-can-edit-prompts-and-labels).

Purple identifies a paid capability, blue an OSS application operation, and gray local preparation. Some Enterprise endpoints start with `/api/public`; the URL prefix does not determine licensing. OSS supports self-hosting and scaling. SSO, project key permissions, dashboards, datasets, prompts, and evaluations should not be presented as universally paid-only features.

**Protected labels govern human UI roles. Writable project keys can still move protected labels for CI.** The demo deliberately proves this using real user sessions rather than a read-only key. Synthetic password accounts use the native signup endpoint only for local demo preparation, then SCIM attaches them to the organization. Signup policies, required email verification, SSO enforcement, or CAPTCHA can block preparation; the portal does not bypass them. Adapt the preparation to approved pre-existing demo accounts if those controls are enabled. Global user accounts remain after reset because the organization SCIM API does not delete them globally.

## Scripts and state

Every workflow has an executable shell entry point and a function in `workflows.py`. Inputs are JSON files, passed as structured process arguments without shell evaluation. For example:

```bash
./scripts/protected-labels.sh --params-file /absolute/path/governance.json \
  --state-dir "$PWD/private" --org-env "$PWD/private/org.env"
```

```json
{"governanceName":"Governance Demo"}
```

`private/` contains the ownership manifest, generated project/org keys, synthetic sign-in credentials, requests, and sanitized run history. Runtime files are mode 0600. Never commit or distribute that folder, and do not discard the manifest before cleanup. The browser receives safe state projections and sanitized events, never secret values or credential paths. Native cookies are ephemeral inside the Node child process. The adapter reads credentials through Node's `--env-file` loader and allows only a fixed procedure/route list.

Rerunning completed workflows verifies/reuses their manifest objects. Partial failures preserve IDs for retry; these are not atomic transactions. A failure between remote creation and local recording can leave an orphan. Reconcile against the run response and ownership metadata manually; the portal intentionally refuses to adopt arbitrary existing names.

Reset refuses organization deletion if the live organization has unrecorded projects, memberships, or org keys. It preserves other projects, the original organization/key, instance key, history, and global accounts. Project data purging is asynchronous. Pending or failed deletions retain records for retry. Reset reports **Cleanup pending** while background project purging prevents organization deletion; it continues eligible cleanup and never reports the pending organization as deleted. There is no rollback for deleted projects; key rotation restores service by issuing another key rather than reviving a revoked secret. See [architecture and contracts](docs/architecture.md) for boundaries and [future add-on specs](docs/future-addons.md) for the remaining Enterprise opportunities.

## Verify

```bash
python3 -B -m unittest discover -s tests -v
node --check public/app.js
node --check scripts/platform-api.mjs
bash -n scripts/*.sh
```

No LLM provider or paid model calls are required. Live proof requires the local licensed stack. [DEMO_SCRIPT.md](DEMO_SCRIPT.md) describes the presenter flow and expected evidence.
