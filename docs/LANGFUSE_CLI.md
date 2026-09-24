# Langfuse CLI

Interact with your Langfuse traces, prompts, datasets, and scores from the terminal.

---

## Prerequisites

- **The Langfuse CLI** — `npm i -g @langfuse/cli` (needs Node.js 18+)
- **`.env` file** with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` set

The wrapper script at `scripts/langfuse-cli.sh` sources `.env` automatically, so you don't need to export variables manually. It also passes `--api-version auto`, which reads `GET /api/public/health` and selects the API snapshot matching your server — that is what lets the same command work against both Langfuse Cloud (v4) and the self-hosted v3 stack.

---

## Authentication

The CLI reads these environment variables (all set in `.env`):

| Variable | Description |
|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Your Langfuse public key |
| `LANGFUSE_SECRET_KEY` | Your Langfuse secret key |
| `LANGFUSE_HOST` | Langfuse URL (`http://localhost:3001` for self-hosted, `https://cloud.langfuse.com` for cloud) |

If you ran `./setup.sh`, these are already configured.

---

## Common Commands

```bash
# List recent spans (the replacement for "list traces" — see below)
./scripts/langfuse-cli.sh api observations list --limit 5

# Everything in one trace
./scripts/langfuse-cli.sh api observations list --trace-id <trace-id>

# List prompts
./scripts/langfuse-cli.sh api prompts list

# List datasets
./scripts/langfuse-cli.sh api datasets list

# List scores
./scripts/langfuse-cli.sh api scores list

# Discover everything the CLI can do — it is generated from the OpenAPI spec,
# so every endpoint is reachable, and deprecated ones are labelled as such
./scripts/langfuse-cli.sh api schema
./scripts/langfuse-cli.sh api <resource> --help
./scripts/langfuse-cli.sh api <resource> <action> --help
```

### Two gotchas worth knowing up front

**`api` is required.** CLI 1.0 moved every REST call under an `api` sub-command.
The older form documented here until recently — `./scripts/langfuse-cli.sh
traces list` — now exits with `Unknown command: traces`. (Pre-1.0 builds printed
the help text and exited `0` instead, so a broken command looked like a quiet
success. If you have scripts from that era, they were doing nothing.) The wrapper
inserts `api` for you and prints a note, but write it explicitly.

**Read span data with `observations`, not `traces`.** `GET /api/public/traces` is
removed in Langfuse v4 (on Cloud: 2026-11-16), and the CLI refuses it outright
against a v4 snapshot rather than letting it fail at the server. `api
observations list` is the portable form — the wrapper passes `--api-version
auto`, so it resolves to `/api/public/v2/observations` against a v4 server and
`/api/public/observations` against the self-hosted v3 one.

Upgrade with `npm i -g @langfuse/cli`. Check what you have with `langfuse
--version`; anything reporting `langfuse-cli 0.x` predates the `api`
sub-command.

---

## Demo Workflows

### Verify traces after seeding

```bash
# Seed demo data
./scripts/seed-demo-data.sh

# Check that traces were created
./scripts/langfuse-cli.sh api observations list --limit 10
```

### Check evaluation scores

```bash
# After configuring LLM-as-a-Judge evaluators in the Langfuse UI:
./scripts/langfuse-cli.sh api scores list
```

### Inspect evaluator setup

`evaluators` and `evaluation-rules` are the stable `/api/public/v2` resources
that replaced the `unstable` ones removed from Cloud on 2026-09-04. Available
against a v4 server only, so on the default self-hosted stack these return 404.

```bash
./scripts/langfuse-cli.sh api evaluators list
./scripts/langfuse-cli.sh api evaluation-rules list
./scripts/langfuse-cli.sh api evaluators list-versions <evaluator-id>
```

---

## Self-Hosted vs Cloud

The only difference is the `LANGFUSE_HOST` value:

| Mode | LANGFUSE_HOST |
|------|---------------|
| Self-hosted | `http://localhost:3001` |
| Cloud | `https://cloud.langfuse.com` (or your custom URL) |

The wrapper script reads this from `.env`, so no changes needed when switching modes.

---

## Troubleshooting

### `the Langfuse CLI is not on PATH`

Install it: `npm i -g @langfuse/cli` (needs Node.js 18+ — https://nodejs.org/).

### `Unknown command: traces`

You are using the pre-1.0 command shape. REST calls go under `api`, so it is
`api observations list`, not `traces list`. See the gotchas above.

### `Cannot call deprecated API operation "legacy-traces list"`

`GET /api/public/traces` is removed in Langfuse v4. Use
`api observations list` (add `--trace-id <id>` for a single trace).

### `API version detection failed: GET .../api/public/health`

Exit code 4 — the server is unreachable, not a CLI problem. For self-hosted,
check the stack is up (`./setup.sh --status`).

### `401 Unauthorized`

Check that `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` match your Langfuse project. For self-hosted, the default demo keys are `pk-lf-1234567890` / `sk-lf-1234567890`.

### `ECONNREFUSED`

Langfuse is not running. Start it with `./setup.sh` or check your `LANGFUSE_HOST` URL.
