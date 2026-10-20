#!/usr/bin/env bash
# Production-like traffic for the dashboards and the managed judges (~30 min, ~$20 incl. judges).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
t() { echo; echo "═══ $*"; $PY -u scripts/generate_traffic.py "$@" 2>&1 | grep -vE "not found during refresh|Warning|llm = self|Transient error|Failed to export"; }
t --scenario baseline --concurrency 6          # every golden + adversarial claim, current release, production prompts
t --scenario canary --limit 12 --concurrency 6  # 'concise' candidate as a canary on stacked-rule claims
t --scenario release-1.4.0 --limit 20 --concurrency 6   # the retriever bug: the guard catches the critical misses
echo; echo "done"
