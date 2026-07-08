#!/usr/bin/env python3
"""Run all seed steps in order. Idempotent."""

import sys

from rich.console import Console
from rich.panel import Panel

console = Console()


def run_step(name: str, fn: callable) -> bool:
    console.print(Panel(f"[bold cyan]Step: {name}[/bold cyan]"))
    try:
        rc = fn()
        if rc == 0:
            console.print(f"[green]{name}: OK[/green]\n")
            return True
        else:
            console.print(f"[yellow]{name}: completed with warnings[/yellow]\n")
            return True
    except Exception as e:
        console.print(f"[red]{name}: FAILED - {e}[/red]\n")
        return False


def main() -> int:
    console.print(Panel("[bold]Brand Promo Multi-Agent Demo - Full Seed[/bold]"))

    from scripts.seed_annotation_queue import seed_annotation_queue
    from scripts.seed_dashboards import seed_dashboards
    from scripts.seed_dataset import seed_dataset
    from scripts.seed_evaluators import seed_evaluators
    from scripts.seed_prompts import seed_prompts
    from scripts.setup_langfuse_project import setup_langfuse_project

    from scripts.setup_score_configs import main as setup_score_configs

    steps = [
        ("Setup Langfuse Project", lambda: (setup_langfuse_project(), 0)[1]),
        ("Seed Prompts", seed_prompts),
        ("Seed Evaluators", seed_evaluators),
        ("Setup Score Configs", setup_score_configs),
        ("Seed Dataset", seed_dataset),
        ("Seed Dashboards", seed_dashboards),
    ]

    all_ok = True
    for name, fn in steps:
        ok = run_step(name, fn)
        if not ok:
            all_ok = False

    # Generate history last (slowest step)
    console.print(Panel("[bold cyan]Step: Generate Synthetic History[/bold cyan]"))
    console.print("[yellow]This generates 50k traces - may take several minutes.[/yellow]")
    console.print("[yellow]Run separately if needed: uv run scripts/generate_history.py[/yellow]")
    try:
        from scripts.generate_history import generate_history
        rc = generate_history()
        if rc == 0:
            console.print("[green]Generate History: OK[/green]\n")
        else:
            console.print("[yellow]Generate History: completed with warnings[/yellow]\n")
    except Exception as e:
        console.print(f"[yellow]Generate History: {e} - run separately[/yellow]\n")

    # Annotation queue after history
    run_step("Seed Annotation Queue", seed_annotation_queue)

    if all_ok:
        console.print(Panel("[bold green]All seed steps complete![/bold green]"))
    else:
        console.print(Panel("[bold yellow]Seed complete with some manual steps required.[/bold yellow]"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
