"""
Langfuse Integration Configuration (v3 API)

Provides dual instrumentation alongside OpenLLMetry.
When LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set,
traces are sent to both ClickStack (via OTLP) and Langfuse.

Updated for Langfuse SDK v3 (OpenTelemetry-based API).
"""

import os
from typing import Optional

# Check if Langfuse is configured
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)


def is_langfuse_enabled() -> bool:
    """Check if Langfuse is configured and available."""
    return LANGFUSE_ENABLED


def get_langfuse_client():
    """
    Get Langfuse client for direct API access (v3 API).
    Returns None if Langfuse is not configured.
    """
    if not LANGFUSE_ENABLED:
        return None

    try:
        from langfuse import get_client
        client = get_client()
        print(f"Langfuse client initialized (v3): {os.getenv('LANGFUSE_HOST')}")
        return client
    except ImportError:
        print("Langfuse package not installed")
        return None
    except Exception as e:
        print(f"Failed to initialize Langfuse client: {e}")
        return None


def flush():
    """Flush any pending Langfuse events."""
    if not LANGFUSE_ENABLED:
        return

    try:
        from langfuse import get_client
        client = get_client()
        if client and hasattr(client, 'flush'):
            client.flush()
    except Exception as e:
        print(f"Failed to flush Langfuse: {e}")


def shutdown():
    """Shutdown the Langfuse client."""
    if not LANGFUSE_ENABLED:
        return

    try:
        from langfuse import get_client
        client = get_client()
        if client:
            if hasattr(client, 'flush'):
                client.flush()
            if hasattr(client, 'shutdown'):
                client.shutdown()
    except Exception as e:
        print(f"Failed to shutdown Langfuse: {e}")
