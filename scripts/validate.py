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
    print("\nDeployment Validation\n" + "="*50)
    all_ok = True

    # Environment
    print("\nEnvironment:")
    all_ok &= check("ANTHROPIC_API_KEY", bool(os.getenv("ANTHROPIC_API_KEY")))

    # Services
    print("\nServices:")
    client = httpx.Client(timeout=5)

    services = [
        ("Langfuse", "http://localhost:3001"),
        ("LibreChat", "http://localhost:3080"),
        ("MCP Server", "http://localhost:8001"),
    ]

    for name, url in services:
        try:
            r = client.get(url)
            all_ok &= check(name, r.status_code in [200, 404, 405], f"Port {url.split(':')[-1]}")
        except Exception as e:
            all_ok &= check(name, False, str(e))

    print("\n" + "="*50)
    print("All checks passed!" if all_ok else "Some checks failed")
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
