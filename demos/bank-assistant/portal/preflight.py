"""
Pre-flight check for the Northwind Bank demo — run from the presenter console
(act 0) or by hand:  .venv/bin/python portal/preflight.py

Read-only. Never prints keys — only whether they are present.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))

import httpx  # noqa: E402

from northwind import config  # noqa: E402

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
problems = 0


def line(status: str, what: str, detail: str = "") -> None:
    global problems
    if status == FAIL:
        problems += 1
    print(f"[{status}] {what}" + (f" — {detail}" if detail else ""), flush=True)


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def http_ok(url: str) -> tuple[bool, str]:
    try:
        r = httpx.get(url, timeout=4, follow_redirects=True)
        return r.status_code < 500, f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


print("Northwind Bank demo — pre-flight", flush=True)
print(f"profile={config.PROFILE}  environment={config.ENVIRONMENT}  release={config.RELEASE}", flush=True)
print(f"Langfuse target: {config.LANGFUSE_BASE_URL}", flush=True)
print("", flush=True)

# 1. Core-banking MCP server
mcp = urlparse(config.MCP_URL)
if port_open(mcp.hostname or "127.0.0.1", mcp.port or 8765):
    line(OK, "Core-banking MCP server", config.MCP_URL)
else:
    line(FAIL, "Core-banking MCP server", f"nothing listening at {config.MCP_URL} — run scripts/run_portal.sh")

# 2. Langfuse auth + project
t0 = time.time()
try:
    projects = config.api("GET", "/api/public/projects", timeout=10)["data"]
    p = projects[0]
    line(OK, "Langfuse API keys", f"project '{p.get('name')}' ({p.get('id')}) in {time.time() - t0:.1f}s")
    print(f"        {config.LANGFUSE_BASE_URL}/project/{p.get('id')}", flush=True)
except Exception as exc:  # noqa: BLE001
    line(FAIL, "Langfuse API keys", str(exc).splitlines()[0][:160])

# 3. Prompt labels
try:
    lf = config.get_langfuse()
    for label in ("production", "staging", "development"):
        try:
            pr = lf.get_prompt("northwind-assistant-system", label=label, cache_ttl_seconds=0,
                               max_retries=0, fetch_timeout_seconds=8)
            line(OK, f"Prompt label '{label}'", f"northwind-assistant-system v{pr.version}")
        except Exception as exc:  # noqa: BLE001
            line(WARN if label != "production" else FAIL, f"Prompt label '{label}'",
                 str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__)
except Exception as exc:  # noqa: BLE001
    line(FAIL, "Langfuse client", f"{type(exc).__name__}: {exc}"[:160])

# 4. Datasets (for the experiment acts)
try:
    ds = config.api("GET", "/api/public/v2/datasets", timeout=10, params={"limit": 50})["data"]
    names = ", ".join(d["name"] for d in ds) or "none"
    line(OK if ds else WARN, "Datasets", names)
except Exception as exc:  # noqa: BLE001
    line(WARN, "Datasets", str(exc).splitlines()[0][:120])

# 5. Model keys (presence only)
line(OK if config.ANTHROPIC_API_KEY else FAIL, "Anthropic API key", "present" if config.ANTHROPIC_API_KEY else "missing")
line(OK if config.OPENAI_API_KEY else WARN, "OpenAI API key",
     "present" if config.OPENAI_API_KEY else "missing — model comparison / voice may fail")
print(f"        agent model: {config.AGENT_MODEL} · judge model: {config.JUDGE_MODEL}", flush=True)

# 6. Local platform
for name, url in (("Jaeger UI (APM stand-in)", "http://localhost:16686"),
                  ("n8n", "http://localhost:5678"),
                  ("Self-hosted Langfuse", "http://localhost:3100/api/public/health")):
    ok, detail = http_ok(url)
    line(OK if ok else WARN, name, f"{url} {detail}")
apm = urlparse(config.APM_OTLP_ENDPOINT or "")
if apm.hostname:
    line(OK if port_open(apm.hostname, apm.port or 4318) else WARN, "APM OTLP endpoint", config.APM_OTLP_ENDPOINT)

# 7. Voice
voice_dir = DEMO_DIR / "data" / "voice"
calls = sorted(p.name for p in voice_dir.glob("*") if p.suffix.lower() in (".mp3", ".wav")) if voice_dir.is_dir() else []
line(OK if calls else WARN, "Voice sample calls", f"{len(calls)} in data/voice" + (f": {', '.join(calls)}" if calls else ""))
line(OK if (DEMO_DIR / "northwind" / "voice.py").exists() else WARN, "Voice module", "northwind/voice.py")

# 8. Demo-act scripts
for script in ("generate_traffic.py", "run_voice_calls.py", "run_n8n_samples.py", "run_experiment.py",
               "prompt_gate.py", "prompt_label.py"):
    present = (DEMO_DIR / "scripts" / script).is_file()
    line(OK if present else WARN, f"scripts/{script}", "present" if present else "not available yet")

print("", flush=True)
print("READY" if problems == 0 else f"{problems} blocking problem(s) — fix before the demo", flush=True)
sys.exit(0 if problems == 0 else 1)
