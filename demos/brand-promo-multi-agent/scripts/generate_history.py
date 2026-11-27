#!/usr/bin/env python3
"""Generate the synthetic backfilled history (50k traces by default) in Langfuse.

Idempotent. The batch is keyed on its inputs and the UTC day it counts back from (see
src/synthetic/history_batch.py), and its trace and observation ids derive from that key.
Before writing, the script reads back the first and last trace of today's batch and of
the batches from the previous `--reuse-days` days:

  complete batch found   unchanged: nothing is written
  today's batch partial  the same batch is re-sent; the same ids upsert in place
  none                   a new batch anchored on today is written

So re-running within the reuse window is a no-op, and a re-run after it writes one fresh
batch while the old one ages out of the 30-day view. Changing the synthetic-history
settings, fleet or models in demo.config.yaml changes the key. `--force` re-sends today's
batch regardless, which still upserts rather than duplicates. Exits 1 when the read-back
or any ingestion batch fails.
"""

import sys
from datetime import UTC, date, datetime, timedelta

# load_env() MUST be called before any langfuse import to avoid disrupting
# the SDK's global OTel initialization with a load_dotenv() side effect.
from src.config import load_config, load_env

load_env()

from rich.console import Console  # noqa: E402

from src.langfuse_rest import ApiError, Client, LangfuseEnvError  # noqa: E402
from src.synthetic import history_batch  # noqa: E402

console = Console()

REUSE_DAYS = 7


def generate_history(
    total: int | None = None,
    seed: int = 42,
    reuse_days: int = REUSE_DAYS,
    force: bool = False,
    client: Client | None = None,
    generate=None,
    today: date | None = None,
) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1
    cfg = load_config()
    synth = cfg.synthetic_history
    n = total or synth.total_traces
    today = today or datetime.now(UTC).date()
    key_today = history_batch.batch_key(cfg, n, seed, today)

    state_today = "absent"
    try:
        for back in range(max(reuse_days, 1)):
            day = today - timedelta(days=back)
            key = history_batch.batch_key(cfg, n, seed, day)
            state = history_batch.batch_state(client, key, n, day, synth.days_back)
            if back == 0:
                state_today = state
            if state == "complete" and not force:
                console.print(f"[dim]History: unchanged. Batch {key} anchored {day} ({n:,} traces) "
                              f"is complete; nothing written.[/dim]")
                return 0
    except ApiError as e:
        console.print(f"[red]History read-back failed, nothing written: {e}[/red]")
        return 1

    action = {"partial": "completing", "complete": "re-sending (--force)"}.get(state_today, "creating")
    console.print(f"[cyan]History: {action} batch {key_today} anchored {today}: {n:,} traces[/cyan]")
    console.print(f"  Hero agent share: {synth.hero_agent_share:.0%}")
    console.print(f"  Days back: {synth.days_back}")
    console.print(f"  Business hours weighting: {synth.business_hours_weighting}")

    if generate is None:
        from src.synthetic.trace_generator import generate_traces as generate

    start = datetime.now(UTC)
    results = generate(
        total=n,
        hero_share=synth.hero_agent_share,
        days_back=synth.days_back,
        business_hours=synth.business_hours_weighting,
        seed=seed,
        anchor=history_batch.anchor(today),
        trace_id_for=lambda i: history_batch.trace_id(key_today, i),
    )
    elapsed = (datetime.now(UTC) - start).total_seconds()
    errors = [r for r in results if "error" in r]
    written = len(results) - len(errors)

    verb = {"creating": "created", "completing": "updated"}.get(action, "updated")
    console.print(f"\n[bold]History: {written:,} traces {verb}, {len(errors)} failed "
                  f"({elapsed:.1f}s)[/bold]")
    if errors:
        console.print(f"[red]First error: {errors[0].get('error')}[/red]")
        return 1
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--total", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reuse-days", type=int, default=REUSE_DAYS,
                        help=f"a complete batch anchored this many days back counts as current (default {REUSE_DAYS})")
    parser.add_argument("--force", action="store_true",
                        help="re-send today's batch even when one is complete (upserts, never duplicates)")
    args = parser.parse_args()
    sys.exit(generate_history(total=args.total, seed=args.seed, reuse_days=args.reuse_days, force=args.force))
