# Langfuse CLI

Interact with your Langfuse traces, prompts, datasets, and scores from the terminal.

---

## Prerequisites

- **Node.js 18+** (for `npx`) — [Install](https://nodejs.org/)
- **`.env` file** with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` set

The wrapper script at `scripts/langfuse-cli.sh` sources `.env` automatically, so you don't need to export variables manually.

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
# List recent traces
./scripts/langfuse-cli.sh traces list --limit 5

# Get a specific trace
./scripts/langfuse-cli.sh traces get <trace-id>

# List prompts
./scripts/langfuse-cli.sh prompts list

# List datasets
./scripts/langfuse-cli.sh datasets list

# List scores
./scripts/langfuse-cli.sh scores list
```

---

## Demo Workflows

### Verify traces after seeding

```bash
# Seed demo data
./scripts/seed-demo-data.sh

# Check that traces were created
./scripts/langfuse-cli.sh traces list --limit 10
```

### Check evaluation scores

```bash
# After configuring LLM-as-a-Judge evaluators in the Langfuse UI:
./scripts/langfuse-cli.sh scores list
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

### `npx: command not found`

Install Node.js 18+: https://nodejs.org/

### `401 Unauthorized`

Check that `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` match your Langfuse project. For self-hosted, the default demo keys are `pk-lf-1234567890` / `sk-lf-1234567890`.

### `ECONNREFUSED`

Langfuse is not running. Start it with `./setup.sh` or check your `LANGFUSE_HOST` URL.
