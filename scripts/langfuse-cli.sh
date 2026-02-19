#!/bin/bash
# ==============================================================================
# Langfuse CLI Wrapper
# ==============================================================================
# Convenience wrapper that sources .env and forwards all args to the Langfuse CLI.
#
# Usage:
#   ./scripts/langfuse-cli.sh <command> [args...]
#
# Examples:
#   ./scripts/langfuse-cli.sh traces list --limit 5
#   ./scripts/langfuse-cli.sh prompts list
#   ./scripts/langfuse-cli.sh datasets list
#   ./scripts/langfuse-cli.sh scores list
#
# Prerequisites:
#   - Node.js 18+ (for npx)
#   - .env file with LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
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

# Check for Node.js
if ! command -v npx &> /dev/null; then
    echo "Error: npx not found. Install Node.js 18+ to use the Langfuse CLI."
    echo "  https://nodejs.org/"
    exit 1
fi

# Forward all arguments to the Langfuse CLI
exec npx langfuse "$@"
