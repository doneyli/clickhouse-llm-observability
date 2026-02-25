#!/bin/bash
# ==============================================================================
# LLM Observability Demo - Idempotent Setup
# ==============================================================================
# Safe to run multiple times. Never overwrites existing secrets or config.
#
# Usage:
#   ./setup.sh                    # Interactive setup
#   ./setup.sh --seed             # Setup + seed demo data
#   ./setup.sh --status           # Show status of all services
#   ./setup.sh --cleanup          # Stop all containers (preserves data)
#   ./setup.sh --help             # Show help
#
# Prerequisites:
#   - Docker and Docker Compose
#   - Anthropic API key (https://console.anthropic.com/)
# ==============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

#######################################
# Print colored output
#######################################
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  !${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
created() { echo -e "${GREEN}  + Created:${NC} $1"; }
reused() { echo -e "${BLUE}  ✓ Reusing:${NC} $1"; }

header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

#######################################
# Check prerequisites
#######################################
check_prerequisites() {
    header "Checking Prerequisites"

    # Check Docker
    if ! command -v docker &> /dev/null; then
        error "Docker is not installed. Please install Docker first."
        echo "  https://docs.docker.com/get-docker/"
        exit 1
    fi
    success "Docker installed: $(docker --version | head -1)"

    # Check Docker Compose
    if ! docker compose version &> /dev/null; then
        error "Docker Compose is not available. Please install Docker Compose v2."
        exit 1
    fi
    success "Docker Compose installed: $(docker compose version | head -1)"

    # Check Docker is running
    if ! docker info &> /dev/null; then
        error "Docker is not running. Please start Docker first."
        exit 1
    fi
    success "Docker daemon is running"

    # Check openssl
    if ! command -v openssl &> /dev/null; then
        error "openssl is not installed (needed for secret generation)."
        exit 1
    fi
    success "openssl available"
}

#######################################
# Ensure .env exists (never overwrite)
#######################################
ensure_env_file() {
    header "Checking Environment"

    if [ -f .env ]; then
        reused ".env file already exists"
    else
        cp .env.example .env
        created ".env from .env.example"
    fi

    # Source environment
    set -a
    source .env
    set +a

    # Clean up deprecated variables
    if grep -q "^CLICKSTACK_API_KEY=" .env 2>/dev/null; then
        sed -i.bak '/^CLICKSTACK_API_KEY=/d' .env && rm -f .env.bak
        warn "Removed deprecated CLICKSTACK_API_KEY from .env"
    fi
}

#######################################
# Detect and validate deployment mode
#######################################
detect_deploy_mode() {
    DEPLOY_MODE="${DEPLOY_MODE:-self-hosted}"

    case "$DEPLOY_MODE" in
        cloud|self-hosted)
            ;;
        *)
            error "Invalid DEPLOY_MODE='$DEPLOY_MODE'. Must be 'cloud' or 'self-hosted'."
            exit 1
            ;;
    esac

    if [ "$DEPLOY_MODE" = "cloud" ]; then
        info "Deployment mode: ${GREEN}cloud${NC} (Langfuse Cloud — fewer containers)"
    else
        info "Deployment mode: ${BLUE}self-hosted${NC} (full Docker stack)"
    fi
}

#######################################
# Cloud mode: ensure Langfuse Cloud keys are real
#######################################
ensure_langfuse_cloud_keys() {
    if [ "$DEPLOY_MODE" != "cloud" ]; then
        return 0
    fi

    header "Checking Langfuse Cloud Keys"

    # Check if keys are still the self-hosted defaults
    if [ "$LANGFUSE_PUBLIC_KEY" = "pk-lf-1234567890" ] || [ "$LANGFUSE_SECRET_KEY" = "sk-lf-1234567890" ]; then
        warn "Langfuse keys are still set to self-hosted defaults."
        echo ""
        echo -e "  Cloud mode requires real Langfuse Cloud API keys."
        echo -e "  Sign up free at: ${GREEN}https://cloud.langfuse.com${NC}"
        echo -e "  Get keys from: Settings > API Keys"
        echo ""

        read -p "  Enter your Langfuse Public Key: " INPUT_PK
        read -p "  Enter your Langfuse Secret Key: " INPUT_SK

        if [ -z "$INPUT_PK" ] || [ -z "$INPUT_SK" ]; then
            error "Langfuse Cloud API keys are required in cloud mode."
            exit 1
        fi

        sed -i.bak "s|^LANGFUSE_PUBLIC_KEY=.*|LANGFUSE_PUBLIC_KEY=${INPUT_PK}|" .env && rm -f .env.bak
        sed -i.bak "s|^LANGFUSE_SECRET_KEY=.*|LANGFUSE_SECRET_KEY=${INPUT_SK}|" .env && rm -f .env.bak
        export LANGFUSE_PUBLIC_KEY="$INPUT_PK"
        export LANGFUSE_SECRET_KEY="$INPUT_SK"
        created "Langfuse Cloud API keys saved to .env"
    else
        success "Langfuse Cloud API keys are set"
    fi

    # Prompt for LANGFUSE_BASE_URL if still localhost
    if [ "$LANGFUSE_BASE_URL" = "http://localhost:3001" ] || [ -z "$LANGFUSE_BASE_URL" ]; then
        echo ""
        read -p "  Langfuse Cloud URL [https://cloud.langfuse.com]: " INPUT_URL
        INPUT_URL="${INPUT_URL:-https://cloud.langfuse.com}"

        sed -i.bak "s|^LANGFUSE_BASE_URL=.*|LANGFUSE_BASE_URL=${INPUT_URL}|" .env && rm -f .env.bak
        export LANGFUSE_BASE_URL="$INPUT_URL"
        created "LANGFUSE_BASE_URL=${INPUT_URL}"
    else
        success "LANGFUSE_BASE_URL=${LANGFUSE_BASE_URL}"
    fi
}

#######################################
# Ensure LANGFUSE_HOST is set (used by CLI and SDKs)
#######################################
ensure_langfuse_host() {
    local host_val="${LANGFUSE_BASE_URL:-http://localhost:3001}"

    if grep -q "^LANGFUSE_HOST=" .env 2>/dev/null; then
        reused "LANGFUSE_HOST"
    else
        echo "LANGFUSE_HOST=${host_val}" >> .env
        export LANGFUSE_HOST="$host_val"
        created "LANGFUSE_HOST=${host_val}"
    fi
}

#######################################
# Cloud mode: set internal URLs for Docker containers
#######################################
set_cloud_internal_urls() {
    if [ "$DEPLOY_MODE" != "cloud" ]; then
        return 0
    fi

    local cloud_url="${LANGFUSE_BASE_URL}"

    if grep -q "^LANGFUSE_INTERNAL_URL=" .env 2>/dev/null; then
        reused "LANGFUSE_INTERNAL_URL"
    else
        echo "LANGFUSE_INTERNAL_URL=${cloud_url}" >> .env
        export LANGFUSE_INTERNAL_URL="$cloud_url"
        created "LANGFUSE_INTERNAL_URL=${cloud_url} (Docker containers will reach Langfuse Cloud)"
    fi
}

#######################################
# Validate critical .env variables
#######################################
validate_env() {
    if [ -z "$LANGFUSE_PUBLIC_KEY" ] || [ -z "$LANGFUSE_SECRET_KEY" ]; then
        warn "LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY not set in .env"
        warn "Langfuse tracing will use default demo keys (pk-lf-1234567890 / sk-lf-1234567890)"
    fi
}

#######################################
# Check/prompt for Anthropic API key
#######################################
ensure_anthropic_key() {
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        success "ANTHROPIC_API_KEY is set"
        return 0
    fi

    echo ""
    warn "ANTHROPIC_API_KEY is not set in .env"
    echo ""
    echo -e "  Get your API key from: ${GREEN}https://console.anthropic.com/${NC}"
    echo ""
    read -p "  Enter your Anthropic API key: " INPUT_KEY

    if [ -z "$INPUT_KEY" ]; then
        error "Anthropic API key is required to run the demo."
        exit 1
    fi

    # Replace the empty value in .env
    if grep -q "^ANTHROPIC_API_KEY=$" .env; then
        sed -i.bak "s|^ANTHROPIC_API_KEY=$|ANTHROPIC_API_KEY=${INPUT_KEY}|" .env && rm -f .env.bak
    else
        echo "ANTHROPIC_API_KEY=${INPUT_KEY}" >> .env
    fi

    export ANTHROPIC_API_KEY="$INPUT_KEY"
    created "ANTHROPIC_API_KEY saved to .env"
}

#######################################
# Generate LibreChat secrets (only if missing)
#######################################
ensure_librechat_secrets() {
    header "Checking LibreChat Secrets"

    local secrets_added=false

    if [ -n "$CREDS_KEY" ]; then
        reused "CREDS_KEY"
    else
        local val=$(openssl rand -hex 32)
        echo "CREDS_KEY=$val" >> .env
        export CREDS_KEY="$val"
        created "CREDS_KEY"
        secrets_added=true
    fi

    if [ -n "$CREDS_IV" ]; then
        reused "CREDS_IV"
    else
        local val=$(openssl rand -hex 16)
        echo "CREDS_IV=$val" >> .env
        export CREDS_IV="$val"
        created "CREDS_IV"
        secrets_added=true
    fi

    if [ -n "$JWT_SECRET" ]; then
        reused "JWT_SECRET"
    else
        local val=$(openssl rand -hex 32)
        echo "JWT_SECRET=$val" >> .env
        export JWT_SECRET="$val"
        created "JWT_SECRET"
        secrets_added=true
    fi

    if [ -n "$JWT_REFRESH_SECRET" ]; then
        reused "JWT_REFRESH_SECRET"
    else
        local val=$(openssl rand -hex 32)
        echo "JWT_REFRESH_SECRET=$val" >> .env
        export JWT_REFRESH_SECRET="$val"
        created "JWT_REFRESH_SECRET"
        secrets_added=true
    fi

    if [ "$secrets_added" = true ]; then
        # Re-source to pick up new values
        set -a
        source .env
        set +a
    fi
}

#######################################
# Derive Langfuse MCP auth token (only if missing)
#######################################
ensure_langfuse_mcp_token() {
    header "Checking Langfuse Configuration"

    if [ -n "$LANGFUSE_PUBLIC_KEY" ] && [ -n "$LANGFUSE_SECRET_KEY" ]; then
        success "Langfuse API keys configured"
    else
        warn "Langfuse keys not set - demo project keys will be used from .env.example"
    fi

    if [ -n "$LANGFUSE_MCP_AUTH_TOKEN" ]; then
        reused "LANGFUSE_MCP_AUTH_TOKEN"
    elif [ -n "$LANGFUSE_PUBLIC_KEY" ] && [ -n "$LANGFUSE_SECRET_KEY" ]; then
        local token
        token=$(echo -n "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" | base64)
        echo "LANGFUSE_MCP_AUTH_TOKEN=$token" >> .env
        export LANGFUSE_MCP_AUTH_TOKEN="$token"
        created "LANGFUSE_MCP_AUTH_TOKEN (derived from API keys)"
    fi
}

#######################################
# Check which services are already running
#######################################
check_running_services() {
    local running=""
    local services=$(docker compose --profile langfuse ps --format '{{.Name}} {{.Status}}' 2>/dev/null || true)

    if echo "$services" | grep -q "langfuse-web.*Up"; then
        running="$running langfuse"
    fi
    if echo "$services" | grep -q "librechat-api.*Up"; then
        running="$running librechat"
    fi
    if echo "$services" | grep -q "mongodb.*Up"; then
        running="$running mongodb"
    fi

    echo "$running"
}

#######################################
# Start services
#######################################
start_services() {
    header "Starting Services"

    local running
    running=$(check_running_services)

    if [ -n "$running" ]; then
        info "Already running:$running"
    fi

    if [ "$DEPLOY_MODE" = "cloud" ]; then
        info "Cloud mode: starting app containers only (Langfuse runs in the cloud)..."
        echo ""
        docker compose up -d
    else
        info "Starting all services (this may take a few minutes on first run)..."
        echo ""
        docker compose --profile langfuse up -d
    fi

    success "Docker Compose services started"

    info "Pre-building demo images (cached if unchanged)..."
    docker compose --profile demo --profile tools build --quiet 2>&1 || true
    success "Demo images ready"
}

#######################################
# Wait for critical services to be healthy
#######################################
wait_for_services() {
    header "Waiting for Services"

    # Wait for Langfuse
    if [ "$DEPLOY_MODE" = "cloud" ]; then
        info "Validating Langfuse Cloud connectivity..."
        local health_url="${LANGFUSE_BASE_URL}/api/public/health"
        if curl -sf "$health_url" > /dev/null 2>&1; then
            success "Langfuse Cloud is reachable at ${LANGFUSE_BASE_URL}"
        else
            warn "Could not reach Langfuse Cloud at ${health_url}"
            warn "Traces may not be ingested. Check your LANGFUSE_BASE_URL and network."
        fi
    else
        info "Waiting for Langfuse to be ready..."
        local attempts=0
        local max_attempts=60
        while ! curl -s http://localhost:${LANGFUSE_PORT:-3001} > /dev/null 2>&1; do
            sleep 2
            attempts=$((attempts + 1))
            if [ $attempts -ge $max_attempts ]; then
                warn "Langfuse not ready after 2 minutes. Check: docker compose --profile langfuse logs langfuse-web"
                return 0
            fi
            echo -n "."
        done
        echo ""
        success "Langfuse is ready"
    fi

    # Wait for LibreChat
    info "Waiting for LibreChat to be ready..."
    local attempts=0
    local max_attempts=60
    while ! curl -s http://localhost:3080/api/health > /dev/null 2>&1; do
        sleep 2
        attempts=$((attempts + 1))
        if [ $attempts -ge $max_attempts ]; then
            warn "LibreChat not ready after 2 minutes. Check: docker compose logs api"
            return 0
        fi
        echo -n "."
    done
    echo ""
    success "LibreChat is ready"
}

#######################################
# Show status and URLs
#######################################
show_status() {
    header "Service Status"

    if [ "$DEPLOY_MODE" = "cloud" ]; then
        docker compose --profile demo --profile tools ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
            docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true
    else
        docker compose --profile langfuse --profile demo --profile tools ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
            docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true
    fi

    header "Access URLs"

    echo ""
    echo -e "  ${GREEN}LibreChat (Chat UI):${NC}        http://localhost:3080"
    echo -e "    First time? Register at http://localhost:3080 (any email/password)"

    if [ "$DEPLOY_MODE" = "cloud" ]; then
        echo -e "  ${GREEN}Langfuse (LLM Traces):${NC}      ${LANGFUSE_BASE_URL}"
        echo -e "    Log in with your Langfuse Cloud account"
    else
        echo -e "  ${GREEN}Langfuse (LLM Traces):${NC}      http://localhost:${LANGFUSE_PORT:-3001}"
        echo -e "    Email: demo@example.com  |  Password: demodemo1!"
    fi
    echo ""

    header "Next Steps"

    echo ""
    echo "  # Seed demo data (generates traces in Langfuse)"
    echo "  ./scripts/seed-demo-data.sh"
    echo ""
    echo "  # Or run demos individually"
    echo "  docker compose run --rm text-to-sql python main.py"
    echo "  docker compose run --rm vector-rag python main.py"
    echo ""
    echo "  # Interactive mode"
    echo "  docker compose run --rm text-to-sql python main.py --interactive"
    echo ""
    echo "  # View logs"
    echo "  docker compose logs -f api"
    echo ""
    echo "  # Stop all services (preserves data)"
    echo "  ./setup.sh --cleanup"
    echo ""
    echo "  # Full reset (destroys all data)"
    echo "  ./scripts/reset.sh"
    echo ""
    echo "  # Langfuse CLI (requires Node.js 18+)"
    echo "  ./scripts/langfuse-cli.sh traces list --limit 5"
    echo ""
}

#######################################
# Cleanup
#######################################
cleanup() {
    header "Stopping Services"

    info "Stopping all containers..."
    if [ "$DEPLOY_MODE" = "cloud" ]; then
        docker compose --profile demo --profile tools down 2>/dev/null || \
            docker compose down 2>/dev/null || true
    else
        docker compose --profile langfuse --profile demo --profile tools down 2>/dev/null || \
            docker compose down 2>/dev/null || true
    fi

    success "All services stopped (data preserved)"

    echo ""
    echo "To remove all data (volumes), run:"
    echo "  ./scripts/reset.sh"
    echo ""
    echo "To start again:"
    echo "  ./setup.sh"
    echo ""
}

#######################################
# Main
#######################################
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║       LLM Observability Demo - Setup                     ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local run_seed=false

    case "${1:-}" in
        --cleanup|-c)
            # Source .env if it exists for variable expansion
            if [ -f .env ]; then
                set -a; source .env; set +a
            fi
            detect_deploy_mode
            cleanup
            exit 0
            ;;
        --status|-s)
            # Source .env if it exists for variable expansion
            if [ -f .env ]; then
                set -a; source .env; set +a
            fi
            detect_deploy_mode
            show_status
            exit 0
            ;;
        --seed)
            run_seed=true
            ;;
        --help|-h)
            echo "Usage: ./setup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  (none)      Interactive setup (idempotent - safe to re-run)"
            echo "  --seed      Setup + seed demo data (single-command experience)"
            echo "  --status    Show status of all services and URLs"
            echo "  --cleanup   Stop all containers (preserves data)"
            echo "  --help      Show this help message"
            echo ""
            echo "Deployment modes (set DEPLOY_MODE in .env):"
            echo "  self-hosted  Full Docker stack including Langfuse (default)"
            echo "  cloud        Use Langfuse Cloud — skips 7 Langfuse containers"
            echo ""
            echo "This script is idempotent:"
            echo "  - Creates .env from .env.example only if .env doesn't exist"
            echo "  - Generates secrets only if they're missing"
            echo "  - Detects and reuses already-running services"
            echo "  - Never overwrites existing configuration"
            echo ""
            exit 0
            ;;
    esac

    check_prerequisites
    ensure_env_file
    detect_deploy_mode
    validate_env
    ensure_anthropic_key
    ensure_langfuse_cloud_keys
    ensure_librechat_secrets
    ensure_langfuse_mcp_token
    ensure_langfuse_host
    set_cloud_internal_urls
    start_services
    wait_for_services
    show_status

    header "Setup Complete!"

    echo ""
    echo -e "${GREEN}Your LLM observability demo is ready!${NC}"
    echo ""

    if [ "$run_seed" = true ]; then
        echo "  Running demo data seeding..."
        echo ""
        "$SCRIPT_DIR/scripts/seed-demo-data.sh"
        "$SCRIPT_DIR/scripts/seed-librechat-agents.sh"
    else
        echo "  Run ./scripts/seed-demo-data.sh to populate sample traces."
        echo "  Or re-run with: ./setup.sh --seed"
        echo ""
    fi
}

main "$@"
