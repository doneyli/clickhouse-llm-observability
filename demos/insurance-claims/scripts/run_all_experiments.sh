#!/usr/bin/env bash
# The experiment matrix behind the demo (SPEC.md §7, §9). Sequential; ~45 min, ~$30 of model calls.
#   ./scripts/run_all_experiments.sh            run everything
#   ./scripts/run_all_experiments.sh --fresh    first delete every experiment run on the demo datasets
# Needs ./scripts/run_services.sh (e2e, adversarial and intake call the MCP server and the A2A service).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
if [ "${1:-}" = "--fresh" ]; then
  $PY - <<'PYEOF'
from claims import lfread
for ds in ("claims-golden-v1", "claims-adversarial-v1", "claims-intake-v1"):
    for e in lfread.experiments(ds):
        print("deleted", ds, e["name"], lfread.delete_experiment(e["id"]))
PYEOF
fi
run() { echo; echo "═══ $*"; $PY -u scripts/run_experiment.py --concurrency "${CONCURRENCY:-12}" "$@" 2>&1 | grep -vE "not found during refresh|Warning|llm = self"; }
run --suite rules --prompt-label production                          # baseline (production v1, Sonnet 5.5)
run --suite rules --prompt-label staging                             # F1 'concise' candidate — the gate must block it
run --suite rules --prompt-label conservative                        # F4 'conservative' — precision drops
run --suite rules --prompt-label production --model claude-sonnet-4-6   # the previous model (why we upgraded)
run --suite rules --prompt-label production --model claude-haiku-5-5    # cost candidate
run --suite rules --prompt-label production --release claims-1.4.0      # F2 retriever bug
run --suite adversarial --prompt-label production
run --suite intake
run --suite e2e --prompt-label production
echo; echo "done"
