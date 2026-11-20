"""
Self-hosted Enterprise governance state for the claims org on the local Langfuse — the instance
.env.selfhosted points at (`python scripts/langfuse_local.py provision insurance-claims`):

  projects   claims-prod (provisioned), claims-dev, annuities-prod — one project per environment and team
  users      test accounts on this local demo instance only, created through its own sign-up endpoint;
             roles then set in Postgres — the same records Settings → Members writes. In production the roles
             come from the identity provider (SSO group claims / SCIM), not from this script.
  roles      platform admin       org OWNER (this org only)
             claims engineer      org NONE · claims-dev MEMBER · claims-prod MEMBER (edits prompts; cannot move protected `production`)
             release manager      org NONE · claims-dev MEMBER · claims-prod ADMIN (may move `production`)
             annuities engineer   org NONE · annuities-prod MEMBER (sees nothing of claims)
             model risk / RAI ops org VIEWER (read-only, every project)
  prompts    `production` is a PROTECTED label on claims-prod: only ADMIN/OWNER may move it
  retention  claims-prod keeps 90 days of traces

    .venv/bin/python scripts/seed_selfhosted_rbac.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import dotenv_values

DEMO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO.parent.parent / "scripts"))
import langfuse_local  # noqa: E402

ENV = dotenv_values(DEMO / ".env")
PASSWORD = ENV["DEMO_USER_PASSWORD"]  # sign-up requires letters, digits and a special character
PROJECTS = [("claims-dev", "claims-dev"), ("annuities-prod", "annuities-prod")]


def users(prod: str) -> list:  # email, name, password, org role, {project: role}
    return [
        (ENV.get("LANGFUSE_INIT_USER_EMAIL") or "platform.admin@northwind-life.example", "Platform Admin",
         ENV.get("LANGFUSE_INIT_USER_PASSWORD") or PASSWORD, "OWNER", {}),
        ("claims.engineer@northwind-life.example", "Claims ML Engineer", PASSWORD, "NONE",
         {"claims-dev": "MEMBER", prod: "MEMBER"}),
        ("claims.release.manager@northwind-life.example", "Claims Release Manager", PASSWORD, "NONE",
         {"claims-dev": "MEMBER", prod: "ADMIN"}),
        ("annuities.engineer@northwind-life.example", "Annuities ML Engineer", PASSWORD, "NONE",
         {"annuities-prod": "MEMBER"}),
        ("model.risk@northwind-life.example", "Model Risk and Responsible AI", PASSWORD, "VIEWER", {}),
    ]


def main() -> int:
    project = langfuse_local.connect(DEMO / ".env.selfhosted")
    print(f"✓ {project.url} · org {project.org_id} · project {project.project_id}")
    for pid, name in PROJECTS:
        print(f"{'+' if project.add_project(pid, name) else '='} project {pid}")
    project.pg.query(f"update projects set retention_days = 90 where id = {langfuse_local.sql_str(project.project_id)}")
    print(f"✓ {project.project_id} retention: 90 days")
    for email, name, password, org_role, project_roles in users(project.project_id):
        if not project.add_user(email, name, password, org_role, project_roles):
            print(f"! {email}: sign-up failed (the password needs letters, digits and a special character)")
            continue
        print(f"✓ {email}: org {org_role}" + "".join(f", {p} {r}" for p, r in project_roles.items()))
    project.protect_label("production")
    print(f"✓ 'production' is a protected prompt label on {project.project_id}")
    print(project.members())
    return 0


if __name__ == "__main__":
    sys.exit(main())
