"""Self-hosted Enterprise demo state (ENT-01, ENT-02): RBAC users, a project-level
role override, and the PROTECTED `production` prompt label.

Runs against the Northwind org on the local Langfuse that .env.selfhosted points at
(`python scripts/langfuse_local.py provision bank-assistant`). Users are created
through the instance's own sign-up endpoint (test accounts on this local demo
instance only); roles are then set in Postgres — the same records the UI's
Settings → Members page writes. In the bank, roles come from Entra ID group
claims / SCIM instead.
"""
import sys
from pathlib import Path

from dotenv import dotenv_values

DEMO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO.parent.parent / "scripts"))
import langfuse_local  # noqa: E402

ENV = dotenv_values(DEMO / ".env")
PASSWORD = ENV.get("NORTHWIND_DEMO_USER_PASSWORD", "northwind-demo-1")

USERS = [  # email, name, org role, project-level override (EE) or None
    (ENV.get("LANGFUSE_INIT_USER_EMAIL") or "admin@northwind.example", "Platform Admin", "OWNER", None),
    ("ds.lead@northwind.example", "Data Science Lead", "ADMIN", None),
    ("ml.engineer@northwind.example", "ML Engineer", "MEMBER", None),
    ("risk.analyst@northwind.example", "Model Risk Analyst", "VIEWER", None),
    ("auditor@northwind.example", "Internal Auditor", "NONE", "VIEWER"),  # only this project, read-only
]


def main():
    project = langfuse_local.connect(DEMO / ".env.selfhosted")
    print(f"✓ {project.url} · org {project.org_id} · project {project.project_id}")
    for email, name, role, project_role in USERS:
        password = (ENV.get("LANGFUSE_INIT_USER_PASSWORD") or PASSWORD) if role == "OWNER" else PASSWORD
        if not project.add_user(email, name, password, role,
                                {project.project_id: project_role} if project_role else None):
            print(f"! {email}: sign-up failed")
            continue
        print(f"  {email} → org role {role}" + (f", project role {project_role}" if project_role else ""))
    project.protect_label("production")
    print("✓ 'production' prompt label protected (Admin/Owner only)")
    print(project.members())


if __name__ == "__main__":
    main()
