#!/usr/bin/env python3
"""Generate the synthetic backfilled history (50k traces by default) in Langfuse.

Idempotent, and it repairs what is missing. The batch is keyed on every resolved input the
generator reads (synthetic-history settings, fleet, models, customer, brands, SKUs,
regions, retail partners), the generator's own source and the UTC day it counts back from
(see src/synthetic/history_batch.py). Its trace, observation and score ids derive from that
key, and every trace and observation carries the key as its version.

Each run first plans the batch offline (a few seconds, no network) to know how many
observations it holds per (trace name, observation name) and how many scores per name.
It then reads back, through the Metrics API, the batches it wrote in the previous
`--reuse-days` days:

  complete batch found   unchanged: nothing is written
  today's batch partial  the same batch is re-sent; the same ids upsert in place
  none                   a new batch anchored on today is written

After a write it reads the batch back until every group is there in full (bounded by
`--confirm-timeout`). Once the current batch is confirmed, every other batch this
generator wrote (a superseded day, or an old config after a re-theme) is deleted by its
derived trace ids, so the project holds one owned history; `--keep-superseded` skips that.
Data the generator did not write is never touched. `--force` re-sends today's batch
regardless, which still upserts rather than duplicates.

Exits 1 when the plan, the read-back, any ingestion chunk, the confirmation or a deletion
fails; 0 only when the current batch is confirmed complete.
"""

import sys
import time
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
CONFIRM_TIMEOUT_S = 600  # ingestion of 50k traces lands within minutes on Cloud
POLL_S = 15
LOOKBACK_DAYS = 400      # how far back to look for superseded batches to remove


def generate_history(
    total: int | None = None,
    seed: int = 42,
    reuse_days: int = REUSE_DAYS,
    force: bool = False,
    client: Client | None = None,
    ingest=None,
    today: date | None = None,
    confirm_timeout: float = CONFIRM_TIMEOUT_S,
    poll_s: float = POLL_S,
    sleep=time.sleep,
    clock=time.monotonic,
    keep_superseded: bool = False,
    cfg=None,
) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1
    from src.synthetic.trace_generator import batch_counts, generate_traces, iter_batch

    cfg = cfg or load_config()
    synth = cfg.synthetic_history
    n = total or synth.total_traces
    today = today or datetime.now(UTC).date()
    definition = history_batch.definition_digest()
    shape = dict(total=n, hero_share=synth.hero_agent_share, days_back=synth.days_back,
                 business_hours=synth.business_hours_weighting, seed=seed, cfg=cfg)

    # The plan: what a complete batch holds. Counts do not depend on the day or the ids.
    expected = batch_counts(iter_batch(**shape))
    if expected["errors"]:
        console.print(f"[red]History plan failed, nothing written: {expected['errors'][0]['error']}[/red]")
        return 1

    key_today = history_batch.batch_key(cfg, n, seed, today, definition)
    v_today = history_batch.stamp(key_today, n)
    current, state_today, short_today = None, "absent", []
    try:
        owned = history_batch.owned_batches(
            client, history_batch.anchor(today) - timedelta(days=LOOKBACK_DAYS + synth.days_back),
            history_batch.anchor(today) + timedelta(days=2))
        for back in range(1 if force else max(reuse_days, 1)):  # --force: only today's batch counts
            day = today - timedelta(days=back)
            key = history_batch.batch_key(cfg, n, seed, day, definition)
            if history_batch.stamp(key, n) not in owned:
                continue
            state, short = history_batch.batch_state(client, key, n, day, synth.days_back, expected)
            if back == 0:
                state_today, short_today = state, short
            if state == "complete" and not force:
                current = history_batch.stamp(key, n)
                console.print(f"[dim]History: unchanged. Batch {key} anchored {day} ({n:,} traces) "
                              f"is complete; nothing written.[/dim]")
                break
    except ApiError as e:
        console.print(f"[red]History read-back failed, nothing written: {e}[/red]")
        return 1

    if current is None:
        action = {"partial": "completing", "complete": "re-sending (--force)"}.get(state_today, "creating")
        console.print(f"[cyan]History: {action} batch {key_today} anchored {today}: {n:,} traces[/cyan]")
        for line in short_today[:5]:
            console.print(f"  short: {line}")
        console.print(f"  Hero agent share: {synth.hero_agent_share:.0%}")
        console.print(f"  Days back: {synth.days_back}")
        console.print(f"  Business hours weighting: {synth.business_hours_weighting}")

        start = datetime.now(UTC)
        results = generate_traces(**shape, anchor=history_batch.anchor(today),
                                  trace_id_for=lambda i: history_batch.trace_id(key_today, i),
                                  version=v_today, ingest=ingest)
        elapsed = (datetime.now(UTC) - start).total_seconds()
        errors = [r for r in results if "error" in r]
        console.print(f"\n[bold]History: {len(results) - len(errors):,} traces sent, {len(errors)} failed "
                      f"({elapsed:.1f}s)[/bold]")
        if errors:
            console.print(f"[red]First error: {errors[0].get('error')}. Re-run to repair: the same ids "
                          f"upsert, and superseded batches are kept until this one is complete.[/red]")
            return 1

        deadline = clock() + confirm_timeout
        while True:
            try:
                state, short = history_batch.batch_state(client, key_today, n, today, synth.days_back, expected)
            except ApiError as e:
                console.print(f"[red]History sent but the read-back failed: {e}[/red]")
                return 1
            if state == "complete":
                break
            if clock() >= deadline:
                console.print(f"[red]History sent but not confirmed within {confirm_timeout:.0f}s "
                              f"({len(short)} group(s) short, e.g. {short[0]}). Re-run: it reads the batch "
                              f"back and re-sends only if rows are still missing.[/red]")
                return 1
            sleep(poll_s)
        console.print(f"[green]History: batch {key_today} confirmed complete.[/green]")
        current = v_today

    superseded = sorted(v for v in owned if v != current)
    if superseded and keep_superseded:
        console.print(f"[yellow]History: {len(superseded)} superseded batch(es) kept (--keep-superseded).[/yellow]")
    elif superseded:
        try:
            for v in superseded:
                sent = history_batch.delete_batch(client, v)
                console.print(f"History: superseded batch {v} deleted ({sent:,} trace ids).")
        except ApiError as e:
            console.print(f"[red]Deleting a superseded history batch failed: {e}[/red]")
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
    parser.add_argument("--confirm-timeout", type=float, default=CONFIRM_TIMEOUT_S,
                        help=f"seconds to wait for a written batch to read back complete (default {CONFIRM_TIMEOUT_S})")
    parser.add_argument("--keep-superseded", action="store_true",
                        help="keep the other batches this generator wrote instead of deleting them")
    args = parser.parse_args()
    sys.exit(generate_history(total=args.total, seed=args.seed, reuse_days=args.reuse_days, force=args.force,
                              confirm_timeout=args.confirm_timeout, keep_superseded=args.keep_superseded))
