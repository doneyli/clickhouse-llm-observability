#!/usr/bin/env bash
# Verify seeded traces using langfuse-cli
# Usage:
#   LANGFUSE_BASE_URL=http://localhost:3001 \
#   LANGFUSE_PUBLIC_KEY=pk-lf-... \
#   LANGFUSE_SECRET_KEY=sk-lf-... \
#   bash scripts/verify.sh
set -euo pipefail

: "${LANGFUSE_BASE_URL:=http://localhost:3001}"
: "${LANGFUSE_PUBLIC_KEY:?LANGFUSE_PUBLIC_KEY must be set}"
: "${LANGFUSE_SECRET_KEY:?LANGFUSE_SECRET_KEY must be set}"

echo "=== Langfuse host: $LANGFUSE_BASE_URL ==="
echo ""

echo "=== Trace count ==="
npx langfuse-cli api traces list --limit 50 --json | jq '.data | length'

echo ""
echo "=== Classification distribution ==="
npx langfuse-cli api traces list --limit 50 --json \
  | jq '[.data[] | .metadata.classification] | group_by(.) | map({classification: .[0], count: length})'

echo ""
echo "=== Team distribution ==="
npx langfuse-cli api traces list --limit 50 --json \
  | jq '[.data[] | .metadata.team] | group_by(.) | map({team: .[0], count: length})'

echo ""
echo "=== Sample traces (first 5) ==="
npx langfuse-cli api traces list --limit 5 --json \
  | jq '.data[] | {id, name, userId, classification: .metadata.classification, team: .metadata.team}'
