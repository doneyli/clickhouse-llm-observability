"""Self-hosted Enterprise demo state (ENT-01, ENT-02): RBAC users, a project-level
role override, and the PROTECTED `production` prompt label.

Users are created through the instance's own sign-up endpoint (test accounts on
this local demo instance only); roles are then set in Postgres — the same
records the UI's Settings → Members page writes. In the bank, roles come from
Entra ID group claims / SCIM instead.
"""
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import dotenv_values  # noqa: E402

ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
BASE = "http://localhost:3100"
PASSWORD = ENV.get("NORTHWIND_DEMO_USER_PASSWORD", "northwind-demo-1")

USERS = [  # email, name, org role, project-level override (EE) or None
    ("ds.lead@northwind.example", "Data Science Lead", "ADMIN", None),
    ("ml.engineer@northwind.example", "ML Engineer", "MEMBER", None),
    ("risk.analyst@northwind.example", "Model Risk Analyst", "VIEWER", None),
    ("auditor@northwind.example", "Internal Auditor", "NONE", "VIEWER"),  # only this project, read-only
]


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", "northwind-postgres-1", "psql", "-U", "postgres", "-t", "-A", "-c", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def main():
    for email, name, role, project_role in USERS:
        r = httpx.post(f"{BASE}/api/auth/signup", json={"name": name, "email": email, "password": PASSWORD}, timeout=20)
        print(f"signup {email}: {r.status_code}")
        uid = psql(f"select id from users where email='{email}'")
        if not uid:
            continue
        psql(f"""insert into organization_memberships (id, org_id, user_id, role, created_at, updated_at)
                 values ('om-{uid}', 'northwind', '{uid}', '{role}', now(), now())
                 on conflict (org_id, user_id) do update set role = excluded.role""")
        if project_role:
            om = psql(f"select id from organization_memberships where org_id='northwind' and user_id='{uid}'")
            psql(f"""insert into project_memberships (project_id, user_id, org_membership_id, role, created_at, updated_at)
                     values ('retail-assistant', '{uid}', '{om}', '{project_role}', now(), now())
                     on conflict (project_id, user_id) do update set role = excluded.role""")
        print(f"  → org role {role}" + (f", project role {project_role}" if project_role else ""))
    psql("""insert into prompt_protected_labels (id, project_id, label, created_at, updated_at)
            values ('ppl-production', 'retail-assistant', 'production', now(), now())
            on conflict do nothing""")
    print("✓ 'production' prompt label protected (Admin/Owner only)")
    print(psql("select u.email, m.role from organization_memberships m join users u on u.id=m.user_id where m.org_id='northwind' order by 2"))


if __name__ == "__main__":
    main()
