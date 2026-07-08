#!/usr/bin/env python3
"""Live demo CLI - run the PromoPlanner orchestrator interactively.

Builds a Langfuse CallbackHandler list and wraps orchestrator calls in
`with_observability_context(...)` so per-call attribution flows correctly.
"""

from __future__ import annotations

import os
import sys

# Ensure the project root is on sys.path so `from src...` resolves.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import time
import uuid
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="PromoPlanner live demo CLI")
console = Console()


def _trace_url(backend: str, host: str | None) -> str | None:
    """Best-effort deep link to the Langfuse trace view."""
    if backend == "langfuse":
        if not host:
            return None
        return f"{host}/project/default/traces?filter=agent_name%3DPromoPlanner"
    return None


def _run_query(query_text: str) -> str | None:
    """Run the orchestrator and return a Langfuse trace URL."""
    from src.agents.orchestrator import run_orchestrator
    from src.config import load_config, load_env, resolve_backend
    from src.observability import (
        make_observability_callbacks,
        make_observability_run_config,
        with_observability_context,
    )

    cfg = load_config()
    env = load_env()
    backend = resolve_backend()
    host = env.langfuse_host or (cfg.langfuse.host if cfg.langfuse else None)

    console.print(f"[cyan]Backend:[/cyan] {backend}")
    console.print(f"[cyan]Query:[/cyan]   {query_text}")
    console.print("[yellow]Running orchestrator...[/yellow]")

    session_id = f"live-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    try:
        run_config = make_observability_run_config(
            agent_name="PromoPlanner",
            session_id=session_id,
            tags=["live_demo"],
            extra_metadata={"customer": cfg.customer.display_name},
            backend=backend,
        )
        with with_observability_context(
            agent_name="PromoPlanner",
            session_id=session_id,
            tags=["live_demo"],
            extra_metadata={"customer": cfg.customer.display_name},
            backend=backend,
        ):
            result = run_orchestrator(query_text, config=run_config)

        console.print("\n[bold green]Final Brief:[/bold green]")
        console.print(Panel(result.get("final_brief", "No brief generated"), expand=False))
        console.print(f"\n[cyan]Intent:[/cyan]     {result.get('intent')}")
        console.print(f"[cyan]Compliance:[/cyan] {result.get('compliance_status', 'N/A')}")
        console.print(f"[cyan]Tools called:[/cyan] {', '.join(result.get('tools_called', []))}")
        console.print(f"[cyan]Session ID:[/cyan]  {session_id}")

        url = _trace_url(backend, host)
        if url:
            console.print(f"\n[bold]{backend.capitalize()} URL:[/bold] {url}")
        return url
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return None


@app.command()
def query(text: str = typer.Argument(..., help="Free-form query to run")):
    """Run the orchestrator once on a free-form query."""
    _run_query(text)


@app.command()
def play(
    query_id: str = typer.Argument(..., help="Demo query ID (e.g. q1_happy_path)"),
    countdown: bool = typer.Option(True, help="Show 2-second countdown"),
):
    """Run a pre-canned demo query by ID with optional countdown."""
    from src.config import load_config

    cfg = load_config()
    query_map = {q.id: q for q in cfg.live_demo_queries}

    if query_id not in query_map:
        console.print(f"[red]Unknown query ID: {query_id}[/red]")
        console.print(f"Available: {', '.join(query_map.keys())}")
        raise typer.Exit(1)

    demo_query = query_map[query_id]
    console.print(Panel(
        f"[bold]Demo Query: {query_id}[/bold]\n"
        f"Expected: {demo_query.expected_outcome}",
        title="Pre-canned Query",
    ))

    if countdown:
        for i in (3, 2, 1):
            console.print(f"[yellow]Starting in {i}...[/yellow]")
            time.sleep(1)

    _run_query(demo_query.text)


@app.command(name="play-all")
def play_all(pause: int = typer.Option(3, help="Seconds to pause between queries")):
    """Run all 5 demo queries in sequence."""
    from src.config import load_config

    cfg = load_config()

    console.print(Panel(
        f"[bold]Running all {len(cfg.live_demo_queries)} demo queries[/bold]\n"
        f"Pause between queries: {pause}s",
        title="Full Demo Run",
    ))

    for i, q in enumerate(cfg.live_demo_queries, 1):
        console.print(f"\n[bold cyan]Query {i}/{len(cfg.live_demo_queries)}: {q.id}[/bold cyan]")
        _run_query(q.text)
        if i < len(cfg.live_demo_queries):
            console.print(f"[dim]Pausing {pause}s before next query...[/dim]")
            time.sleep(pause)

    console.print(Panel("[bold green]All demo queries complete![/bold green]"))


@app.command(name="clear-live-tag")
def clear_live_tag():
    """Add a timestamped live-demo tag to recent traces for filtering in the UI."""
    from src.config import load_config, load_env

    env = load_env()
    cfg = load_config()

    tag = f"demo_live_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print("[red]No Langfuse keys in .env - cannot tag traces[/red]")
        raise typer.Exit(1)
    host = env.langfuse_host or (cfg.langfuse.host if cfg.langfuse else "")
    console.print(f"[cyan]Demo live tag: {tag}[/cyan]")
    console.print(
        f"[yellow]To filter: open {host} -> Traces, filter by tag '{tag}'[/yellow]"
    )
    console.print("[dim]Tag has been recorded. Apply it to your session in the UI.[/dim]")


if __name__ == "__main__":
    app()
