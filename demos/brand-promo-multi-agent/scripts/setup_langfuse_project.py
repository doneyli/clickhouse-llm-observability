#!/usr/bin/env python3
"""Set up or validate the Langfuse project for the demo."""

import sys

import httpx
from rich.console import Console
from rich.panel import Panel

from src.config import load_config, load_env

console = Console()


def setup_langfuse_project() -> bool:
    cfg = load_config()
    env = load_env()

    host = env.langfuse_host or cfg.langfuse.host
    project_name = cfg.langfuse.project_name

    console.print(Panel(f"[bold]Langfuse Project Setup[/bold]\nHost: {host}\nProject: {project_name}"))

    if env.langfuse_admin_token:
        console.print("[yellow]LANGFUSE_ADMIN_TOKEN found - attempting API project creation...[/yellow]")
        try:
            headers = {
                "Authorization": f"Bearer {env.langfuse_admin_token}",
                "Content-Type": "application/json",
            }
            resp = httpx.post(
                f"{host}/api/admin/projects",
                json={"name": project_name},
                headers=headers,
                timeout=10,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                console.print(f"[green]Project created: {data.get('name')}[/green]")
                if "publicKey" in data and "secretKey" in data:
                    console.print("\n[bold]Add to .env:[/bold]")
                    console.print(f"LANGFUSE_PUBLIC_KEY={data['publicKey']}")
                    console.print(f"LANGFUSE_SECRET_KEY={data['secretKey']}")
                    console.print(f"LANGFUSE_HOST={host}")
                return True
            elif resp.status_code == 409:
                console.print("[yellow]Project already exists.[/yellow]")
            else:
                console.print(f"[red]Admin API returned {resp.status_code}: {resp.text}[/red]")
        except Exception as e:
            console.print(f"[red]Admin API failed: {e}[/red]")

    if not env.langfuse_admin_token:
        console.print(
            Panel(
                "[bold yellow]MANUAL SETUP REQUIRED[/bold yellow]\n\n"
                "No LANGFUSE_ADMIN_TOKEN found. Create the project manually:\n\n"
                f"1. Open {host} in your browser\n"
                "2. Sign in (default: admin@langfuse.com / password)\n"
                "3. Click 'New Project'\n"
                f"4. Name it: [bold]{project_name}[/bold]\n"
                "5. After creation, go to Project Settings -> API Keys\n"
                "6. Copy the Public Key and Secret Key\n"
                "7. Add to .env:\n"
                "   LANGFUSE_PUBLIC_KEY=<public key>\n"
                "   LANGFUSE_SECRET_KEY=<secret key>\n"
                f"   LANGFUSE_HOST={host}\n\n"
                "Then re-run this script to verify connectivity.",
                title="Manual Step",
            )
        )

    # Verify connectivity if keys are present
    if env.langfuse_public_key and env.langfuse_secret_key:
        console.print("[cyan]Verifying Langfuse connectivity...[/cyan]")
        try:
            resp = httpx.get(
                f"{host}/api/public/projects",
                auth=(env.langfuse_public_key, env.langfuse_secret_key),
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                projects = data.get("data", [])
                if projects:
                    console.print(f"[green]Connected! Project: {projects[0].get('name')}[/green]")
                    return True
                else:
                    console.print("[yellow]Connected but no projects found.[/yellow]")
            else:
                console.print(f"[red]Connectivity check failed: {resp.status_code}[/red]")
        except Exception as e:
            console.print(f"[red]Connection error: {e}[/red]")
            console.print("[yellow]Is Langfuse running at " + host + "?[/yellow]")
    else:
        console.print("[yellow]No LANGFUSE_PUBLIC_KEY / SECRET_KEY in .env - skipping connectivity check.[/yellow]")

    return False


if __name__ == "__main__":
    success = setup_langfuse_project()
    sys.exit(0 if success else 1)
