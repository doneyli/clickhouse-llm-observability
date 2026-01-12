# Extending clickhouse-llm-observability with TruLens & OpenLLMetry

**Adding LLM Evaluation and Enhanced Instrumentation to the Existing Demo**

> **For Claude Code**: This spec EXTENDS an existing repository. Do NOT recreate files that already exist. Only add new files and modify existing ones where specified.

---

## 🎯 Objective

Extend the existing LibreChat + ClickStack demo to add:
1. **TruLens** - LLM quality evaluation (groundedness, relevance, coherence)
2. **OpenLLMetry** - Enhanced LLM tracing (prompts, completions, token counts)
3. **Python RAG Example** - Alternative to LibreChat for developers
4. **Dashboard Queries** - SQL queries for HyperDX analysis
5. **Utility Scripts** - Load generation and validation

---

## 📁 Existing Repository Structure

```
clickhouse-llm-observability/          # ✅ EXISTS
├── README.md                          # ✅ EXISTS - will UPDATE
├── LICENSE                            # ✅ EXISTS
├── .gitignore                         # ✅ EXISTS
├── .env.example                       # ✅ EXISTS - will UPDATE
├── docker-compose.yaml                # ✅ EXISTS - will UPDATE
├── Dockerfile.mcp                     # ✅ EXISTS
├── Dockerfile.otel                    # ✅ EXISTS
├── otel-file-collector.yaml           # ✅ EXISTS
├── librechat.yaml                     # ✅ EXISTS
└── client/                            # ✅ EXISTS
```

---

## 📁 New Files to Add

```
clickhouse-llm-observability/
├── # ... existing files above ...
│
├── Dockerfile.rag                     # 🆕 CREATE - Python RAG app
├── python-rag/                        # 🆕 CREATE - New directory
│   ├── __init__.py
│   ├── requirements.txt
│   ├── instrumentation.py             # OpenLLMetry setup
│   ├── rag_pipeline.py                # LangChain RAG
│   ├── mcp_client.py                  # MCP SSE client
│   ├── trulens_config.py              # TruLens feedback functions
│   └── main.py                        # Entry point
│
├── scripts/                           # 🆕 CREATE - New directory
│   ├── setup.sh                       # Setup helper
│   ├── validate.py                    # Deployment validation
│   └── generate_load.py               # Load testing
│
├── queries/                           # 🆕 CREATE - New directory
│   ├── token_usage.sql
│   ├── cost_estimation.sql
│   ├── latency_analysis.sql
│   ├── error_analysis.sql
│   └── mcp_analysis.sql
│
└── docs/                              # 🆕 CREATE - New directory
    ├── TRULENS_GUIDE.md
    ├── OPENLLMETRY_GUIDE.md
    └── SAMPLE_QUESTIONS.md
```

---

## 🔧 Files to Modify

### 1. UPDATE: .env.example

Add these new variables to the EXISTING file:

```bash
# =============================================================================
# NEW: Python RAG App Configuration
# =============================================================================
OPENAI_API_KEY=sk-your-openai-api-key-here

# LLM Settings
OPENAI_MODEL=gpt-4o
TEMPERATURE=0.7

# TruLens evaluation model (use smaller model for cost efficiency)
TRULENS_MODEL=gpt-4o-mini

# Python RAG App
RAG_APP_PORT=8002
RAG_APP_ENABLED=true
```

### 2. UPDATE: docker-compose.yaml

Add this new service to the EXISTING docker-compose.yaml:

```yaml
  # =============================================================================
  # NEW: Python RAG Application with TruLens + OpenLLMetry
  # =============================================================================
  python-rag:
    build:
      context: .
      dockerfile: Dockerfile.rag
    container_name: python-rag
    ports:
      - "${RAG_APP_PORT:-8002}:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - CLICKSTACK_API_KEY=${CLICKSTACK_API_KEY}
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318/v1/traces
      - MCP_SERVER_URL=http://mcp-clickhouse:8000/sse
      - OTEL_SERVICE_NAME=python-rag-demo
      - OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o}
      - TEMPERATURE=${TEMPERATURE:-0.7}
      - TRULENS_MODEL=${TRULENS_MODEL:-gpt-4o-mini}
    depends_on:
      - mcp-clickhouse
    extra_hosts:
      - "host.docker.internal:host-gateway"
    profiles:
      - rag  # Optional: only starts with --profile rag
    volumes:
      - ./python-rag:/app
```

### 3. UPDATE: README.md

Add this section to the EXISTING README:

```markdown
---

## 🆕 Python RAG Demo with TruLens & OpenLLMetry

In addition to LibreChat, this repo includes a Python RAG application that demonstrates:
- **OpenLLMetry** - Automatic capture of prompts, completions, and token usage
- **TruLens** - LLM quality evaluation (groundedness, relevance, coherence)

### Starting the Python RAG App

```bash
# Add your OpenAI API key to .env
echo "OPENAI_API_KEY=sk-..." >> .env

# Start with the rag profile
docker compose --profile rag up -d

# Or run standalone
docker compose up python-rag
```

### Using the Python RAG App

```bash
# Run demo queries
docker compose exec python-rag python main.py

# Interactive mode
docker compose exec python-rag python main.py --interactive

# Generate load for dashboards
docker compose exec python-rag python /scripts/generate_load.py -n 20
```

### Access Points

| Service | URL | Description |
|---------|-----|-------------|
| LibreChat | http://localhost:3080 | Chat UI with MCP |
| Python RAG | http://localhost:8002 | API + TruLens evals |
| HyperDX | http://localhost:8080 | Observability UI |

### Sample HyperDX Queries

See `queries/` directory for SQL queries to analyze:
- Token usage and costs
- Latency percentiles
- Error rates
- MCP server performance
- TruLens evaluation scores
```

---

## 🆕 New Files to Create

### Dockerfile.rag

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY python-rag/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY python-rag/ .
COPY scripts/ /scripts/

ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "main.py"]
```

### python-rag/requirements.txt

```text
# Core LLM frameworks
langchain>=0.3.0
langchain-openai>=0.2.0
langchain-community>=0.3.0

# OpenTelemetry - Core
opentelemetry-api>=1.25.0
opentelemetry-sdk>=1.25.0
opentelemetry-exporter-otlp-proto-http>=1.25.0

# OpenLLMetry - LLM-specific instrumentation
traceloop-sdk>=0.30.0
opentelemetry-instrumentation-langchain>=0.30.0

# TruLens - LLM evaluation
trulens-core>=1.0.0
trulens-providers-openai>=1.0.0

# MCP client & utilities
httpx>=0.27.0
python-dotenv>=1.0.0
uvicorn>=0.30.0
fastapi>=0.111.0
```

### python-rag/__init__.py

```python
"""Python RAG Demo with TruLens and OpenLLMetry"""
__version__ = "1.0.0"
```

### python-rag/instrumentation.py

```python
"""
OpenTelemetry + OpenLLMetry Instrumentation

IMPORTANT: This module must be imported BEFORE LangChain!
"""

import os
from opentelemetry import trace
from opentelemetry.sdk import trace as trace_sdk
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor


def setup_instrumentation():
    """Initialize OpenTelemetry with OpenLLMetry for LangChain."""
    
    service_name = os.getenv("OTEL_SERVICE_NAME", "python-rag-demo")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    api_key = os.getenv("CLICKSTACK_API_KEY", "")
    debug = os.getenv("DEBUG", "false").lower() == "true"
    
    # Create resource with service metadata
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })
    
    tracer_provider = trace_sdk.TracerProvider(resource=resource)
    
    # OTLP exporter for ClickStack
    if api_key:
        otlp_exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers={"authorization": api_key}
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(otlp_exporter, max_export_batch_size=512)
        )
        print(f"✅ OTLP export enabled: {otlp_endpoint}")
    else:
        print("⚠️  CLICKSTACK_API_KEY not set - traces will not be exported")
    
    # Console exporter for debugging
    if debug:
        tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        print("✅ Console span export enabled (DEBUG mode)")
    
    trace.set_tracer_provider(tracer_provider)
    
    # Enable OpenLLMetry auto-instrumentation for LangChain
    # This captures: gen_ai.prompt.*, gen_ai.completion.*, gen_ai.usage.*
    LangchainInstrumentor().instrument(tracer_provider=tracer_provider)
    
    print(f"✅ OpenLLMetry instrumentation enabled for: {service_name}")
    return tracer_provider


def get_tracer(name: str = "python-rag"):
    return trace.get_tracer(name)
```

### python-rag/rag_pipeline.py

```python
"""RAG Pipeline with ClickHouse MCP Integration"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser


@dataclass
class RAGConfig:
    model_name: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 2000


# Available databases at sql.clickhouse.com
CLICKHOUSE_DATABASES = """
Available databases include:
- uk_price_paid: UK property transactions
- github_events: GitHub activity data  
- opensky: Flight tracking data
- stackoverflow: Stack Overflow posts
- reddit: Reddit posts and comments
- hackernews: Hacker News stories
- wikistat: Wikipedia page views
- youtube: YouTube video metadata
- food_prices: Global food price indices
- nyc_taxi: NYC taxi trip data
- ontime: US flight delay data
- cell_towers: OpenCellID cell tower locations
- crypto_prices: Cryptocurrency prices
"""


class ClickHouseRAGPipeline:
    """RAG pipeline that queries ClickHouse via MCP."""
    
    def __init__(self, config: Optional[RAGConfig] = None):
        self.config = config or RAGConfig(
            model_name=os.getenv("OPENAI_MODEL", "gpt-4o"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
        )
        self._setup_llm()
        self._setup_chains()
        self._context = ""
    
    def _setup_llm(self):
        self.llm = ChatOpenAI(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            metadata={"component": "openai_chat", "purpose": "rag_generation"}
        )
    
    def _setup_chains(self):
        self.analysis_prompt = ChatPromptTemplate.from_template(
            f"You are a data analyst with access to ClickHouse at sql.clickhouse.com.\n\n"
            f"{CLICKHOUSE_DATABASES}\n\n"
            "Question: {question}\n\n"
            "Identify which database(s) and data would help answer this question."
        )
        
        self.analysis_chain = (
            self.analysis_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "query_analysis"}})
        
        self.response_prompt = ChatPromptTemplate.from_template(
            "Based on the analysis and context, answer the question.\n\n"
            "Question: {question}\n"
            "Analysis: {analysis}\n"
            "Context: {context}\n\n"
            "Provide a clear, data-driven response."
        )
        
        self.response_chain = (
            self.response_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "response_generation"}})
    
    def retrieve_context(self, question: str, analysis: str) -> str:
        """Retrieve context from ClickHouse via MCP."""
        try:
            from mcp_client import create_mcp_client
            mcp = create_mcp_client()
            self._context = mcp.get_context_for_question(question, analysis)
            return self._context
        except Exception as e:
            self._context = f"[MCP unavailable: {e}]"
            return self._context
    
    def query(self, question: str) -> str:
        """Execute the full RAG pipeline."""
        analysis = self.analysis_chain.invoke({"question": question})
        context = self.retrieve_context(question, analysis)
        answer = self.response_chain.invoke({
            "question": question,
            "analysis": analysis,
            "context": context
        })
        return answer
    
    @property
    def context(self) -> str:
        """Expose context for TruLens groundedness evaluation."""
        return self._context


def create_pipeline(config: Optional[RAGConfig] = None) -> ClickHouseRAGPipeline:
    return ClickHouseRAGPipeline(config)
```

### python-rag/mcp_client.py

```python
"""MCP Client for ClickHouse Server"""

import os
import json
import asyncio
from typing import Dict, Any, List
from dataclasses import dataclass
import httpx
from opentelemetry import trace

tracer = trace.get_tracer("mcp-client")


@dataclass 
class MCPConfig:
    server_url: str = "http://localhost:8001/sse"
    timeout: float = 30.0


class SyncClickHouseMCPClient:
    """Synchronous MCP client for ClickHouse."""
    
    def __init__(self, config: MCPConfig = None):
        self.config = config or MCPConfig(
            server_url=os.getenv("MCP_SERVER_URL", "http://mcp-clickhouse:8000/sse")
        )
    
    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call an MCP tool synchronously."""
        with tracer.start_as_current_span(f"mcp.{tool_name}") as span:
            span.set_attribute("mcp.tool", tool_name)
            
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                    "id": 1
                }
                
                with httpx.Client(timeout=self.config.timeout) as client:
                    # MCP servers typically have a /message endpoint for RPC
                    endpoint = self.config.server_url.replace("/sse", "/message")
                    response = client.post(endpoint, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    
                span.set_attribute("mcp.success", True)
                return result.get("result", {})
                
            except Exception as e:
                span.set_attribute("mcp.error", str(e))
                raise
    
    def list_databases(self) -> List[str]:
        """List available databases."""
        try:
            result = self._call_tool("list_databases", {})
            return result.get("databases", [])
        except Exception:
            # Fallback to known databases
            return ["uk_price_paid", "github_events", "opensky", "stackoverflow"]
    
    def execute_query(self, query: str) -> Dict[str, Any]:
        """Execute a SQL query."""
        with tracer.start_as_current_span("mcp.execute_query") as span:
            span.set_attribute("db.statement", query[:500])
            span.set_attribute("db.system", "clickhouse")
            return self._call_tool("run_select_query", {"query": query})
    
    def get_context_for_question(self, question: str, analysis: str) -> str:
        """Get context from ClickHouse for a question."""
        with tracer.start_as_current_span("mcp.get_context") as span:
            span.set_attribute("question", question[:200])
            
            try:
                databases = self.list_databases()
                context_parts = [
                    f"Available ClickHouse databases: {', '.join(databases[:10])}",
                    "",
                    "Connected to sql.clickhouse.com with 35+ demo datasets.",
                ]
                
                context = "\n".join(context_parts)
                span.set_attribute("context_length", len(context))
                return context
                
            except Exception as e:
                return f"[MCP error: {e}]"


def create_mcp_client() -> SyncClickHouseMCPClient:
    return SyncClickHouseMCPClient()
```

### python-rag/trulens_config.py

```python
"""TruLens Evaluation Configuration"""

import os
from typing import List, Optional
from trulens.core import Feedback
from trulens.providers.openai import OpenAI as TruLensOpenAI
from trulens.apps.app import instrument


class TruLensConfig:
    def __init__(
        self,
        app_name: str = "clickhouse-rag-demo",
        app_version: str = "1.0.0",
        model: str = None,
    ):
        self.app_name = app_name
        self.app_version = app_version
        self.model = model or os.getenv("TRULENS_MODEL", "gpt-4o-mini")


def create_feedback_functions(config: TruLensConfig = None) -> List[Feedback]:
    """
    Create TruLens feedback functions for RAG evaluation.
    
    These evaluate:
    - Answer Relevance: Does the answer address the question?
    - Groundedness: Is the answer supported by the context?
    - Coherence: Is the response well-structured?
    - Toxicity: Safety check (lower is better)
    """
    config = config or TruLensConfig()
    provider = TruLensOpenAI(model_engine=config.model)
    
    feedbacks = [
        # Answer Relevance (0-1, higher is better)
        Feedback(provider.relevance_with_cot_reasons, name="Answer Relevance")
            .on_input().on_output(),
        
        # Groundedness (0-1, higher is better)
        Feedback(provider.groundedness_measure_with_cot_reasons, name="Groundedness")
            .on("context").on_output(),
        
        # Coherence (0-1, higher is better)
        Feedback(provider.coherence_with_cot_reasons, name="Coherence")
            .on_output(),
        
        # Toxicity (0-1, LOWER is better)
        Feedback(provider.moderation_toxicity, name="Toxicity", higher_is_better=False)
            .on_output(),
    ]
    
    return feedbacks


class InstrumentedRAGPipeline:
    """
    RAG Pipeline wrapper with TruLens instrumentation.
    
    The @instrument decorator marks methods for TruLens tracking.
    """
    
    def __init__(self, base_pipeline, config: TruLensConfig = None):
        self.pipeline = base_pipeline
        self.config = config or TruLensConfig()
    
    @instrument
    def retrieve(self, question: str) -> str:
        """Retrieve context - tracked by TruLens."""
        analysis = self.pipeline.analysis_chain.invoke({"question": question})
        return self.pipeline.retrieve_context(question, analysis)
    
    @instrument
    def generate(self, question: str, context: str) -> str:
        """Generate response - tracked by TruLens."""
        return self.pipeline.response_chain.invoke({
            "question": question,
            "analysis": "",
            "context": context
        })
    
    @instrument
    def query(self, question: str) -> str:
        """Full RAG query - main TruLens entry point."""
        context = self.retrieve(question)
        return self.generate(question, context)
    
    @property
    def context(self) -> str:
        """Expose context for groundedness evaluation."""
        return self.pipeline.context
```

### python-rag/main.py

```python
"""
ClickHouse RAG Demo with TruLens & OpenLLMetry

Entry point for the Python RAG application.
"""

import os
import sys

# ============================================================
# CRITICAL: Setup instrumentation BEFORE importing LangChain!
# ============================================================
from instrumentation import setup_instrumentation
setup_instrumentation()

# Now safe to import LangChain and other modules
from rag_pipeline import create_pipeline
from trulens_config import TruLensConfig, create_feedback_functions, InstrumentedRAGPipeline
from trulens.core import TruSession
from trulens.apps.app import TruApp

# Demo questions covering different databases
DEMO_QUESTIONS = [
    "What are the most expensive areas for property in London?",
    "How has GitHub activity changed over the past year?",
    "What are the busiest airports based on flight data?",
    "What programming languages are most discussed on Stack Overflow?",
    "What are the trends in global food prices?",
]


def create_app():
    """Create the RAG application with full instrumentation."""
    
    # 1. Create base pipeline
    base_pipeline = create_pipeline()
    
    # 2. Wrap with TruLens instrumentation
    trulens_config = TruLensConfig()
    instrumented = InstrumentedRAGPipeline(base_pipeline, trulens_config)
    
    # 3. Create TruLens session and feedback functions
    session = TruSession()
    feedbacks = create_feedback_functions(trulens_config)
    
    # 4. Create TruApp wrapper
    tru_app = TruApp(
        instrumented,
        app_name=trulens_config.app_name,
        app_version=trulens_config.app_version,
        feedbacks=feedbacks
    )
    
    return instrumented, tru_app, session


def run_demo(pipeline, tru_app):
    """Run demo queries with evaluation."""
    
    print("\n" + "="*60)
    print("🚀 ClickHouse RAG Demo with TruLens & OpenLLMetry")
    print("="*60)
    
    for i, question in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[{i}/{len(DEMO_QUESTIONS)}] {question}")
        print("-"*50)
        
        with tru_app as recording:
            try:
                response = pipeline.query(question)
                print(f"📝 {response[:400]}...")
            except Exception as e:
                print(f"❌ Error: {e}")
        
        # Show evaluation results
        try:
            record = recording.get()
            if record and record.feedback_results:
                print("\n📊 Evaluations:")
                for name, result in record.feedback_results.items():
                    score = getattr(result, 'result', 'pending')
                    print(f"   • {name}: {score}")
        except Exception:
            pass
    
    print("\n" + "="*60)
    print("✅ Demo complete!")
    print("   View traces: http://localhost:8080 (HyperDX)")
    print("="*60 + "\n")


def run_interactive(pipeline, tru_app):
    """Interactive query mode."""
    
    print("\n🔮 Interactive Mode - Type 'quit' to exit\n")
    
    while True:
        try:
            question = input("❓ Question: ").strip()
            
            if question.lower() in ('quit', 'exit', 'q'):
                break
            if not question:
                continue
            
            with tru_app as recording:
                response = pipeline.query(question)
                print(f"\n📝 {response}\n")
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Error: {e}\n")


def main():
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║   ClickHouse RAG Demo with TruLens & OpenLLMetry          ║
    ║                                                           ║
    ║   • OpenLLMetry: Auto-captures prompts, tokens            ║
    ║   • TruLens: Evaluates groundedness, relevance            ║
    ║   • ClickStack: Unified observability in ClickHouse       ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    # Create app
    pipeline, tru_app, session = create_app()
    
    # Run mode
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        run_interactive(pipeline, tru_app)
    else:
        run_demo(pipeline, tru_app)


if __name__ == "__main__":
    main()
```

---

## 🆕 Utility Scripts

### scripts/setup.sh

```bash
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
```

### scripts/validate.py

```python
#!/usr/bin/env python3
"""Validate deployment is working correctly."""

import os
import sys
import httpx

def check(name, passed, msg=""):
    status = "✓" if passed else "✗"
    print(f"  {status} {name}" + (f" - {msg}" if msg else ""))
    return passed

def main():
    print("\n🔍 Deployment Validation\n" + "="*50)
    all_ok = True
    
    # Environment
    print("\n📋 Environment:")
    all_ok &= check("ANTHROPIC_API_KEY", bool(os.getenv("ANTHROPIC_API_KEY")))
    all_ok &= check("OPENAI_API_KEY", bool(os.getenv("OPENAI_API_KEY", "").startswith("sk-")))
    all_ok &= check("CLICKSTACK_API_KEY", bool(os.getenv("CLICKSTACK_API_KEY")))
    
    # Services
    print("\n🌐 Services:")
    client = httpx.Client(timeout=5)
    
    services = [
        ("ClickStack", "http://localhost:8080"),
        ("LibreChat", "http://localhost:3080"),
        ("MCP Server", "http://localhost:8001"),
    ]
    
    for name, url in services:
        try:
            r = client.get(url)
            all_ok &= check(name, r.status_code in [200, 404, 405], f"Port {url.split(':')[-1]}")
        except Exception as e:
            all_ok &= check(name, False, str(e))
    
    # Python RAG (optional)
    try:
        r = client.get("http://localhost:8002/health")
        check("Python RAG", r.status_code == 200, "Port 8002")
    except Exception:
        check("Python RAG", False, "Not running (optional)")
    
    print("\n" + "="*50)
    print("✅ All checks passed!" if all_ok else "⚠️  Some checks failed")
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
```

### scripts/generate_load.py

```python
#!/usr/bin/env python3
"""Generate load to populate HyperDX dashboards."""

import sys
import os
import random
import argparse

# Add python-rag to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python-rag'))

QUESTIONS = [
    "What are the most expensive areas for property in London?",
    "How has GitHub activity changed over the past year?",
    "What are the busiest airports based on flight data?",
    "What programming languages are most discussed on Stack Overflow?",
    "What are the trends in global food prices?",
    "Which UK cities have the highest property price growth?",
    "What time of day are most GitHub commits made?",
    "What are the most delayed flight routes in the US?",
    "How long does it take for Stack Overflow questions to get answered?",
    "Which cryptocurrency has been most volatile?",
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--num-queries", type=int, default=10)
    args = parser.parse_args()
    
    from main import create_app
    pipeline, tru_app, _ = create_app()
    
    print(f"\n🚀 Generating {args.num_queries} queries...\n")
    
    for i in range(args.num_queries):
        q = random.choice(QUESTIONS)
        print(f"[{i+1}/{args.num_queries}] {q[:50]}...")
        
        with tru_app:
            try:
                pipeline.query(q)
                print("  ✓ Complete")
            except Exception as e:
                print(f"  ✗ {e}")
    
    print(f"\n✅ Done! View in HyperDX: http://localhost:8080\n")

if __name__ == "__main__":
    main()
```

---

## 🆕 SQL Queries for HyperDX

### queries/token_usage.sql

```sql
-- Token usage by service and operation
SELECT
    ServiceName,
    SpanAttributes['traceloop.association.properties.purpose'] AS operation,
    count() AS requests,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.prompt_tokens'])) AS input_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.completion_tokens'])) AS output_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.total_tokens'])) AS total_tokens
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
  AND SpanAttributes['gen_ai.system'] != ''
GROUP BY ServiceName, operation
ORDER BY total_tokens DESC
```

### queries/cost_estimation.sql

```sql
-- Daily cost estimation (GPT-4o pricing)
SELECT
    toDate(Timestamp) AS date,
    ServiceName,
    count() AS requests,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.prompt_tokens'])) AS input_tokens,
    sum(toUInt32OrZero(SpanAttributes['gen_ai.usage.completion_tokens'])) AS output_tokens,
    -- GPT-4o: $2.50/1M input, $10/1M output
    round((input_tokens * 2.50 + output_tokens * 10.0) / 1000000, 4) AS estimated_cost_usd
FROM otel_traces
WHERE SpanAttributes['gen_ai.system'] = 'openai'
GROUP BY date, ServiceName
ORDER BY date DESC
```

### queries/latency_analysis.sql

```sql
-- Latency percentiles by operation
SELECT
    ServiceName,
    SpanName,
    count() AS requests,
    round(quantile(0.50)(Duration / 1e6), 2) AS p50_ms,
    round(quantile(0.95)(Duration / 1e6), 2) AS p95_ms,
    round(quantile(0.99)(Duration / 1e6), 2) AS p99_ms,
    round(max(Duration / 1e6), 2) AS max_ms
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 1 HOUR
GROUP BY ServiceName, SpanName
ORDER BY p50_ms DESC
LIMIT 20
```

### queries/error_analysis.sql

```sql
-- Error rate and details
SELECT
    toStartOfHour(Timestamp) AS hour,
    ServiceName,
    countIf(StatusCode = 'OK') AS success,
    countIf(StatusCode = 'ERROR') AS errors,
    round(errors / (success + errors) * 100, 2) AS error_rate_pct
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 24 HOUR
GROUP BY hour, ServiceName
HAVING errors > 0
ORDER BY hour DESC
```

### queries/mcp_analysis.sql

```sql
-- MCP Server tool usage
SELECT
    SpanName,
    count() AS calls,
    round(avg(Duration / 1e6), 2) AS avg_ms,
    countIf(StatusCode = 'ERROR') AS errors
FROM otel_traces
WHERE ServiceName = 'mcp-clickhouse'
  AND Timestamp > now() - INTERVAL 1 HOUR
GROUP BY SpanName
ORDER BY calls DESC
```

---

## 📋 Implementation Checklist for Claude Code

```
EXISTING FILES (do not recreate):
✅ .gitignore
✅ LICENSE  
✅ docker-compose.yaml (UPDATE only - add python-rag service)
✅ .env.example (UPDATE only - add new variables)
✅ README.md (UPDATE only - add Python RAG section)
✅ Dockerfile.mcp
✅ Dockerfile.otel
✅ otel-file-collector.yaml
✅ librechat.yaml
✅ client/

NEW FILES TO CREATE:
□ Dockerfile.rag
□ python-rag/__init__.py
□ python-rag/requirements.txt
□ python-rag/instrumentation.py
□ python-rag/rag_pipeline.py
□ python-rag/mcp_client.py
□ python-rag/trulens_config.py
□ python-rag/main.py
□ scripts/setup.sh
□ scripts/validate.py
□ scripts/generate_load.py
□ queries/token_usage.sql
□ queries/cost_estimation.sql
□ queries/latency_analysis.sql
□ queries/error_analysis.sql
□ queries/mcp_analysis.sql
□ docs/TRULENS_GUIDE.md (optional)
□ docs/SAMPLE_QUESTIONS.md (optional)
```

---

## 🧪 Testing Commands

```bash
# 1. Start everything including Python RAG
docker compose --profile rag up -d

# 2. Validate deployment
python scripts/validate.py

# 3. Run Python RAG demo
docker compose exec python-rag python main.py

# 4. Interactive mode
docker compose exec python-rag python main.py --interactive

# 5. Generate load for dashboards
docker compose exec python-rag python /scripts/generate_load.py -n 20

# 6. View in HyperDX
open http://localhost:8080
```
