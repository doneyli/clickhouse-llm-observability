"""
Give the self-hosted `annuities-prod` project its own API keys (.env.selfhosted-annuities) so the retirement /
annuities use case writes to its OWN project — the annuities team sees it; the claims team does not.

Self-hosted has no org API key, so the key pair is inserted the way Langfuse stores it: the fast hash is
sha256(secret + hex(sha256(SALT))). Local demo instance only; in production keys come from the UI or the
Enterprise organization API and live in a secrets manager.

    .venv/bin/python scripts/seed_selfhosted_annuities.py
"""

from __future__ import annotations

import hashlib
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values

DEMO = Path(__file__).resolve().parent.parent
ENV = dotenv_values(DEMO / ".env")
OUT = DEMO / ".env.selfhosted-annuities"
PROJECT = "annuities-prod"


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", "claims-postgres-1", "psql", "-U", "postgres", "-t", "-A", "-c", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    if OUT.exists():
        print(f"= {OUT.name} exists")
        return 0
    pk, sk = f"pk-lf-annuities-{secrets.token_hex(6)}", f"sk-lf-annuities-{secrets.token_hex(16)}"
    fast = hashlib.sha256((sk + hashlib.sha256(ENV["SALT"].encode()).hexdigest()).encode()).hexdigest()
    psql(f"""insert into api_keys (id, created_at, note, public_key, hashed_secret_key, display_secret_key, project_id,
             fast_hashed_secret_key, scope, is_in_app_agent_key)
             values ('{uuid.uuid4()}', now(), 'annuities demo (seed_selfhosted_annuities.py)', '{pk}',
                     '{secrets.token_hex(24)}', 'sk-lf-...{sk[-4:]}', '{PROJECT}', '{fast}', 'PROJECT', false)""")
    r = httpx.get("http://localhost:3200/api/public/projects", auth=(pk, sk), timeout=20)
    if r.status_code != 200 or r.json()["data"][0]["id"] != PROJECT:
        print("! minted key does not authenticate:", r.status_code, r.text[:200])
        return 1
    OUT.write_text(f"# Self-hosted project {PROJECT} — written by scripts/seed_selfhosted_annuities.py\n"
                   f"LANGFUSE_BASE_URL=http://localhost:3200\nLANGFUSE_PROJECT_ID={PROJECT}\n"
                   f"LANGFUSE_PUBLIC_KEY={pk}\nLANGFUSE_SECRET_KEY={sk}\n")
    print(f"+ API key for {PROJECT} → {OUT.name} (verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
