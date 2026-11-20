#!/usr/bin/env bash
# Seed prompts, judges, datasets and dashboards into every configured Langfuse target — Cloud
# (.env.cloud) and the local self-hosted copy (.env.selfhosted) — so dual mode shows the same
# setup on both. Every seeder is idempotent; each runs once per target with the profile pinned.
#   ./scripts/seed_targets.sh               # every target with an env file
#   ./scripts/seed_targets.sh selfhosted    # one target (cloud | selfhosted)
# The local copy's judges score the mirrored traffic as well, so in dual mode the judge spend
# doubles; seed only `cloud` to keep it single. Traffic and experiments are not seeded here.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL  # shell keys must not leak in
PY=./.venv/bin/python
TARGETS="${*:-}"
if [ -z "$TARGETS" ]; then
  [ -f .env.cloud ] && TARGETS="cloud"
  [ -f .env.selfhosted ] && TARGETS="$TARGETS selfhosted"
fi
[ -n "${TARGETS// /}" ] || { echo "no Langfuse target configured (.env.cloud / .env.selfhosted)" >&2; exit 1; }
for t in $TARGETS; do
  case "$t" in cloud|selfhosted) ;; *) echo "unknown target '$t' (cloud | selfhosted)" >&2; exit 2 ;; esac
  echo "══ $t"
  export NORTHWIND_PROFILE="$t"
  $PY scripts/seed_prompts.py
  $PY scripts/seed_evals.py
  $PY scripts/seed_datasets.py
  $PY scripts/seed_dashboard.py
  $PY scripts/seed_business_dashboard.py
  $PY scripts/seed_compliance_dashboard.py
done
echo "✓ seeded: $TARGETS"
