"""
Configuration and clients for the marketing agent debugging demo.

Key isolation first: this demo writes to its own Langfuse project. A shell that has
other LANGFUSE_* keys exported would silently send every trace to a different
project, and every "trace not found" afterwards would be a false lead. So we:
  1. load this folder's .env with override=True,
  2. hard-set the LANGFUSE_* variables the SDK reads (LANGFUSE_BASE_URL outranks a
     `host=` argument in SDK v4),
  3. pass the keys and base_url to Langfuse() explicitly,
  4. verify_project() checks the keys resolve to LANGFUSE_PROJECT_NAME.
"""

from __future__ import annotations

import base64
import os
import threading
from pathlib import Path

import httpx
from dotenv import load_dotenv

DEMO_DIR = Path(__file__).resolve().parent.parent
load_dotenv(DEMO_DIR / ".env", override=True)

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
                     or "https://us.cloud.langfuse.com").rstrip("/")
LANGFUSE_PROJECT_ID = os.environ.get("LANGFUSE_PROJECT_ID", "")
EXPECTED_PROJECT = os.environ.get("LANGFUSE_PROJECT_NAME", "marketing-agent-debugging")
ENVIRONMENT = os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "production")

for _k, _v in (("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY), ("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY),
               ("LANGFUSE_BASE_URL", LANGFUSE_BASE_URL), ("LANGFUSE_HOST", LANGFUSE_BASE_URL),
               ("LANGFUSE_TRACING_ENVIRONMENT", ENVIRONMENT)):
    os.environ[_k] = _v

AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-opus-5-5")
# Opus 5.5 defaults to effort=medium; set it explicitly so a model swap can't change it.
AGENT_EFFORT = os.environ.get("AGENT_EFFORT", "medium")
FACTCHECK_MODEL = os.environ.get("FACTCHECK_MODEL", AGENT_MODEL)
# Server-side refusal fallback (Claude API beta). "off" disables it.
AGENT_FALLBACKS = os.environ.get("AGENT_FALLBACKS", "default")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "10"))

# Stable release identifier of the agent harness, recorded as the trace `version`.
HARNESS_VERSION = "2026.11.0"

_lock = threading.Lock()
_langfuse = None
_anthropic = None


def get_langfuse():
    global _langfuse
    with _lock:
        if _langfuse is None:
            if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
                raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY missing — copy .env.example to "
                                 ".env or run scripts/provision_cloud.py")
            from langfuse import Langfuse
            from langfuse.span_filter import is_default_export_span
            # The Anthropic SDK emits its own GenAI span per call (scope com.anthropic.sdk.python).
            # Our plan-next-step / verify-claims generations already record each call with the
            # prompt link and the OpenAI-shaped I/O, so exporting both would count every model
            # call — and its cost — twice.
            _langfuse = Langfuse(public_key=LANGFUSE_PUBLIC_KEY, secret_key=LANGFUSE_SECRET_KEY,
                                 base_url=LANGFUSE_BASE_URL, environment=ENVIRONMENT,
                                 release=HARNESS_VERSION,
                                 should_export_span=lambda span: is_default_export_span(span) and not (
                                     span.instrumentation_scope is not None
                                     and span.instrumentation_scope.name.startswith("com.anthropic")))
        return _langfuse


def get_anthropic():
    global _anthropic
    with _lock:
        if _anthropic is None:
            import anthropic
            _anthropic = anthropic.Anthropic(max_retries=4, timeout=300)
        return _anthropic


def auth_header() -> dict:
    token = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def api(method: str, path: str, **kwargs) -> httpx.Response:
    """Plain REST call against this demo's project (for endpoints the SDK doesn't wrap)."""
    return httpx.request(method, f"{LANGFUSE_BASE_URL}/api/public{path}", headers=auth_header(),
                         timeout=60, **kwargs)


def verify_project() -> dict:
    """Fail loudly if the keys belong to a different project than the one we expect."""
    r = api("GET", "/projects")
    r.raise_for_status()
    projects = r.json().get("data", [])
    if not projects:
        raise SystemExit("Langfuse keys resolved to no project")
    project = projects[0]
    if project.get("name") != EXPECTED_PROJECT:
        raise SystemExit(f"Langfuse keys belong to project {project.get('name')!r}, expected "
                         f"{EXPECTED_PROJECT!r}. Fix .env (shell LANGFUSE_* exports are ignored).")
    global LANGFUSE_PROJECT_ID
    LANGFUSE_PROJECT_ID = project["id"]
    return project


def trace_url(trace_id: str) -> str:
    pid = LANGFUSE_PROJECT_ID or verify_project()["id"]
    return f"{LANGFUSE_BASE_URL}/project/{pid}/traces/{trace_id}"


def project_url(path: str = "") -> str:
    pid = LANGFUSE_PROJECT_ID or verify_project()["id"]
    return f"{LANGFUSE_BASE_URL}/project/{pid}{path}"
