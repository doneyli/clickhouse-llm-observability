"""
Create (or reuse) this demo's Langfuse Cloud project and write its keys to .env.

Needs an ORGANIZATION API key (Organization settings → API keys) in .env.cloud-org:
    LANGFUSE_ORG_PUBLIC_KEY, LANGFUSE_ORG_SECRET_KEY, LANGFUSE_BASE_URL
Both files are gitignored. ANTHROPIC_API_KEY is copied from the environment if set.

    .venv/bin/python scripts/provision_cloud.py [--project-name NAME]
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

DEMO = Path(__file__).resolve().parent.parent
RETENTION_DAYS = 90


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-name", default="marketing-agent-debugging")
    args = ap.parse_args()
    env_path = DEMO / ".env"
    if env_path.exists() and "LANGFUSE_SECRET_KEY" in env_path.read_text():
        print(".env already has project keys — delete them to re-provision")
        return 0
    org = {k: v for k, v in dotenv_values(DEMO / ".env.cloud-org").items() if v}
    base = org["LANGFUSE_BASE_URL"].rstrip("/")
    auth = "Basic " + base64.b64encode(
        f"{org['LANGFUSE_ORG_PUBLIC_KEY']}:{org['LANGFUSE_ORG_SECRET_KEY']}".encode()).decode()
    h = {"Authorization": auth}

    r = httpx.get(f"{base}/api/public/organizations/projects", headers=h, timeout=30)
    r.raise_for_status()
    existing = [p for p in r.json().get("projects", []) if p["name"] == args.project_name]
    if existing:
        project = existing[0]
        print(f"= project {args.project_name} ({project['id']})")
    else:
        r = httpx.post(f"{base}/api/public/projects", headers=h, timeout=30,
                       json={"name": args.project_name, "retention": RETENTION_DAYS,
                             "metadata": {"demo": "marketing-agent-debugging"}})
        r.raise_for_status()
        project = r.json()
        print(f"+ project {args.project_name} ({project['id']}), retention {RETENTION_DAYS} days")
    r = httpx.post(f"{base}/api/public/projects/{project['id']}/apiKeys", headers=h, timeout=30,
                   json={"note": "marketing-agent-debugging demo (scripts/provision_cloud.py)"})
    r.raise_for_status()
    keys = r.json()
    lines = [f"# Langfuse Cloud project '{args.project_name}' — created by scripts/provision_cloud.py",
             f"LANGFUSE_BASE_URL={base}", f"LANGFUSE_PROJECT_ID={project['id']}",
             f"LANGFUSE_PROJECT_NAME={args.project_name}",
             f"LANGFUSE_PUBLIC_KEY={keys['publicKey']}", f"LANGFUSE_SECRET_KEY={keys['secretKey']}"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        lines.append(f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}")
    previous = env_path.read_text() if env_path.exists() else ""
    env_path.write_text("\n".join(lines) + "\n" + previous)
    print("+ project API key → .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
