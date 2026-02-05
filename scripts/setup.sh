#!/bin/bash
# ==============================================================================
# Demo Setup Script
# ==============================================================================
# Handles first-run setup and subsequent starts of the demo environment.
#
# Features:
# - Detects first-run vs returning user
# - Auto-generates LibreChat secrets
# - Guides through ClickStack account/API key creation
# - Guides through Langfuse account creation
# - Auto-generates derived tokens (MCP auth)
# - Starts all services
#
# Usage: ./scripts/setup.sh [--skip-clickstack] [--skip-langfuse]
# ==============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Change to project root
cd "$(dirname "$0")/.."

echo ""
echo -e "${BLUE}=============================================="
echo "LLM Observability Demo - Setup"
echo -e "==============================================${NC}"
echo ""

# ------------------------------------------------------------------------------
# Parse Arguments
# ------------------------------------------------------------------------------
SKIP_CLICKSTACK=false
SKIP_LANGFUSE=false

for arg in "$@"; do
    case $arg in
        --skip-clickstack)
            SKIP_CLICKSTACK=true
            ;;
        --skip-langfuse)
            SKIP_LANGFUSE=true
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Check Prerequisites
# ------------------------------------------------------------------------------
echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    exit 1
fi

if ! command -v docker compose &> /dev/null; then
    echo -e "${RED}Error: Docker Compose is not installed${NC}"
    exit 1
fi

if ! command -v openssl &> /dev/null; then
    echo -e "${RED}Error: openssl is not installed (needed for secret generation)${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Docker, Docker Compose, and openssl are available"
echo ""

# ------------------------------------------------------------------------------
# Check/Create .env file
# ------------------------------------------------------------------------------
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}No .env file found. Creating from template...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✓${NC} Created .env from .env.example"
    echo ""
    echo -e "${YELLOW}ACTION REQUIRED:${NC}"
    echo "  Edit .env and add your ANTHROPIC_API_KEY"
    echo ""
    read -p "Press Enter after adding your API key..."
fi

# Source environment
set -a
source .env
set +a

# ------------------------------------------------------------------------------
# Check Required Variables
# ------------------------------------------------------------------------------
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${RED}Error: ANTHROPIC_API_KEY is not set in .env${NC}"
    echo "Get your API key from: https://console.anthropic.com/"
    exit 1
fi
echo -e "${GREEN}✓${NC} ANTHROPIC_API_KEY is configured"

# ------------------------------------------------------------------------------
# Generate LibreChat Secrets (if missing)
# ------------------------------------------------------------------------------
echo ""
echo "Checking LibreChat secrets..."

generate_secret() {
    openssl rand -hex 32
}

SECRETS_ADDED=false

if [ -z "$CREDS_KEY" ]; then
    CREDS_KEY=$(generate_secret)
    echo "CREDS_KEY=$CREDS_KEY" >> .env
    echo -e "${GREEN}✓${NC} Generated CREDS_KEY"
    SECRETS_ADDED=true
else
    echo -e "${GREEN}✓${NC} CREDS_KEY already configured"
fi

if [ -z "$CREDS_IV" ]; then
    CREDS_IV=$(openssl rand -hex 16)
    echo "CREDS_IV=$CREDS_IV" >> .env
    echo -e "${GREEN}✓${NC} Generated CREDS_IV"
    SECRETS_ADDED=true
else
    echo -e "${GREEN}✓${NC} CREDS_IV already configured"
fi

if [ -z "$JWT_SECRET" ]; then
    JWT_SECRET=$(generate_secret)
    echo "JWT_SECRET=$JWT_SECRET" >> .env
    echo -e "${GREEN}✓${NC} Generated JWT_SECRET"
    SECRETS_ADDED=true
else
    echo -e "${GREEN}✓${NC} JWT_SECRET already configured"
fi

if [ -z "$JWT_REFRESH_SECRET" ]; then
    JWT_REFRESH_SECRET=$(generate_secret)
    echo "JWT_REFRESH_SECRET=$JWT_REFRESH_SECRET" >> .env
    echo -e "${GREEN}✓${NC} Generated JWT_REFRESH_SECRET"
    SECRETS_ADDED=true
else
    echo -e "${GREEN}✓${NC} JWT_REFRESH_SECRET already configured"
fi

if [ "$SECRETS_ADDED" = true ]; then
    # Re-source environment to pick up new secrets
    set -a
    source .env
    set +a
fi

# ------------------------------------------------------------------------------
# Handle ClickStack Setup
# ------------------------------------------------------------------------------
if [ "$SKIP_CLICKSTACK" = true ]; then
    echo ""
    echo -e "${YELLOW}Skipping ClickStack setup (--skip-clickstack flag)${NC}"
else
    echo ""
    if [ -z "$CLICKSTACK_API_KEY" ]; then
        echo -e "${YELLOW}=============================================="
        echo "ClickStack Setup Required"
        echo -e "==============================================${NC}"
        echo ""
        echo "ClickStack API key not found. Starting ClickStack for initial setup..."
        echo ""

        # Start only ClickStack services
        docker compose --profile clickstack up -d clickstack-clickhouse clickstack-mongo clickstack-otel-collector clickstack

        echo ""
        echo "Waiting for ClickStack to be ready..."
        ATTEMPTS=0
        MAX_ATTEMPTS=60
        until curl -s http://localhost:8080 > /dev/null 2>&1; do
            sleep 2
            ATTEMPTS=$((ATTEMPTS + 1))
            if [ $ATTEMPTS -ge $MAX_ATTEMPTS ]; then
                echo -e "${RED}ClickStack did not start within 2 minutes${NC}"
                echo "Check logs with: docker compose --profile clickstack logs clickstack"
                exit 1
            fi
            echo -n "."
        done
        echo ""
        echo -e "${GREEN}✓${NC} ClickStack is ready!"
        echo ""

        echo -e "${BLUE}=============================================="
        echo "ACTION REQUIRED: Get ClickStack API Key"
        echo -e "==============================================${NC}"
        echo ""
        echo "1. Open ${GREEN}http://localhost:8080${NC} in your browser"
        echo "2. Create an account (or log in)"
        echo "3. Go to ${GREEN}Team Settings${NC} (gear icon)"
        echo "4. Copy your API Key"
        echo ""
        echo "Then enter it below:"
        echo ""

        read -p "CLICKSTACK_API_KEY: " INPUT_API_KEY

        if [ -n "$INPUT_API_KEY" ]; then
            # Add to .env
            echo "" >> .env
            echo "# ClickStack API Key (added by setup script)" >> .env
            echo "CLICKSTACK_API_KEY=$INPUT_API_KEY" >> .env

            echo ""
            echo -e "${GREEN}✓${NC} ClickStack API key saved to .env"

            # Re-source environment
            export CLICKSTACK_API_KEY="$INPUT_API_KEY"
        else
            echo -e "${YELLOW}Skipping ClickStack configuration (key not provided)${NC}"
        fi
    else
        echo -e "${GREEN}✓${NC} ClickStack API key found in .env"
    fi
fi

# ------------------------------------------------------------------------------
# Handle Langfuse Setup
# ------------------------------------------------------------------------------
if [ "$SKIP_LANGFUSE" = true ]; then
    echo ""
    echo -e "${YELLOW}Skipping Langfuse setup (--skip-langfuse flag)${NC}"
else
    echo ""
    if [ -z "$LANGFUSE_PUBLIC_KEY" ] || [ -z "$LANGFUSE_SECRET_KEY" ]; then
        echo -e "${YELLOW}=============================================="
        echo "Langfuse Setup Required"
        echo -e "==============================================${NC}"
        echo ""
        echo "Langfuse API keys not found. Starting Langfuse for initial setup..."
        echo ""

        # Start only Langfuse services
        docker compose --profile langfuse up -d langfuse-postgres langfuse-redis langfuse-minio langfuse-minio-init langfuse-clickhouse langfuse-worker langfuse-web

        echo ""
        echo "Waiting for Langfuse to be ready..."
        ATTEMPTS=0
        MAX_ATTEMPTS=60
        until curl -s http://localhost:3001 > /dev/null 2>&1; do
            sleep 2
            ATTEMPTS=$((ATTEMPTS + 1))
            if [ $ATTEMPTS -ge $MAX_ATTEMPTS ]; then
                echo -e "${RED}Langfuse did not start within 2 minutes${NC}"
                echo "Check logs with: docker compose --profile langfuse logs langfuse-web"
                exit 1
            fi
            echo -n "."
        done
        echo ""
        echo -e "${GREEN}✓${NC} Langfuse is ready!"
        echo ""

        echo -e "${BLUE}=============================================="
        echo "ACTION REQUIRED: Create Langfuse Account"
        echo -e "==============================================${NC}"
        echo ""
        echo "1. Open ${GREEN}http://localhost:3001${NC} in your browser"
        echo "2. Sign up for an account"
        echo "3. Create a project (or use the default)"
        echo "4. Go to ${GREEN}Settings → API Keys${NC}"
        echo "5. Copy your Public Key and Secret Key"
        echo ""
        echo "Then enter them below:"
        echo ""

        read -p "LANGFUSE_PUBLIC_KEY (pk-lf-...): " INPUT_PUBLIC_KEY
        read -p "LANGFUSE_SECRET_KEY (sk-lf-...): " INPUT_SECRET_KEY

        if [ -n "$INPUT_PUBLIC_KEY" ] && [ -n "$INPUT_SECRET_KEY" ]; then
            # Add to .env
            echo "" >> .env
            echo "# Langfuse API Keys (added by setup script)" >> .env
            echo "LANGFUSE_PUBLIC_KEY=$INPUT_PUBLIC_KEY" >> .env
            echo "LANGFUSE_SECRET_KEY=$INPUT_SECRET_KEY" >> .env

            # Generate MCP auth token
            MCP_TOKEN=$(echo -n "$INPUT_PUBLIC_KEY:$INPUT_SECRET_KEY" | base64)
            echo "LANGFUSE_MCP_AUTH_TOKEN=$MCP_TOKEN" >> .env

            echo ""
            echo -e "${GREEN}✓${NC} Langfuse keys saved to .env"
            echo -e "${GREEN}✓${NC} MCP auth token generated"

            # Re-source environment
            export LANGFUSE_PUBLIC_KEY="$INPUT_PUBLIC_KEY"
            export LANGFUSE_SECRET_KEY="$INPUT_SECRET_KEY"
            export LANGFUSE_MCP_AUTH_TOKEN="$MCP_TOKEN"
        else
            echo -e "${YELLOW}Skipping Langfuse configuration (keys not provided)${NC}"
        fi
    else
        echo -e "${GREEN}✓${NC} Langfuse keys found in .env"

        # Generate MCP token if missing
        if [ -z "$LANGFUSE_MCP_AUTH_TOKEN" ]; then
            MCP_TOKEN=$(echo -n "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64)
            echo "LANGFUSE_MCP_AUTH_TOKEN=$MCP_TOKEN" >> .env
            echo -e "${GREEN}✓${NC} Generated missing MCP auth token"
        else
            echo -e "${GREEN}✓${NC} MCP auth token configured"
        fi
    fi
fi

# ------------------------------------------------------------------------------
# Start Services
# ------------------------------------------------------------------------------
echo ""
echo -e "${BLUE}Starting all services...${NC}"
echo ""

# Build the docker compose command with appropriate profiles
PROFILES=""
if [ "$SKIP_CLICKSTACK" = false ]; then
    PROFILES="$PROFILES --profile clickstack"
fi
if [ "$SKIP_LANGFUSE" = false ]; then
    PROFILES="$PROFILES --profile langfuse"
fi

docker compose $PROFILES up -d

echo ""
echo -e "${GREEN}=============================================="
echo "Setup Complete!"
echo -e "==============================================${NC}"
echo ""
echo "Services are starting. Access them at:"
echo ""
echo "  LibreChat:    ${GREEN}http://localhost:3080${NC}"
if [ "$SKIP_CLICKSTACK" = false ]; then
echo "  ClickStack:   ${GREEN}http://localhost:8080${NC}"
fi
if [ "$SKIP_LANGFUSE" = false ]; then
echo "  Langfuse:     ${GREEN}http://localhost:3001${NC}"
fi
echo "  Text-to-SQL:  ${GREEN}http://localhost:8002${NC}"
echo "  Vector RAG:   ${GREEN}http://localhost:8003${NC}"
echo ""
echo "Next steps:"
echo "  1. Wait ~30 seconds for services to fully start"
echo "  2. Run ${GREEN}./scripts/seed-demo-data.sh${NC} to populate sample data"
echo "  3. Open the URLs above to explore!"
echo ""
