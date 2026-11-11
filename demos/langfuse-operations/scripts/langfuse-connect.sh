#!/usr/bin/env bash
# ============================================================================
#  Langfuse Connect - sanctioned Langfuse CLI wrapper
# ============================================================================
#
#  The approved way to invoke the Langfuse CLI from this repo. Works on any
#  machine, in any Langfuse region, for any project, with no global install
#  required.
#
#  Usage:
#    ./templates/langfuse-connect.sh api <resource> <action> [flags...]
#    ./templates/langfuse-connect.sh --env-file langfuse.env api prompts list
#    ./templates/langfuse-connect.sh --region us api observations list --limit 5
#    ./templates/langfuse-connect.sh --check          # preflight, prints no data
#    ./templates/langfuse-connect.sh --runner         # print resolved runner
#
#  Credential resolution (first match wins):
#    1. --env-file <path>          passed to the CLI as `--env <path>`
#    2. $LANGFUSE_ENV_FILE
#    3. <repo-root>/langfuse.env   (gitignored by the `*.env` rule)
#    4. LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY already exported
#
#  This wrapper NEVER reads the env file. It hands the path to the CLI, which
#  loads it in its own process, so no secret transits this script's stdout or
#  an agent's context. Get keys from Langfuse UI -> Project settings -> API Keys.
#
#  Host resolution (first match wins):
#    --host <url>  >  --region <eu|us|jp|hipaa>  >  LANGFUSE_BASE_URL /
#    LANGFUSE_HOST in the environment  >  BASE_URL in the env file  >  EU cloud
#
#  A LANGFUSE_HOST already exported in your shell (a local stack, say) wins over
#  an env file's BASE_URL, which is a quiet way to query the wrong server.
#  `--check` prints the host the CLI actually resolved, so check it there.
#
#  API version: left at the CLI's default (latest), which is right for Cloud and
#  for current self-hosted deployments. Against an older self-hosted server pass
#  `--api-version auto` to have the CLI detect it, or pin one with
#  `--api-version 3`. Verified 2026-09-09: `auto` probes /api/public/health
#  before every call and aborts the command if that probe fails, so it costs a
#  round trip and turns an unreachable host into a detection error.
#
#  Runner resolution (first match wins):
#    1. $LANGFUSE_CLI_BIN     e.g. "langfuse" or "npx --yes @langfuse/cli@1"
#    2. `langfuse` on PATH    when it reports major version >= 1
#    3. npx --yes @langfuse/cli@1
#    4. bunx @langfuse/cli@1
#
#  Exit codes are the CLI's own, passed through unchanged:
#    0 success · 2 usage · 3 configuration · 4 network · 5 HTTP error · 6 local file
#  Wrapper-level failures (no runner, no credentials) also exit 3.
#
#  Requires Node.js 20+ or Bun. Docs:
#    https://langfuse.com/docs/api-and-data-platform/features/cli
#    https://github.com/langfuse/langfuse-cli
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Pinned major. The repo's scripts rely on v1 semantics (kebab-case body flags,
# --all pagination, machine-readable exit codes).
CLI_PKG="@langfuse/cli@1"
CLI_MIN_MAJOR=1

usage() {
    cat <<'EOF'
Usage: templates/langfuse-connect.sh [wrapper options] api <resource> <action> [cli flags...]
       templates/langfuse-connect.sh [wrapper options] --check
       templates/langfuse-connect.sh --runner

Wrapper options:
  --env-file <path>        Credentials file, passed to the CLI as --env
  --region <eu|us|jp|hipaa>  Langfuse Cloud region (default: whatever the env says)
  --host <url>             Self-hosted or explicit host URL
  --api-version <version>  Exact/major version, latest, or auto. Default: the
                           CLI's own (latest). Use `auto` for an older
                           self-hosted server; it adds a health probe per call.
  --check                  Preflight: runner, CLI version, host reachability, auth
  --runner                 Print the resolved runner and exit
  -h, --help               This help

Everything after the wrapper options is passed to the Langfuse CLI verbatim:

  templates/langfuse-connect.sh api observations list --limit 5 --json
  templates/langfuse-connect.sh api prompts create --type text --name greeting \
      --prompt 'Hello {{name}}'
  templates/langfuse-connect.sh api observations list --limit 1 --curl   # dry run

Discovery:
  templates/langfuse-connect.sh api help
  templates/langfuse-connect.sh api <resource> help
  templates/langfuse-connect.sh api <resource> <action> --help

First-time setup:
  cp templates/langfuse.env.example langfuse.env   # then fill in your keys
EOF
    exit "${1:-1}"
}

die() { echo "Error: $*" >&2; exit 3; }

# ----------------------------------------------------------------------------
#  Parse wrapper options (stop at the first non-wrapper argument)
# ----------------------------------------------------------------------------

ENV_FILE=""
REGION=""
HOST=""
API_VERSION=""
MODE="passthrough"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)    [[ $# -ge 2 ]] || die "--env-file requires a path"; ENV_FILE="$2"; shift 2 ;;
        --region)      [[ $# -ge 2 ]] || die "--region requires a value"; REGION="$2"; shift 2 ;;
        --host)        [[ $# -ge 2 ]] || die "--host requires a URL"; HOST="$2"; shift 2 ;;
        --api-version) [[ $# -ge 2 ]] || die "--api-version requires a value"; API_VERSION="$2"; shift 2 ;;
        --check)       MODE="check"; shift ;;
        --runner)      MODE="runner"; shift ;;
        -h|--help)     usage 0 ;;
        *)             break ;;
    esac
done

# ----------------------------------------------------------------------------
#  Resolve the runner
# ----------------------------------------------------------------------------

RUNNER=()
RUNNER_SOURCE=""

if [[ -n "${LANGFUSE_CLI_BIN:-}" ]]; then
    # Word-split intentionally: the override may be a multi-word command.
    # shellcheck disable=SC2206
    RUNNER=(${LANGFUSE_CLI_BIN})
    RUNNER_SOURCE="LANGFUSE_CLI_BIN"
elif command -v langfuse &>/dev/null; then
    INSTALLED_VERSION="$(langfuse --version 2>/dev/null | tr -d '[:space:]' || true)"
    INSTALLED_MAJOR="${INSTALLED_VERSION%%.*}"
    if [[ "$INSTALLED_MAJOR" =~ ^[0-9]+$ ]] && (( INSTALLED_MAJOR >= CLI_MIN_MAJOR )); then
        RUNNER=("langfuse")
        RUNNER_SOURCE="langfuse ${INSTALLED_VERSION} on PATH"
    else
        echo "Warning: 'langfuse' on PATH reports version '${INSTALLED_VERSION:-unknown}';" >&2
        echo "         this repo needs >= ${CLI_MIN_MAJOR}.0. Falling back to ${CLI_PKG} via npx/bunx." >&2
        echo "         Upgrade with: npm install -g @langfuse/cli@1" >&2
    fi
fi

if [[ ${#RUNNER[@]} -eq 0 ]]; then
    if command -v npx &>/dev/null; then
        RUNNER=("npx" "--yes" "$CLI_PKG")
        RUNNER_SOURCE="npx ${CLI_PKG}"
    elif command -v bunx &>/dev/null; then
        RUNNER=("bunx" "$CLI_PKG")
        RUNNER_SOURCE="bunx ${CLI_PKG}"
    else
        die "no Langfuse CLI runner found. Install Node.js 20+ or Bun, then either:
    npm install -g @langfuse/cli@1
  or rely on npx/bunx, which this wrapper uses automatically."
    fi
fi

if [[ "$MODE" == "runner" ]]; then
    echo "${RUNNER[*]}  (${RUNNER_SOURCE})"
    exit 0
fi

# ----------------------------------------------------------------------------
#  Resolve credentials source
# ----------------------------------------------------------------------------

if [[ -z "$ENV_FILE" && -n "${LANGFUSE_ENV_FILE:-}" ]]; then
    ENV_FILE="${LANGFUSE_ENV_FILE}"
fi
if [[ -z "$ENV_FILE" && -f "${REPO_ROOT}/langfuse.env" ]]; then
    ENV_FILE="${REPO_ROOT}/langfuse.env"
fi

if [[ -n "$ENV_FILE" ]]; then
    [[ "$ENV_FILE" != /* ]] && ENV_FILE="${REPO_ROOT}/${ENV_FILE}"
    # Metadata check only. This script never reads the file's contents.
    [[ -f "$ENV_FILE" ]] || die "credentials file not found: $ENV_FILE
  Create it from the template:
    cp ${REPO_ROOT}/templates/langfuse.env.example ${REPO_ROOT}/langfuse.env"
elif [[ -z "${LANGFUSE_PUBLIC_KEY:-}" || -z "${LANGFUSE_SECRET_KEY:-}" ]]; then
    die "no Langfuse credentials found.
  Either export LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY, or create a
  credentials file:
    cp ${REPO_ROOT}/templates/langfuse.env.example ${REPO_ROOT}/langfuse.env
  Keys live in the Langfuse UI under Project settings -> API Keys."
fi

# ----------------------------------------------------------------------------
#  Resolve host
# ----------------------------------------------------------------------------

if [[ -z "$HOST" && -n "$REGION" ]]; then
    case "$REGION" in
        eu)    HOST="https://cloud.langfuse.com" ;;
        us)    HOST="https://us.cloud.langfuse.com" ;;
        jp)    HOST="https://jp.cloud.langfuse.com" ;;
        hipaa) HOST="https://hipaa.cloud.langfuse.com" ;;
        *)     die "unknown --region '$REGION'. Use eu, us, jp, hipaa, or pass --host <url>." ;;
    esac
fi

# Only pass --host when the caller asked for one. Otherwise the CLI resolves it
# from LANGFUSE_BASE_URL / LANGFUSE_HOST in the environment or the env file.
GLOBAL_ARGS=()
[[ -n "$ENV_FILE" ]]    && GLOBAL_ARGS+=(--env "$ENV_FILE")
[[ -n "$HOST" ]]        && GLOBAL_ARGS+=(--host "$HOST")
[[ -n "$API_VERSION" ]] && GLOBAL_ARGS+=(--api-version "$API_VERSION")

# ----------------------------------------------------------------------------
#  --check: preflight without printing any project data
# ----------------------------------------------------------------------------

if [[ "$MODE" == "check" ]]; then
    command -v jq &>/dev/null || die "jq is required for --check. Install it (brew install jq)."

    echo "Runner:       ${RUNNER[*]}  (${RUNNER_SOURCE})"
    echo "CLI version:  $("${RUNNER[@]}" --version 2>/dev/null | tr -d '[:space:]' || echo unknown)"
    if [[ -n "$ENV_FILE" ]]; then
        echo "Credentials:  ${ENV_FILE} (via CLI --env)"
    else
        echo "Credentials:  LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY from the environment"
    fi
    RESOLVED_HOST="$("${RUNNER[@]}" ${GLOBAL_ARGS[@]+"${GLOBAL_ARGS[@]}"} api health get --curl 2>/dev/null \
        | sed -nE "s#.*(https?://[^/']+)/api/public/health.*#\\1#p" | head -1)"
    echo "Host:         ${RESOLVED_HOST:-unresolved}"
    echo ""

    HEALTH_RC=0
    HEALTH_JSON="$("${RUNNER[@]}" ${GLOBAL_ARGS[@]+"${GLOBAL_ARGS[@]}"} api health get --json 2>/dev/null)" || HEALTH_RC=$?
    if [[ $HEALTH_RC -eq 0 ]]; then
        echo "Health:       HTTP $(echo "$HEALTH_JSON" | jq -r '.status // "?"') · server $(echo "$HEALTH_JSON" | jq -r '.body.version // "?"')"
    else
        echo "Health:       FAILED (exit ${HEALTH_RC})"
        echo "              2=usage 3=configuration 4=network 5=HTTP 6=local file" >&2
        exit "$HEALTH_RC"
    fi

    # Auth probe. Only the HTTP status is surfaced, never the response body.
    AUTH_RC=0
    AUTH_SCOPE="project"
    AUTH_JSON="$("${RUNNER[@]}" ${GLOBAL_ARGS[@]+"${GLOBAL_ARGS[@]}"} api prompts list --limit 1 --json 2>/dev/null)" || AUTH_RC=$?
    AUTH_STATUS="$(echo "${AUTH_JSON:-}" | jq -r '.status // empty' 2>/dev/null || true)"
    if [[ "${AUTH_STATUS:-}" == "403" ]]; then
        # A valid organization key is denied project endpoints with 403; probe an organization endpoint instead.
        ORG_JSON="$("${RUNNER[@]}" ${GLOBAL_ARGS[@]+"${GLOBAL_ARGS[@]}"} api organizations list-projects --json 2>/dev/null)" || true
        if [[ "$(echo "${ORG_JSON:-}" | jq -r '.status // empty' 2>/dev/null || true)" == "200" ]]; then
            AUTH_STATUS=200
            AUTH_SCOPE="organization"
        fi
    fi
    case "${AUTH_STATUS:-}" in
        200) echo "Auth:         OK (HTTP 200, ${AUTH_SCOPE} key accepted)" ;;
        401) echo "Auth:         REJECTED (HTTP 401). Wrong key pair for this host, or wrong region."; exit 5 ;;
        403) echo "Auth:         DENIED (HTTP 403). The key authenticated but may read neither prompts nor organization projects."; exit 5 ;;
        "")  echo "Auth:         FAILED (exit ${AUTH_RC})"; exit "${AUTH_RC:-5}" ;;
        *)   echo "Auth:         unexpected HTTP ${AUTH_STATUS}"; exit 5 ;;
    esac

    echo ""
    echo "Ready."
    exit 0
fi

# ----------------------------------------------------------------------------
#  Passthrough
# ----------------------------------------------------------------------------

[[ $# -eq 0 ]] && usage 1

exec "${RUNNER[@]}" ${GLOBAL_ARGS[@]+"${GLOBAL_ARGS[@]}"} "$@"
