#!/usr/bin/env bash
# Readiness check for the RLS demo: READY or NOT READY, one line per check, exit 0 or 1.
#
#   bash scripts/verify.sh            # from demos/langfuse-rls
#   bash scripts/verify.sh --links    # also print the link each act opens
#
# The checks live in scripts/verify_demo.ts (unit-tested in tests/verify_demo.test.ts).
# It reads LANGFUSE_* from this demo's .env only; shell exports are ignored.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x node_modules/.bin/tsx ]; then
  echo "  FAIL  dependencies installed  ->  run npm ci in demos/langfuse-rls"
  echo ""
  echo "NOT READY (0/1 checks passed, 1 required failed)"
  exit 1
fi

exec node_modules/.bin/tsx scripts/verify_demo.ts "$@"
