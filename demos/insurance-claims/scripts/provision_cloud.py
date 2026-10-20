"""
Create (or reuse) this demo's Langfuse Cloud project and write its keys to .env.cloud.

Needs an ORGANIZATION API key (Organization settings → API keys) in .env.cloud-org:
    LANGFUSE_ORG_PUBLIC_KEY, LANGFUSE_ORG_SECRET_KEY, LANGFUSE_BASE_URL
Both files are gitignored. Run once:  .venv/bin/python scripts/provision_cloud.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

DEMO = Path(__file__).resolve().parent.parent
PROJECT_NAME = "northwind-life-claims"
RETENTION_DAYS = 90


def main() -> int:
    org = {k: v for k, v in dotenv_values(DEMO / ".env.cloud-org").items() if v}
    base = org["LANGFUSE_BASE_URL"].rstrip("/")
    auth = "Basic " + base64.b64encode(
        f"{org['LANGFUSE_ORG_PUBLIC_KEY']}:{org['LANGFUSE_ORG_SECRET_KEY']}".encode()).decode()
    h = {"Authorization": auth}

    if (DEMO / ".env.cloud").exists():
        print(".env.cloud already exists — delete it to re-provision")
        return 0
    r = httpx.get(f"{base}/api/public/organizations/projects", headers=h, timeout=30)
    r.raise_for_status()
    existing = [p for p in r.json().get("projects", []) if p["name"] == PROJECT_NAME]
    if existing:
        project = existing[0]
        print(f"= project {PROJECT_NAME} ({project['id']})")
    else:
        r = httpx.post(f"{base}/api/public/projects", headers=h, timeout=30,
                       json={"name": PROJECT_NAME, "retention": RETENTION_DAYS,
                             "metadata": {"demo": "insurance-claims"}})
        r.raise_for_status()
        project = r.json()
        print(f"+ project {PROJECT_NAME} ({project['id']}), retention {RETENTION_DAYS} days")
    r = httpx.post(f"{base}/api/public/projects/{project['id']}/apiKeys", headers=h, timeout=30,
                   json={"note": "insurance-claims demo (scripts/provision_cloud.py)"})
    r.raise_for_status()
    keys = r.json()
    (DEMO / ".env.cloud").write_text(
        f"# Langfuse Cloud project '{PROJECT_NAME}' — created by scripts/provision_cloud.py\n"
        f"LANGFUSE_BASE_URL={base}\nLANGFUSE_PROJECT_ID={project['id']}\n"
        f"LANGFUSE_PUBLIC_KEY={keys['publicKey']}\nLANGFUSE_SECRET_KEY={keys['secretKey']}\n")
    print("+ project API key → .env.cloud")
    return 0


if __name__ == "__main__":
    sys.exit(main())
