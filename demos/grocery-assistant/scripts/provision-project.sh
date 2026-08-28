#!/usr/bin/env bash
# Provision a dedicated Langfuse project + API keys for this demo on the
# SELF-HOSTED stack, and write them into ./.env.
#
# Why Postgres and not an API: self-hosted Langfuse exposes no project-creation
# endpoint without an organization API key, and this stack has none. Every other
# demo in this repo is provisioned the same way.
#
# Auth note: Langfuse checks `fast_hashed_secret_key` — sha256(secret + hex(sha256(SALT)))
# — before falling back to the bcrypt `hashed_secret_key`. The fast hash is what
# makes the key work, so `hashed_secret_key` only has to be unique and non-null.
# The formula was verified against this stack's seeded demo-project key.
#
# Idempotent: re-running detects an existing project and leaves it alone.
#
# Usage:  ./scripts/provision-project.sh
#         LANGFUSE_HOST=http://localhost:3001 ./scripts/provision-project.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_NAME="${GROCERY_PROJECT_NAME:-grocery-assistant}"
PG_CONTAINER="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
HOST="${LANGFUSE_HOST:-http://localhost:3001}"
# Must match SALT in the repo root docker-compose.yaml.
SALT="${LANGFUSE_SALT:-langfuse-salt-min-32-characters-here}"

green(){ printf '  \033[0;32m✓\033[0m %s\n' "$1"; }
warn(){  printf '  \033[1;33m⚠\033[0m %s\n' "$1"; }

docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$" || {
  echo "Postgres container '${PG_CONTAINER}' is not running."
  echo "Start the stack first:  docker compose --profile langfuse up -d"
  exit 1
}

q(){ docker exec -i "$PG_CONTAINER" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }

EXISTING=$(echo "SELECT id FROM projects WHERE name='${PROJECT_NAME}' LIMIT 1;" | q | tr -d '[:space:]')

if [ -n "$EXISTING" ]; then
  green "Project '${PROJECT_NAME}' already exists (${EXISTING})"
  PROJECT_ID="$EXISTING"
  KEYS=$(echo "SELECT public_key FROM api_keys WHERE project_id='${PROJECT_ID}' LIMIT 1;" | q | tr -d '[:space:]')
  if [ -n "$KEYS" ]; then
    warn "Keys already exist for this project. Secrets are hashed and cannot be read back."
    warn "If .env has no working keys, delete the row and re-run:"
    warn "  docker exec -i ${PG_CONTAINER} psql -U postgres -d postgres -c \\"
    warn "    \"DELETE FROM api_keys WHERE project_id='${PROJECT_ID}';\""
    exit 0
  fi
else
  ORG_ID=$(echo "SELECT id FROM organizations ORDER BY created_at LIMIT 1;" | q | tr -d '[:space:]')
  [ -n "$ORG_ID" ] || { echo "No organization found — is this a fresh Langfuse install?"; exit 1; }
  PROJECT_ID="grocery-assistant"
  echo "INSERT INTO projects (id, name, org_id, created_at, updated_at)
        VALUES ('${PROJECT_ID}', '${PROJECT_NAME}', '${ORG_ID}', NOW(), NOW());" | q >/dev/null
  green "Created project '${PROJECT_NAME}' (${PROJECT_ID}) in org ${ORG_ID}"
fi

# Mint a keypair. Random suffix so re-provisioning after a key wipe never
# collides with a historical hash (both hash columns are UNIQUE).
read -r PK SK FAST DISP <<EOF
$(python3 - "$SALT" <<'PY'
import hashlib, secrets, sys
salt_hex = hashlib.sha256(sys.argv[1].encode()).hexdigest()
suffix = secrets.token_hex(12)
pk = f"pk-lf-grocery-{suffix}"
sk = f"sk-lf-grocery-{suffix}"
fast = hashlib.sha256((sk + salt_hex).encode()).hexdigest()
print(pk, sk, fast, f"sk-lf-...{sk[-4:]}")
PY
)
EOF

KEY_ID="grocery-key-$(python3 -c 'import secrets;print(secrets.token_hex(8))')"
echo "INSERT INTO api_keys (id, created_at, note, public_key, hashed_secret_key,
                            display_secret_key, project_id, fast_hashed_secret_key, scope)
      VALUES ('${KEY_ID}', NOW(), 'grocery-assistant demo (provisioned by script)',
              '${PK}', 'unused-bcrypt-${KEY_ID}', '${DISP}', '${PROJECT_ID}', '${FAST}', 'PROJECT');" | q >/dev/null
green "Minted API keypair (${DISP})"

# Verify over HTTP before writing .env — a key that does not authenticate is worse
# than no key, because every later failure looks like an app bug.
RESOLVED=$(curl -s -m 15 -u "${PK}:${SK}" "${HOST}/api/public/projects" || true)
if printf '%s' "$RESOLVED" | grep -q "\"name\":[[:space:]]*\"${PROJECT_NAME}\""; then
  green "Keys authenticate and resolve to '${PROJECT_NAME}' @ ${HOST}"
else
  echo "  Keys did NOT resolve. API said: ${RESOLVED:-<no response>}"
  exit 1
fi

if [ -f .env ]; then
  warn ".env exists — not overwriting. Add these lines yourself:"
  echo ""
  echo "LANGFUSE_PUBLIC_KEY=${PK}"
  echo "LANGFUSE_SECRET_KEY=${SK}"
  echo "LANGFUSE_BASE_URL=${HOST}"
  echo "LANGFUSE_PROJECT_NAME=${PROJECT_NAME}"
else
  cat > .env <<ENVFILE
# Provisioned by scripts/provision-project.sh — gitignored, never commit.
LANGFUSE_PUBLIC_KEY=${PK}
LANGFUSE_SECRET_KEY=${SK}
LANGFUSE_BASE_URL=${HOST}
LANGFUSE_PROJECT_NAME=${PROJECT_NAME}

# The model that plays the assistant, and the one that plays the shopper /
# judges conversations. Set at least one provider key.
ANTHROPIC_API_KEY=
AGENT_MODEL=claude-sonnet-4-6
JUDGE_MODEL=claude-sonnet-4-6
ENVFILE
  green "Wrote .env (add ANTHROPIC_API_KEY before running the demo)"
fi

echo ""
echo "Project ready: ${HOST} → '${PROJECT_NAME}'"
