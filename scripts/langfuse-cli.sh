#!/bin/bash
# ==============================================================================
# Langfuse CLI Wrapper
# ==============================================================================
# Sources .env and forwards to the Langfuse CLI with the right API snapshot for
# whichever Langfuse this repo is pointed at.
#
# Usage:
#   ./scripts/langfuse-cli.sh api <resource> <action> [args...]
#
# Examples:
#   ./scripts/langfuse-cli.sh api observations list --limit 5
#   ./scripts/langfuse-cli.sh api observations list --trace-id <trace-id>
#   ./scripts/langfuse-cli.sh api prompts list
#   ./scripts/langfuse-cli.sh api datasets list
#   ./scripts/langfuse-cli.sh api scores list
#   ./scripts/langfuse-cli.sh api schema              # every resource + action
#
# Two things about the command shape, both easy to trip over:
#
#   1. `api` is REQUIRED. CLI 1.0 moved every REST call under an `api`
#      sub-command, so the pre-1.0 form (`langfuse-cli.sh traces list`) now
#      exits with "Unknown command: traces". Earlier 0.x builds printed help and
#      exited 0 instead, which is why this was easy to miss.
#
#   2. Read span data with `observations`, not `traces`. `GET /api/public/traces`
#      is removed in Langfuse v4 (Cloud: 2026-11-16) and the CLI refuses it
#      against a v4 snapshot outright. `api observations list` is the portable
#      form: with --api-version auto (set below) it resolves to
#      /api/public/v2/observations on a v4 server and /api/public/observations
#      on a self-hosted v3 one, so the same command works in both deploy modes.
#      Use `--trace-id` to read one trace's observations.
#
# --api-version auto asks GET /api/public/health for the server version and
# picks the matching bundled snapshot. Without it the CLI assumes its newest
# snapshot and rejects endpoints that are perfectly valid against the v3 server
# this repo self-hosts (langfuse/langfuse:3.221.1).
#
# Prerequisites:
#   - the Langfuse CLI on PATH:  npm i -g @langfuse/cli
#   - .env with LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
# ==============================================================================

set -e

# Change to project root
cd "$(dirname "$0")/.."

# Source .env to pick up Langfuse keys and host
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Ensure LANGFUSE_HOST is set (CLI needs this)
export LANGFUSE_HOST="${LANGFUSE_HOST:-${LANGFUSE_BASE_URL:-http://localhost:3001}}"
export LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}"
export LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY}"

if ! command -v langfuse &> /dev/null; then
    echo "Error: the Langfuse CLI is not on PATH. Install it with:"
    echo "  npm i -g @langfuse/cli"
    exit 1
fi

# Nudge the pre-1.0 command shape toward the current one rather than letting the
# CLI answer "Unknown command: traces" with no hint about what changed.
case "${1:-}" in
    traces|observations|prompts|datasets|scores|sessions|comments|models|media|metrics|projects|health)
        echo "Note: REST calls now go under 'api' — running 'api $*' instead." >&2
        echo "      (see the examples in $0)" >&2
        set -- api "$@"
        ;;
esac

# Forward to the Langfuse CLI, letting it match the server's API version.
exec langfuse --api-version auto "$@"
