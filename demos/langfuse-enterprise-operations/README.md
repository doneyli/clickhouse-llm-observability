# Langfuse Enterprise Operations — Real platform lifecycle automation from a local portal, covering projects, RBAC, key rotation, audit evidence and reviewed reset

A local portal for real Langfuse platform operations: application and team onboarding,
role changes, key rotation, dashboards, protected prompt labels, native audit evidence,
organization provisioning, and reviewed cleanup. Parameters flow from the forms into
executable scripts. The inspector shows the script, API surface, authentication type,
request, response, object IDs and edition badges. Its main action buttons execute real
operations; workflow tiles only select a form. A completed run can be reopened read-only
at `/?run=<run-id>`.

There is no company: the presenter types application, team and organization names, people
are synthetic `example.invalid` identities, and every prompt and dataset is synthetic.

**The question it answers:** can a platform team run Langfuse for many application teams
as scripted, reviewable operations, and which of those operations need Enterprise?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Governance · Operations |
| **Stack** | Python · Docker |
| **Runs as** | Standalone: own toolchain and `.env` |
| **Langfuse** | Self-hosted · project `Demo Organization` |
| **Length** | 20–30 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 24 minutes, 30 with the optional acts; 16-minute short path)
- **Design record:** [SPEC.md](SPEC.md), which indexes [design.md](design.md) and [docs/architecture.md](docs/architecture.md)

## What it demonstrates

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

## Who it's for

Platform, security and procurement stakeholders evaluating self-hosted Langfuse
Enterprise, and the SA answering "what does the license buy us?". Pick it when the
conversation is about administering Langfuse for many teams. For who sees which traces
inside one project, use [Trace Governance (RLS)](../langfuse-rls/). For app quality,
use any tracing or evaluation demo in the [index](../README.md).

## What runs where

```
 browser ── server.py :8091 (loopback only) ── scripts/<workflow>.sh ── workflows.py
                                                                          │
     ┌────────────────────────────────────────────────────────────────────┤
     ▼ public REST, org or project key                                    ▼ instance REST (Bearer), native UI RPC (session)
 scripts/langfuse-connect.sh → Langfuse CLI 1.x        node --env-file scripts/platform-api.mjs
     └──────────────────────────────┬─────────────────────────────────────┘
                                    ▼
                 local Langfuse :3001 (Enterprise license, server 4.76.0)
```

One server worker and `private/workflow.lock` serialize runs. Python never reads a
credential file. [docs/architecture.md](docs/architecture.md) has the full contract.

**What lands in Langfuse:** no traces. In the org key's organization: projects named in the
form, marked `metadata.demo = langfuse-admin-portal`; per project a key, prompt
`<slug>/system` (label `development`), dataset `<slug>/smoke-tests` with one item, and on
request a dashboard `<name> operations` with a `P95 observation latency` widget. Restricted
key checks write one synthetic `portal-key-permission-check` score (session
`portal-key-check-<projectId>`, environment `portal-demo`). Protected labels add a
governance project (default `Governance Demo`) with prompt `governance-demo/release` and two
SCIM personas. Organizations creates an organization marked `metadata.demo =
langfuse-operations` with a `<name> / Starter application` project. Each role change and
label move appears in the native audit log.

## Set up from scratch

Prerequisites: a local Langfuse at `http://localhost:3001` running server **4.76.0** with an
Enterprise license; Python 3.10+, Node 22+ (tested with 24.13), Bash, and Langfuse CLI 1.x
(tested with 1.2.4); an **organization** API key. Project keys cannot provision projects,
manage memberships, or create organizations. This demo does not change the root stack's
image pin or upgrade its databases.

```bash
cd demos/langfuse-enterprise-operations
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
python3 scripts/seed_all.py      # first run: creates private/ (0700) and private/org.env (0600), then stops
# Fill private/org.env with your organization key using your editor.
python3 scripts/seed_all.py      # idempotent: governance project and personas; no model calls
./scripts/run-portal.sh          # http://127.0.0.1:8091; leave it running
python3 scripts/verify_demo.py   # in a second terminal; should print READY
```

There is no `.env`: [`.env.example`](.env.example) indexes every variable and the private
file that holds it. Shell-exported `LANGFUSE_*` keys are ignored, because the CLI's
`--env` file wins and the seed and verify scripts strip them. `./setup.sh` does not run
this demo's seed. For a fixed rehearsal environment install
`npm install -g @langfuse/cli@1.2.4` first; otherwise the wrapper falls back to a
pinned-major `npx` or `bunx`.

The portal takes `--port`, `--state-dir /absolute/private/path` and
`--org-env /absolute/path/org.env`; pass the same `--state-dir` and `--org-env` to the seed
and verify scripts. The target is restricted to a local HTTP instance. No cloud
configuration is silently substituted.

**Upgrading from `demos/langfuse-operations`:** move its ignored `private/` directory into
this folder before starting the portal. On load, saved application, previous-key, persona
and child-organization credential paths under the former default directory are rebased to
matching files in the active state directory, and the next workflow save persists them.
Custom credential paths stay unchanged, and missing files are not substituted. If the root
`.env` sets `LANGFUSE_INSTANCE_ENV_FILE` to the old directory, update it too.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `python3 scripts/seed_all.py` | Re-proves Protected labels; catches signup or license problems early |
| 30 minutes before | `./scripts/run-portal.sh`, then `python3 scripts/verify_demo.py --links` | Prints READY and the link each act opens |
| 5 minutes before | Open the portal and Langfuse; keep the inspector open | Every act runs from the portal |

Preview Reset in the portal if `verify_demo.py` warns about stale or pending records.

## Run it

```bash
./scripts/run-portal.sh                          # portal on http://127.0.0.1:8091
./scripts/run-portal.sh --port 8092              # another port
```

Every workflow also runs headless. Inputs are JSON files, passed as structured process
arguments without shell evaluation:

```bash
./scripts/protected-labels.sh --params-file /absolute/path/governance.json \
  --state-dir "$PWD/private" --org-env "$PWD/private/org.env"   # {"governanceName":"Governance Demo"}
```

**What you see in Langfuse:** the projects, keys, prompts, datasets, dashboards, memberships
and organizations listed under [What runs where](#what-runs-where), each with the ID the
inspector shows. The audit-log page of the governance project shows the Member → Viewer
change the Audit evidence workflow made.

## Enable organization creation

The instance API uses a separate high-privilege Bearer key. If the instance already has a
key, store that value in the portal's private `instance.env` (mode 0600). For a new
instance, generate one. Skip this if you only need project-level workflows.

```bash
python3 scripts/prepare-instance-key.py   # refuses to overwrite an existing file
```

For a deployment using the repository's root Compose file, set
`LANGFUSE_INSTANCE_ENV_FILE=./demos/langfuse-enterprise-operations/private/instance.env` in
the root `.env`. Use an absolute path for a separate checkout or a custom `--state-dir`.
Keep the credential itself only in the private file. The root Compose file loads it into
`langfuse-web` on later recreates, including with other overlays. Other services import the
root `.env` in full.

```bash
./scripts/enable-instance-api.sh --dry-run   # check the running image first
./scripts/enable-instance-api.sh
```

The root may pin an older image than the running server. The helper reuses the Compose
project, files and env files recorded on the running `langfuse-web`, adds this demo's
override, and checks the image matches before recreating web. Pass
`--instance-env /absolute/path/instance.env` for a custom state directory. Deployments with
a different base Compose file must include the override and private env file on later
recreates. To disable the instance API, finish any demo organization cleanup, remove
`LANGFUSE_INSTANCE_ENV_FILE` from the root `.env`, and recreate web from the matching
deployment files without the override.

## The paid boundary

[Source: official license/edition list](https://langfuse.com/self-hosting/license-key),
[organization API](https://langfuse.com/docs/administration/scim-and-org-api),
[instance API](https://langfuse.com/self-hosting/administration/instance-management-api),
[audit logs](https://langfuse.com/docs/administration/audit-logs),
[protected-label permissions](https://langfuse.com/faq/all/who-can-edit-prompts-and-labels).

Purple identifies a paid capability, blue an OSS application operation, and gray local
preparation. Some Enterprise endpoints start with `/api/public`; the URL prefix does not
determine licensing. OSS supports self-hosting and scaling. SSO, project key permissions,
dashboards, datasets, prompts and evaluations should not be presented as paid-only.

**Protected labels govern human UI roles. Writable project keys can still move protected
labels for CI.** The demo proves this with real user sessions rather than a read-only key.
Synthetic password accounts use the native signup endpoint only for local demo
preparation, then SCIM attaches them to the organization.

## State and safety rails

`private/` holds the ownership manifest (`state.json`), generated project and org keys,
synthetic sign-in credentials, requests and sanitized run history. Runtime files are mode
0600. Never commit or distribute it, and do not discard the manifest before cleanup. The
browser receives safe state projections and sanitized events, never secrets or credential
paths. Native cookies live only inside the Node child process, which allows a fixed
procedure and route list.

Rerunning a completed workflow verifies and reuses its manifest objects. Partial failures
keep IDs for retry; runs are not atomic. A failure between remote creation and local
recording can leave an orphan: reconcile it against the run response and ownership
metadata by hand. The portal refuses to adopt arbitrary existing names.

Reset refuses organization deletion if the live organization has unrecorded projects,
memberships or org keys. It keeps other projects, the original organization and key, the
instance key, history and global accounts. While background project purging blocks an
organization delete, Reset reports **Cleanup pending**, continues eligible cleanup, and
never reports that organization as deleted. Deleted projects cannot be rolled back; key
rotation restores service by issuing another key, not by reviving a revoked one.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (in `private/org.env`) | blank | The organization key every workflow uses |
| `LANGFUSE_BASE_URL` (in `private/org.env`) | `http://localhost:3001` | Informational; `HOST` in `workflows.py` fixes the target |
| `ADMIN_API_KEY` (in `private/instance.env`) | unset | Instance API key; only the Organizations workflow needs it |
| `LANGFUSE_INSTANCE_ENV_FILE` (root `.env`) | `instance.env.example` (empty) | Which file the root compose loads into `langfuse-web` |
| `LANGFUSE_CLI_BIN` | global CLI, then `npx`, then `bunx` | Runner override for `scripts/langfuse-connect.sh` |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Organization name `verify_demo.py` expects the org key to belong to |

## Layout

```
demos/langfuse-enterprise-operations/
├── demo.yaml                     manifest: profile, coverage, commands (feeds the index)
├── README.md                     this file
├── DEMO_SCRIPT.md                the talk track, act by act
├── SPEC.md                       design record index → design.md, docs/architecture.md
├── design.md                     portal visual and interaction rules
├── .env.example                  index of every variable and the private file holding it
├── org.env.example               template for private/org.env
├── instance.env.example          empty file the root compose loads while the instance API is off
├── docker-compose.instance-api.yaml   override adding ADMIN_API_KEY to langfuse-web
├── api-operations.json           CLI operation → method, path, edition, docs (inspector)
├── server.py                     loopback portal on :8091
├── workflows.py                  every workflow; credentials only via the CLI or Node loaders
├── public/                       portal UI (index.html, app.js, style.css)
├── scripts/
│   ├── run-portal.sh             start the portal
│   ├── seed_all.py               the one seed entry point (idempotent)
│   ├── verify_demo.py            readiness check: READY or NOT READY
│   ├── onboard-application.sh … reset-demo.sh   one entry point per workflow
│   ├── langfuse-connect.sh       vendored Langfuse CLI wrapper
│   ├── platform-api.mjs          instance REST and native UI RPC adapter
│   ├── prepare-instance-key.py   generate private/instance.env
│   └── enable-instance-api.sh    recreate langfuse-web with the instance key, same image
├── docs/                         architecture.md, future-addons.md
├── tests/                        no services
└── private/                      gitignored runtime state and credentials
```

## Tests

```bash
cd demos/langfuse-enterprise-operations
python3 -B -m unittest discover -s tests -v   # no services, no model calls, under a second
node --check public/app.js && node --check scripts/platform-api.mjs && bash -n scripts/*.sh
```

They cover validation, redaction, key-role fail-closed checks, rotation, reset preview and
ownership rules, state migration, and `verify_demo.py` and `seed_all.py` against fakes. The
Team form regression runs in Chromium against fixture data; it needs Playwright in a
temporary directory:

```bash
pw_dir="$(mktemp -d)"
npm install --prefix "$pw_dir" playwright@1.61.1
"$pw_dir/node_modules/.bin/playwright" install chromium
NODE_PATH="$pw_dir/node_modules" node --test tests/team-form.test.cjs
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `organization key lists its projects (403 …)` | A project key in `private/org.env`. Create an organization key |
| `organization key lists its projects (401 …)` | Wrong or revoked key, or Langfuse is another instance |
| `Langfuse server … (want 4.76.0)` | Protected labels and Audit evidence refuse other versions. See [docs/architecture.md](docs/architecture.md) before upgrading |
| Seed fails at "Prepare native … sign-in identity" | Signup policy, email verification, SSO enforcement or CAPTCHA. Adapt to approved pre-existing demo accounts |
| `Instance API is not configured` | No `private/instance.env`, or web lacks `ADMIN_API_KEY`: see [Enable organization creation](#enable-organization-creation) |
| `Resource temporarily unavailable` | Another workflow holds `private/workflow.lock`. Wait for the portal run to finish |
| Reset reports **Cleanup pending** | Background project purging. Preview and retry Reset later; records are kept |

## Known limits

- No traces, scores or evaluations of an LLM app: this demo is administration only.
- The native UI RPC adapter is pinned to server 4.76.0 and is not a supported Public API.
- Runs are not transactional; a crash between a remote mutation and the manifest write can
  need manual reconciliation. There is no persistent remote job queue.
- Reset cannot stop another administrator racing a mutation after its last scope check;
  use an isolated local instance.
- Global user accounts remain after Reset; the organization SCIM API cannot delete them.
- Run logs are sanitized presentation records, not a tamper-resistant evidence store.
- It is a loopback sample, not an authenticated administration service; production needs
  identity, authorization, secret management, job persistence and approvals.
- Act minutes are estimates until a rehearsal measures them. Remaining Enterprise
  opportunities are specified in [docs/future-addons.md](docs/future-addons.md).
