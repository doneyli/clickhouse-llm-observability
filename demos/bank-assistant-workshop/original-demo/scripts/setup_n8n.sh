#!/usr/bin/env bash
# OBS-02 — wire the local n8n into Langfuse and deploy "Complaint triage (Northwind)".
#
# Idempotent. Safe to re-run after `docker compose down`, a key rotation or a
# workflow edit. What it does:
#   1. points n8n's built-in OpenTelemetry exporter at Langfuse (N8N_OTEL_* in .env)
#   2. creates the n8n owner account once (N8N_OWNER_EMAIL / N8N_OWNER_PASSWORD in .env)
#   3. installs the official Langfuse node (@langfuse/n8n-nodes-langfuse, prompt management)
#   4. creates/updates the Langfuse prompt `northwind-complaint-triage` (label production)
#   5. imports two n8n credentials (Langfuse, Anthropic) — secrets go straight from this
#      demo's .env files into n8n's encrypted store via stdin, never onto disk
#   6. imports + publishes the workflow, restarts n8n so the webhook goes live
#
# Target Langfuse = the same one the Python app uses (northwind/config.py):
# .env.cloud when present (Langfuse Cloud), otherwise the local self-hosted stack.
# Local demo instance only; credentials are demo/test credentials.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL  # shell keys must not leak in

PY=./.venv/bin/python
N8N_CONTAINER=northwind-n8n-1
N8N_URL=http://localhost:5678
WORKFLOW_FILE=n8n/complaint-triage.workflow.json
WORKFLOW_ID=nwComplaintTriage
LANGFUSE_NODE_PKG=@langfuse/n8n-nodes-langfuse@0.2.3

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

wait_for_n8n() {
  for _ in $(seq 1 60); do
    curl -sf "$N8N_URL/healthz" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "n8n did not become healthy at $N8N_URL" >&2; exit 1
}

# ── 1. OTel exporter settings + owner credentials into .env ──────────────────
say "Writing N8N_OTEL_* and owner settings into .env"
"$PY" - <<'PY'
import re, secrets, string
from pathlib import Path
from northwind import config

env = Path(".env")
text = env.read_text() if env.exists() else ""
current = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text, flags=re.M))

base = config.LANGFUSE_BASE_URL
# n8n runs inside the compose network: the self-hosted Langfuse is langfuse-web:3000 there.
otel_base = base.replace("http://localhost:3100", "http://langfuse-web:3000")
values = {
    "N8N_OTEL_ENABLED": "true",
    "N8N_OTEL_EXPORTER_OTLP_ENDPOINT": f"{otel_base}/api/public/otel",
    "N8N_OTEL_EXPORTER_OTLP_HEADERS":
        "Authorization=" + config.basic_auth() + ",x-langfuse-ingestion-version=4",
}
if not current.get("N8N_OWNER_EMAIL"):
    values["N8N_OWNER_EMAIL"] = "owner@northwind.example"
if not current.get("N8N_OWNER_PASSWORD"):
    alphabet = string.ascii_letters + string.digits
    values["N8N_OWNER_PASSWORD"] = "Nw-" + "".join(secrets.choice(alphabet) for _ in range(14)) + "7a"

for key, val in values.items():
    line = f"{key}={val}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", lambda _m: line, text, flags=re.M)
    else:
        text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
env.write_text(text)
print(f"   Langfuse target: {base} (profile {config.PROFILE})")
PY

say "Starting n8n (recreated only if its config changed)"
docker compose up -d n8n >/dev/null
wait_for_n8n

# ── 2. Owner account (first run only) ────────────────────────────────────────
OWNER_EMAIL="$(grep -E '^N8N_OWNER_EMAIL=' .env | cut -d= -f2-)"
OWNER_PASSWORD="$(grep -E '^N8N_OWNER_PASSWORD=' .env | cut -d= -f2-)"
if curl -sf "$N8N_URL/rest/settings" | grep -q '"showSetupOnFirstLoad":true'; then
  say "Creating n8n owner $OWNER_EMAIL"
  OWNER_EMAIL="$OWNER_EMAIL" OWNER_PASSWORD="$OWNER_PASSWORD" "$PY" - <<'PY'
import os, httpx
r = httpx.post("http://localhost:5678/rest/owner/setup", json={
    "email": os.environ["OWNER_EMAIL"], "password": os.environ["OWNER_PASSWORD"],
    "firstName": "Northwind", "lastName": "Platform"}, timeout=30)
r.raise_for_status()
PY
else
  say "n8n owner already set up"
fi

# ── 3. Official Langfuse node (prompt management) ────────────────────────────
NEEDS_RESTART=0
if ! docker exec "$N8N_CONTAINER" test -d /home/node/.n8n/nodes/node_modules/@langfuse/n8n-nodes-langfuse; then
  say "Installing $LANGFUSE_NODE_PKG into n8n"
  # --omit=peer: n8n-workflow is provided by n8n itself (installing it pulls native builds)
  docker exec "$N8N_CONTAINER" sh -c "mkdir -p ~/.n8n/nodes && cd ~/.n8n/nodes && \
    npm install --omit=dev --omit=peer --legacy-peer-deps --no-audit --no-fund $LANGFUSE_NODE_PKG" >/dev/null
  NEEDS_RESTART=1
else
  say "Langfuse node already installed"
fi

# ── 4. Managed prompt in Langfuse ────────────────────────────────────────────
say "Ensuring Langfuse prompt northwind-complaint-triage (label production)"
"$PY" - <<'PY'
from northwind import config

NAME = "northwind-complaint-triage"
PROMPT = """You are the complaint-triage assistant of Northwind Bank (a fictional retail bank).
Classify the customer's complaint. Reply with ONLY a JSON object, no prose, with exactly these keys:
- "category": one of "card_fraud", "fees", "transfers", "service", "other"
- "severity": one of "low", "medium", "high"
- "regulatory_escalation": true when the complaint must be logged as a formal (regulatory) complaint — the customer explicitly asks to file a formal complaint, mentions a regulator, ombudsman or lawyer, alleges discrimination, or reports unauthorised transactions or a loss above 500 USD that is not yet resolved; otherwise false
- "rationale": one short sentence explaining the decision

Categories: card_fraud = unrecognised or unauthorised card transactions, stolen or compromised cards; fees = charges, interest, overdraft or FX fees; transfers = payments, wires or transfers that are delayed, missing or wrong; service = branch, app or call-centre experience; other = anything else.
Severity: high = money lost or at risk right now, a vulnerable customer, or a regulatory threat; medium = money involved but not at immediate risk; low = inconvenience only.
Card numbers and other identifiers were masked before you see the text. Never try to reconstruct them."""
CONFIG = {"model": "claude-haiku-4-5", "temperature": 0, "max_tokens": 300}

try:
    cur = config.api("GET", f"/api/public/v2/prompts/{NAME}", params={"label": "production"})
except RuntimeError as e:
    if "404" not in str(e):
        raise
    cur = None
if cur and cur.get("prompt") == PROMPT and cur.get("config") == CONFIG:
    print(f"   up to date (version {cur['version']})")
else:
    new = config.api("POST", "/api/public/v2/prompts", body={
        "name": NAME, "type": "text", "prompt": PROMPT, "config": CONFIG,
        "labels": ["production"], "tags": ["n8n", "customer-care"],
        "commitMessage": "Complaint triage prompt used by the n8n workflow (OBS-02)"})
    print(f"   created version {new['version']}")
PY

# ── 5. Credentials (secrets via stdin → n8n encrypted store) ─────────────────
say "Importing n8n credentials 'Langfuse (Northwind)' and 'Anthropic (Northwind)'"
"$PY" - <<'PY' | docker exec -i "$N8N_CONTAINER" sh -c 'umask 077; cat > /tmp/nw-creds.json'
import json, sys
from northwind import config
if not config.ANTHROPIC_API_KEY:
    sys.exit("ANTHROPIC_API_KEY not found (.env or repo-root .env)")
json.dump([
    {"id": "nwLangfuseCred01", "name": "Langfuse (Northwind)", "type": "langfuseApi",
     "data": {"host": config.LANGFUSE_BASE_URL.replace("http://localhost:3100", "http://langfuse-web:3000"),
              "publicKey": config.LANGFUSE_PUBLIC_KEY, "secretKey": config.LANGFUSE_SECRET_KEY}},
    {"id": "nwAnthropicCred1", "name": "Anthropic (Northwind)", "type": "anthropicApi",
     "data": {"apiKey": config.ANTHROPIC_API_KEY, "url": "https://api.anthropic.com"}},
], sys.stdout)
PY
docker exec "$N8N_CONTAINER" sh -c 'n8n import:credentials --input=/tmp/nw-creds.json; rc=$?; rm -f /tmp/nw-creds.json; exit $rc' 2>&1 | grep -vi -E 'deprecat|N8N_|^ - |^$' || true

# ── 6. Workflow: import, publish, restart ────────────────────────────────────
say "Importing + publishing workflow '$WORKFLOW_ID'"
# The OTLP URL in the exported JSON is Langfuse Cloud US; rewrite it for other targets.
"$PY" - "$WORKFLOW_FILE" <<'PY' | docker exec -i "$N8N_CONTAINER" sh -c 'cat > /tmp/nw-workflow.json'
import json, sys
from northwind import config
wf = json.load(open(sys.argv[1]))
base = config.LANGFUSE_BASE_URL.replace("http://localhost:3100", "http://langfuse-web:3000")
for node in wf["nodes"]:
    if node["name"] == "Send trace to Langfuse":
        node["parameters"]["url"] = f"{base}/api/public/otel/v1/traces"
json.dump([wf], sys.stdout)
PY
docker exec "$N8N_CONTAINER" sh -c "n8n import:workflow --input=/tmp/nw-workflow.json && \
  n8n publish:workflow --id=$WORKFLOW_ID; rc=\$?; rm -f /tmp/nw-workflow.json; exit \$rc" 2>&1 \
  | grep -vi -E 'deprecat|N8N_|^ - |^$|restart n8n' || true

say "Restarting n8n so the published webhook (and any new node) is live"
docker compose restart n8n >/dev/null
wait_for_n8n
sleep 3
[[ "$NEEDS_RESTART" == 1 ]] && echo "   (Langfuse node loaded)"

# Smoke check without spending tokens: a GET on the POST-only webhook answers
# "not registered for GET" once the production webhook is live.
if curl -s "$N8N_URL/webhook/complaint-triage" | grep -q 'make a POST request'; then
  echo "   ✓ webhook live: POST $N8N_URL/webhook/complaint-triage"
else
  echo "   ! webhook not registered yet — check: docker logs $N8N_CONTAINER" >&2
fi
echo
echo "n8n UI:     $N8N_URL  (login: N8N_OWNER_EMAIL / N8N_OWNER_PASSWORD in .env)"
echo "Run demo:   ./.venv/bin/python scripts/run_n8n_samples.py"
