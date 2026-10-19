"""
Self-hosted Enterprise governance state for the local instance (http://localhost:3200):

  projects   claims-prod (bootstrapped), claims-dev, annuities-prod — one project per environment and team
  users      test accounts on this local demo instance only, created through its own sign-up endpoint;
             roles then set in Postgres — the same records Settings → Members writes. In production the roles
             come from the identity provider (SSO group claims / SCIM), not from this script.
  roles      claims engineer      org NONE · claims-dev MEMBER · claims-prod MEMBER (edits prompts; cannot move protected `production`)
             release manager      org NONE · claims-dev MEMBER · claims-prod ADMIN (may move `production`)
             annuities engineer   org NONE · annuities-prod MEMBER (sees nothing of claims)
             model risk / RAI ops org VIEWER (read-only, every project)
  prompts    `production` is a PROTECTED label on claims-prod: only ADMIN/OWNER may move it
  retention  claims-prod keeps 90 days of traces

    .venv/bin/python scripts/seed_selfhosted_rbac.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

DEMO = Path(__file__).resolve().parent.parent
ENV = dotenv_values(DEMO / ".env")
BASE = "http://localhost:3200"
ORG = "northwind-life"
PASSWORD = ENV["DEMO_USER_PASSWORD"]  # sign-up requires letters, digits and a special character
PROJECTS = [("claims-dev", "claims-dev"), ("annuities-prod", "annuities-prod")]
USERS = [  # email, name, org role, {project: role}
    ("claims.engineer@northwind-life.example", "Claims ML Engineer", "NONE",
     {"claims-dev": "MEMBER", "claims-prod": "MEMBER"}),
    ("claims.release.manager@northwind-life.example", "Claims Release Manager", "NONE",
     {"claims-dev": "MEMBER", "claims-prod": "ADMIN"}),
    ("annuities.engineer@northwind-life.example", "Annuities ML Engineer", "NONE", {"annuities-prod": "MEMBER"}),
    ("model.risk@northwind-life.example", "Model Risk and Responsible AI", "VIEWER", {}),
]


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", "claims-postgres-1", "psql", "-U", "postgres", "-t", "-A", "-c", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    for pid, name in PROJECTS:
        psql(f"insert into projects (id, org_id, name, created_at, updated_at) values ('{pid}', '{ORG}', '{name}', "
             "now(), now()) on conflict (id) do nothing")
        print(f"✓ project {pid}")
    psql("update projects set retention_days = 90 where id = 'claims-prod'")
    print("✓ claims-prod retention: 90 days")
    for email, name, org_role, project_roles in USERS:
        r = httpx.post(f"{BASE}/api/auth/signup", json={"name": name, "email": email, "password": PASSWORD}, timeout=20)
        uid = psql(f"select id from users where email='{email}'")
        if not uid:
            print(f"! {email}: sign-up {r.status_code} {r.text[:120]}")
            continue
        psql(f"""insert into organization_memberships (id, org_id, user_id, role, created_at, updated_at)
                 values ('om-{uid}', '{ORG}', '{uid}', '{org_role}', now(), now())
                 on conflict (org_id, user_id) do update set role = excluded.role""")
        om = psql(f"select id from organization_memberships where org_id='{ORG}' and user_id='{uid}'")
        for pid, role in project_roles.items():
            psql(f"""insert into project_memberships (project_id, user_id, org_membership_id, role, created_at, updated_at)
                     values ('{pid}', '{uid}', '{om}', '{role}', now(), now())
                     on conflict (project_id, user_id) do update set role = excluded.role""")
        print(f"✓ {email}: org {org_role}" + "".join(f", {p} {r}" for p, r in project_roles.items()))
    psql("""insert into prompt_protected_labels (id, project_id, label, created_at, updated_at)
            values ('ppl-claims-prod-production', 'claims-prod', 'production', now(), now()) on conflict do nothing""")
    print("✓ 'production' is a protected prompt label on claims-prod")
    print(psql("select u.email || ' · ' || m.role from organization_memberships m join users u on u.id = m.user_id "
               f"where m.org_id = '{ORG}' order by 1"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
