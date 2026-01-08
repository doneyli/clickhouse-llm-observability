#!/bin/bash
set -e

echo "🚀 ClickHouse LLM Observability - Extended Setup"
echo "================================================="

# Check .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✓ Created .env from template"
fi

source .env 2>/dev/null || true

# Validate keys
[ -z "$ANTHROPIC_API_KEY" ] && echo "⚠️  ANTHROPIC_API_KEY not set (needed for LibreChat)"
[ -z "$OPENAI_API_KEY" ] && echo "⚠️  OPENAI_API_KEY not set (needed for Python RAG)"
[ -z "$CLICKSTACK_API_KEY" ] && echo "⚠️  CLICKSTACK_API_KEY not set (get from http://localhost:8080)"

echo ""
echo "Starting services..."

# Start ClickStack first if not running
if ! curl -s http://localhost:8080 > /dev/null 2>&1; then
    echo "Starting ClickStack..."
    docker run -d --name clickstack \
        -p 8080:8080 -p 4317:4317 -p 4318:4318 \
        docker.hyperdx.io/hyperdx/hyperdx-all-in-one

    echo "Waiting for ClickStack..."
    until curl -s http://localhost:8080 > /dev/null 2>&1; do sleep 2; done
    echo "✓ ClickStack ready"
fi

# Start main services
docker compose up -d

# Optionally start Python RAG
if [ "$RAG_APP_ENABLED" = "true" ] || [ "$1" = "--with-rag" ]; then
    echo "Starting Python RAG app..."
    docker compose --profile rag up -d
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Access points:"
echo "  • LibreChat:  http://localhost:3080"
echo "  • HyperDX:    http://localhost:8080"
[ "$RAG_APP_ENABLED" = "true" ] && echo "  • Python RAG: http://localhost:8002"
echo ""
