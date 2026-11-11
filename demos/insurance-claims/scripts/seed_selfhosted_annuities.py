"""
Give the self-hosted `annuities-prod` project its own API keys (.env.selfhosted-annuities) so the retirement /
annuities use case writes to its OWN project — the annuities team sees it; the claims team does not.

The project lives in the claims org on the local Langfuse that .env.selfhosted points at. The key pair is
inserted the way Langfuse stores it (scripts/langfuse_local.py: fast hash = sha256(secret + hex(sha256(SALT)))).
Local demo instance only; in production keys come from the UI or the Enterprise organization API and live in a
secrets manager.

    .venv/bin/python scripts/seed_selfhosted_annuities.py
"""

from __future__ import annotations

import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO.parent.parent / "scripts"))
import langfuse_local  # noqa: E402

OUT = DEMO / ".env.selfhosted-annuities"
PROJECT = "annuities-prod"


def main() -> int:
    project = langfuse_local.connect(DEMO / ".env.selfhosted")
    current = langfuse_local.read_env(OUT)
    if (current.get("LANGFUSE_BASE_URL", "").rstrip("/") == project.url
            and langfuse_local.keys_work(project.url, current.get("LANGFUSE_PUBLIC_KEY", ""),
                                         current.get("LANGFUSE_SECRET_KEY", ""), PROJECT)):
        print(f"= {OUT.name} already points at {project.url} and its keys work")
        return 0
    if project.add_project(PROJECT, PROJECT):
        print(f"+ project {PROJECT} in org {project.org_id}")
    pk, sk = langfuse_local.mint_key(project.pg, project.salt, PROJECT, "annuities",
                                     "annuities demo (seed_selfhosted_annuities.py)")
    if not langfuse_local.keys_work(project.url, pk, sk, PROJECT):
        print(f"! minted a key for {PROJECT} but {project.url} rejects it")
        return 1
    OUT.write_text(f"# Self-hosted project {PROJECT} — written by scripts/seed_selfhosted_annuities.py\n"
                   f"LANGFUSE_BASE_URL={project.url}\nLANGFUSE_PROJECT_ID={PROJECT}\n"
                   f"LANGFUSE_PUBLIC_KEY={pk}\nLANGFUSE_SECRET_KEY={sk}\n")
    print(f"+ API key for {PROJECT} → {OUT.name} (verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
