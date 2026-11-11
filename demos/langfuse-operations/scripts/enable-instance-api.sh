#!/usr/bin/env bash
# Add ADMIN_API_KEY to the running langfuse-web without changing its image or Compose project.
#
# Recreating web from a different Compose file can silently swap its image (the repository root may pin an
# older Langfuse than the running stack), which downgrades the server against already-migrated databases.
# This script reuses the project, Compose files, and env files Docker recorded when the running container was
# created, adds only docker-compose.instance-api.yaml and the private instance env, and refuses to recreate
# web unless the resulting image is identical to the running one. It never prints credential values.
#
# Usage: scripts/enable-instance-api.sh [--instance-env /absolute/path/instance.env] [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(dirname "$SCRIPT_DIR")"
OVERRIDE="$DEMO_DIR/docker-compose.instance-api.yaml"
INSTANCE_ENV="$DEMO_DIR/private/instance.env"
DRY_RUN=0

die() { echo "Error: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance-env) [[ $# -ge 2 ]] || die "--instance-env requires a path"; INSTANCE_ENV="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -f "$INSTANCE_ENV" ]] || die "instance credentials not found: $INSTANCE_ENV (run scripts/prepare-instance-key.py first)"
docker inspect langfuse-web >/dev/null 2>&1 || die "no running langfuse-web container; start the stack first"

label() { docker inspect --format "{{index .Config.Labels \"$1\"}}" langfuse-web; }
PROJECT="$(label com.docker.compose.project)"
WORKDIR="$(label com.docker.compose.project.working_dir)"
CONFIG_FILES="$(label com.docker.compose.project.config_files)"
ENV_FILES="$(label com.docker.compose.project.environment_file)"
[[ -n "$PROJECT" && -n "$WORKDIR" && -n "$CONFIG_FILES" ]] || die "langfuse-web was not created by Docker Compose; add ADMIN_API_KEY with your own deployment tooling"

ENVS=()
if [[ -n "$ENV_FILES" ]]; then
    IFS=, read -r -a ENVS <<<"$ENV_FILES"
elif [[ -f "$WORKDIR/.env" ]]; then
    # An explicit --env-file disables Compose's implicit .env, so name it explicitly.
    ENVS=("$WORKDIR/.env")
fi

# Later env files win: keep the recorded order (minus any earlier instance.env), then this demo's file.
ARGS=()
for env_file in ${ENVS[@]+"${ENVS[@]}"}; do
    [[ "$(basename "$env_file")" == "instance.env" ]] && continue
    [[ -f "$env_file" ]] || die "the env file that created langfuse-web no longer exists: $env_file"
    ARGS+=(--env-file "$env_file")
done
ARGS+=(--env-file "$INSTANCE_ENV" -p "$PROJECT" --project-directory "$WORKDIR" --profile langfuse)

IFS=, read -r -a FILES <<<"$CONFIG_FILES"
for file in "${FILES[@]}"; do
    # Drop any earlier copy of this override; it is re-added from this checkout below.
    [[ "$(basename "$file")" == "docker-compose.instance-api.yaml" ]] && continue
    [[ -f "$file" ]] || die "the Compose file that created langfuse-web no longer exists: $file"
    ARGS+=(-f "$file")
done
ARGS+=(-f "$OVERRIDE")

RUNNING_IMAGE="$(docker inspect --format '{{.Config.Image}}' langfuse-web)"
# The rendered config contains credentials; it stays in this variable and only the image name is printed.
CONFIG_JSON="$(docker compose "${ARGS[@]}" config --format json)" || die "docker compose could not render the recorded configuration"
PLANNED_IMAGE="$(python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["langfuse-web"]["image"])' <<<"$CONFIG_JSON")" \
    || die "the recorded configuration has no langfuse-web image"

echo "Compose project:  $PROJECT"
echo "Running image:    $RUNNING_IMAGE"
echo "Recreated image:  $PLANNED_IMAGE"
[[ "$RUNNING_IMAGE" == "$PLANNED_IMAGE" ]] || die "recreating langfuse-web would change its image. Refusing; use the Compose files that started this stack."

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry run: images match. Re-run without --dry-run to recreate langfuse-web with ADMIN_API_KEY."
    exit 0
fi
docker compose "${ARGS[@]}" up -d --no-deps langfuse-web
