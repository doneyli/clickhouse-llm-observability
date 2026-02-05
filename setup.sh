#!/bin/bash
#
# LLM Observability Demo - One-Click Setup
# =========================================
# This script sets up the complete LLM observability demo with a single command.
#
# Usage:
#   ./setup.sh                    # Interactive setup (prompts for API keys)
#   ./setup.sh --auto             # Auto setup (uses existing .env or prompts)
#   ./setup.sh --cleanup          # Stop and remove all containers
#   ./setup.sh --status           # Show status of all services
#
# Prerequisites:
#   - Docker and Docker Compose
#   - Anthropic API key (https://console.anthropic.com/)
#

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
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

header() {
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================================${NC}"
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
}

#######################################
# Start ClickStack (observability backend)
#######################################
start_clickstack() {
    header "Starting ClickStack (Observability Backend)"

    if docker ps --format '{{.Names}}' | grep -q '^clickstack$'; then
        success "ClickStack is already running"
    else
        info "Starting ClickStack container..."
        docker run -d --name clickstack \
            -p 8080:8080 -p 4317:4317 -p 4318:4318 \
            docker.hyperdx.io/hyperdx/hyperdx-all-in-one

        info "Waiting for ClickStack to be ready..."
        local count=0
        while ! curl -s http://localhost:8080 > /dev/null 2>&1; do
            sleep 2
            count=$((count + 1))
            if [ $count -gt 60 ]; then
                error "ClickStack failed to start after 2 minutes"
                exit 1
            fi
            echo -n "."
        done
        echo ""
        success "ClickStack is ready"
    fi

    # Connect to Docker network
    docker network create clickhouse-llm-observability_default 2>/dev/null || true
    docker network connect clickhouse-llm-observability_default clickstack 2>/dev/null || true
}

#######################################
# Get ClickStack API Key
#######################################
get_clickstack_api_key() {
    if [ -n "$CLICKSTACK_API_KEY" ]; then
        return 0
    fi

    if [ -f .env ] && grep -q "CLICKSTACK_API_KEY=." .env; then
        export CLICKSTACK_API_KEY=$(grep "CLICKSTACK_API_KEY=" .env | cut -d'=' -f2)
        if [ -n "$CLICKSTACK_API_KEY" ]; then
            success "Using ClickStack API key from .env"
            return 0
        fi
    fi

    echo ""
    warn "ClickStack API key not found."
    echo ""
    echo -e "${YELLOW}To get your API key:${NC}"
    echo "  1. Open http://localhost:8080"
    echo "  2. Create an account (any email/password for local use)"
    echo "  3. Go to Team Settings (gear icon)"
    echo "  4. Copy the Ingestion API Key"
    echo ""
    read -p "Enter your ClickStack API key: " CLICKSTACK_API_KEY

    if [ -z "$CLICKSTACK_API_KEY" ]; then
        error "ClickStack API key is required"
        exit 1
    fi
    export CLICKSTACK_API_KEY
}

#######################################
# Get Anthropic API Key
#######################################
get_anthropic_api_key() {
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        return 0
    fi

    if [ -f .env ] && grep -q "ANTHROPIC_API_KEY=." .env; then
        export ANTHROPIC_API_KEY=$(grep "ANTHROPIC_API_KEY=" .env | cut -d'=' -f2)
        if [ -n "$ANTHROPIC_API_KEY" ]; then
            success "Using Anthropic API key from .env"
            return 0
        fi
    fi

    echo ""
    warn "Anthropic API key not found."
    echo ""
    echo -e "${YELLOW}Get your API key from:${NC} https://console.anthropic.com/"
    echo ""
    read -p "Enter your Anthropic API key: " ANTHROPIC_API_KEY

    if [ -z "$ANTHROPIC_API_KEY" ]; then
        error "Anthropic API key is required"
        exit 1
    fi
    export ANTHROPIC_API_KEY
}

#######################################
# Configure environment
#######################################
configure_environment() {
    header "Configuring Environment"

    get_clickstack_api_key
    get_anthropic_api_key

    # Generate secrets if needed
    CREDS_KEY=${CREDS_KEY:-$(openssl rand -hex 32)}
    CREDS_IV=${CREDS_IV:-$(openssl rand -hex 16)}
    JWT_SECRET=${JWT_SECRET:-$(openssl rand -hex 32)}
    JWT_REFRESH_SECRET=${JWT_REFRESH_SECRET:-$(openssl rand -hex 32)}

    # Create .env file
    cat > .env << EOF
# ==============================================================================
# LLM Observability Demo - Environment Configuration
# Generated by setup.sh on $(date)
# ==============================================================================

# Required API Keys
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
CLICKSTACK_API_KEY=${CLICKSTACK_API_KEY}

# LibreChat Secrets (auto-generated)
CREDS_KEY=${CREDS_KEY}
CREDS_IV=${CREDS_IV}
JWT_SECRET=${JWT_SECRET}
JWT_REFRESH_SECRET=${JWT_REFRESH_SECRET}

# ClickHouse MCP Server (public demo database)
CLICKHOUSE_HOST=sql-clickhouse.clickhouse.com
CLICKHOUSE_USER=demo
CLICKHOUSE_PASSWORD=

# LLM Models
ANTHROPIC_MODEL=claude-sonnet-4-20250514
EVALUATOR_MODEL=claude-3-5-haiku-20241022
TEMPERATURE=0.7

# Service Ports
TEXT_TO_SQL_PORT=8002
VECTOR_RAG_PORT=8003

# Langfuse (optional)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://localhost:3001
LANGFUSE_PORT=3001

# Internal
MONGO_URI=mongodb://mongodb:27017/LibreChat
MEILI_HOST=http://meilisearch:7700
MEILI_MASTER_KEY=DrhYf7zENyR6AlUCKmnz0eYASOQdl6zxH7s7MKFSfFCt
ALLOW_REGISTRATION=true
CONSOLE_JSON=true
DEBUG_CONSOLE=true
EOF

    success "Environment configured (.env created)"
}

#######################################
# Build and start services
#######################################
build_and_start() {
    header "Building Docker Images"

    info "This may take 3-5 minutes on first run..."
    docker compose build --parallel
    success "All images built"

    header "Starting Services"

    docker compose up -d
    success "Services started"

    # Wait for services to be healthy
    info "Waiting for services to be ready..."
    sleep 10

    local retries=0
    while [ $retries -lt 30 ]; do
        local healthy=$(docker compose ps --format json 2>/dev/null | grep -c '"healthy"' || echo "0")
        local total=$(docker compose ps -q 2>/dev/null | wc -l | tr -d ' ')

        if [ "$healthy" -ge 3 ]; then
            break
        fi

        sleep 2
        retries=$((retries + 1))
        echo -n "."
    done
    echo ""

    success "Services are ready"
}

#######################################
# Run demo
#######################################
run_demo() {
    header "Running Demo"

    info "The text-to-sql demo runs automatically on startup."
    info "Waiting for demo to complete..."

    sleep 30

    # Check if demo completed
    if docker logs text-to-sql 2>&1 | grep -q "Demo complete"; then
        success "Demo completed successfully"
    else
        warn "Demo may still be running. Check logs with: docker logs text-to-sql"
    fi
}

#######################################
# Show status and URLs
#######################################
show_status() {
    header "Service Status"

    docker compose ps --format "table {{.Name}}\t{{.Status}}"

    header "Access URLs"

    echo ""
    echo -e "  ${GREEN}LibreChat (Chat UI):${NC}        http://localhost:3080"
    echo -e "  ${GREEN}HyperDX (Traces):${NC}           http://localhost:8080"
    echo -e "  ${GREEN}Langfuse (Evaluations):${NC}     http://localhost:3001"
    echo ""

    header "Quick Commands"

    echo ""
    echo "  # View logs"
    echo "  docker compose logs -f text-to-sql"
    echo ""
    echo "  # Run trace evaluator"
    echo "  docker compose run --rm trace-evaluator python main.py --service text-to-sql-demo --hours 1"
    echo ""
    echo "  # Stop all services"
    echo "  ./setup.sh --cleanup"
    echo ""
}

#######################################
# Cleanup
#######################################
cleanup() {
    header "Cleaning Up"

    info "Stopping services..."
    docker compose down || true

    info "Stopping ClickStack..."
    docker stop clickstack 2>/dev/null || true
    docker rm clickstack 2>/dev/null || true

    success "Cleanup complete"

    echo ""
    echo "To remove all data (volumes), run:"
    echo "  docker compose down -v"
    echo "  docker volume rm \$(docker volume ls -q | grep clickhouse-llm-observability)"
}

#######################################
# Main
#######################################
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     LLM Observability Demo - One-Click Setup               ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    case "${1:-}" in
        --cleanup|-c)
            cleanup
            exit 0
            ;;
        --status|-s)
            show_status
            exit 0
            ;;
        --help|-h)
            echo "Usage: ./setup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  (none)      Interactive setup - prompts for API keys"
            echo "  --auto      Auto setup using existing .env or prompts"
            echo "  --cleanup   Stop and remove all containers"
            echo "  --status    Show status of all services"
            echo "  --help      Show this help message"
            echo ""
            exit 0
            ;;
    esac

    check_prerequisites
    start_clickstack
    configure_environment
    build_and_start
    run_demo
    show_status

    header "Setup Complete!"

    echo ""
    echo -e "${GREEN}Your LLM observability demo is now running!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Open http://localhost:8080 to view traces in HyperDX"
    echo "  2. Open http://localhost:3001 to view evaluations in Langfuse"
    echo "  3. Open http://localhost:3080 to chat via LibreChat"
    echo ""
}

main "$@"
