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
