#!/usr/bin/env python3
"""Run every seed step in order and report what each one actually did. Idempotent.

Each step reconciles its own assets and prints created / updated / unchanged counts, so a
second run with nothing changed writes nothing. The steps' results are aggregated rather
than swallowed:

  exit 0  every step succeeded and nothing is left to do by hand
  exit 2  every automated step succeeded, but a manual UI step is still outstanding
          (the persona dashboards or the online judges); the checklist is printed above
  exit 1  at least one step failed: an API error, a missing key, or an incomplete write

A failed step does not stop the later ones, except that the annotation queue is skipped
when the history step failed, because it picks its items from the history.
"""

import sys

from rich.console import Console
from rich.panel import Panel

console = Console()

OK, FAILED, MANUAL = 0, 1, 2
LABELS = {OK: "[green]OK[/green]", FAILED: "[red]FAILED[/red]", MANUAL: "[yellow]MANUAL STEP[/yellow]"}


def run_step(name: str, fn) -> int:
    """Run one step and map its result to OK, FAILED or MANUAL. Exceptions are failures."""
    console.print(Panel(f"[bold cyan]Step: {name}[/bold cyan]"))
    try:
        rc = fn()
    except Exception as e:  # a step that raises has not finished its assets
        console.print(f"[red]{name}: FAILED - {type(e).__name__}: {e}[/red]\n")
        return FAILED
    status = rc if rc in (OK, MANUAL) else FAILED
    console.print(f"{name}: {LABELS[status]}\n")
    return status


def default_steps() -> list[tuple[str, object]]:
    from scripts.generate_history import generate_history
    from scripts.seed_annotation_queue import seed_annotation_queue
    from scripts.seed_dashboards import seed_dashboards
    from scripts.seed_dataset import seed_dataset
    from scripts.seed_evaluators import seed_evaluators
    from scripts.seed_prompts import seed_prompts
    from scripts.setup_langfuse_project import setup_langfuse_project
    from scripts.setup_score_configs import main as setup_score_configs

    return [
        ("Setup Langfuse Project", lambda: OK if setup_langfuse_project() else FAILED),
        ("Seed Prompts", seed_prompts),
        ("Check Online Judges", seed_evaluators),
        ("Setup Score Configs", lambda: setup_score_configs([])),
        ("Seed Dataset", seed_dataset),
        ("Check Dashboards", seed_dashboards),
        ("Generate Synthetic History", generate_history),  # no-op when a current batch exists
        ("Seed Annotation Queue", seed_annotation_queue),  # picks items from the history
    ]


def main(steps: list[tuple[str, object]] | None = None) -> int:
    console.print(Panel("[bold]Brand Promo Multi-Agent Demo - Full Seed[/bold]"))
    results: dict[str, int] = {}
    for name, fn in steps if steps is not None else default_steps():
        if name == "Seed Annotation Queue" and results.get("Generate Synthetic History") == FAILED:
            console.print(f"{name}: [red]SKIPPED[/red] (the history step failed)\n")
            results[name] = FAILED
            continue
        results[name] = run_step(name, fn)

    console.print(Panel("\n".join(f"{LABELS[s]:<32} {n}" for n, s in results.items()), title="Seed summary"))
    failed = [n for n, s in results.items() if s == FAILED]
    manual = [n for n, s in results.items() if s == MANUAL]
    if failed:
        console.print(f"[bold red]Seed incomplete: {len(failed)} step(s) failed: {', '.join(failed)}[/bold red]")
        return FAILED
    if manual:
        console.print(f"[bold yellow]Automated steps complete; manual UI step(s) outstanding: "
                      f"{', '.join(manual)}. Follow the checklists above, then re-run.[/bold yellow]")
        return MANUAL
    console.print("[bold green]All seed steps complete.[/bold green]")
    return OK


if __name__ == "__main__":
    sys.exit(main())
