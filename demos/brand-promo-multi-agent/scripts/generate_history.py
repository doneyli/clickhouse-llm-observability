#!/usr/bin/env python3
"""Generate 50k synthetic backfilled traces in Langfuse."""

import sys
import time

# load_env() MUST be called before any langfuse import to avoid disrupting
# the SDK's global OTel initialization with a load_dotenv() side effect.
from src.config import load_config, load_env

load_env()

from rich.console import Console

console = Console()


def generate_history(total: int | None = None, seed: int = 42) -> int:
    env = load_env()
    cfg = load_config()

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print(
            "[bold yellow]MANUAL: No Langfuse keys found.[/bold yellow]\n"
            "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env first."
        )
        return 1

    synth = cfg.synthetic_history
    n = total or synth.total_traces

    console.print(f"[cyan]Generating {n:,} synthetic traces...[/cyan]")
    console.print(f"  Hero agent share: {synth.hero_agent_share:.0%}")
    console.print(f"  Days back: {synth.days_back}")
    console.print(f"  Business hours weighting: {synth.business_hours_weighting}")

    start = time.time()

    from src.synthetic.trace_generator import generate_traces

    results = generate_traces(
        total=n,
        hero_share=synth.hero_agent_share,
        days_back=synth.days_back,
        business_hours=synth.business_hours_weighting,
        seed=seed,
    )

    elapsed = time.time() - start
    errors = [r for r in results if "error" in r]
    success = len(results) - len(errors)

    console.print("\n[bold green]Done![/bold green]")
    console.print(f"  Total generated: {success:,}")
    console.print(f"  Errors: {len(errors)}")
    console.print(f"  Elapsed: {elapsed:.1f}s ({n / max(elapsed, 1):.0f} traces/sec)")

    if errors:
        console.print(f"\n[yellow]First error: {errors[0].get('error')}[/yellow]")

    return 0 if not errors else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    sys.exit(generate_history(total=args.total, seed=args.seed))
