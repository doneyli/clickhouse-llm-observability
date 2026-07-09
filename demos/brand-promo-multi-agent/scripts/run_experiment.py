#!/usr/bin/env python3
"""Run PromoPlanner experiment against the golden dataset in Langfuse.

Evaluates the agent on a labeled dataset, scores each item with deterministic
and LLM-as-judge evaluators, aggregates run-level scores, and applies a
multi-dimensional certification gate. The demo's "three moments" live here.

Usage:
    uv run python scripts/run_experiment.py --run-name baseline
    uv run python scripts/run_experiment.py --run-name strategy-v2 --label strategy-v2
    uv run python scripts/run_experiment.py --evaluators deterministic --ci
    uv run python scripts/run_experiment.py --sample 10 --queue-failures
    uv run python scripts/run_experiment.py --system-prompt-file prompts/strategy_v2.md --label v2

Cost note: full run (75 items, all judges) ~$10-15 on Anthropic. Use --sample 10
for cheap rehearsal.

Environment variables:
    ANTHROPIC_API_KEY      (required)
    LANGFUSE_PUBLIC_KEY    (required)
    LANGFUSE_SECRET_KEY    (required)
    LANGFUSE_HOST          (default: http://localhost:3001)
"""

import argparse
import json
import os
import random
import sys
import urllib.request
from datetime import datetime

# OTel settings must be configured before langfuse is imported. These prevent
# queue saturation hangs on long runs (75+ items) with a slow local Langfuse.
os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "20000")
os.environ.setdefault("OTEL_BSP_SCHEDULE_DELAY", "2000")
os.environ.setdefault("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "64")
os.environ.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "120000")
os.environ.setdefault("LANGFUSE_FLUSH_AT", "64")
os.environ.setdefault("LANGFUSE_FLUSH_INTERVAL", "2")

DATASET_NAME = "promo-planner-golden-v1"
REVIEW_QUEUE_NAME = "PromoPlanner Human Review"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run PromoPlanner experiments against the golden dataset"
    )
    parser.add_argument("--dataset", type=str, default=DATASET_NAME,
                        help=f"Langfuse dataset name (default: {DATASET_NAME})")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Custom run name (default: auto-generated timestamp)")
    parser.add_argument("--label", type=str, default=None,
                        help="Variant label for side-by-side comparison (e.g. strategy-v2)")
    parser.add_argument("--evaluators", type=str, default="all",
                        choices=["all", "deterministic", "judge", "accuracy"],
                        help="Which evaluator groups to run (default: all)")
    parser.add_argument("--sample", type=int, default=None,
                        help="Run on a random subset of N items (for cheap rehearsal)")
    parser.add_argument("--max-concurrency", type=int, default=3,
                        help="Max concurrent orchestrator + judge calls (default: 3)")
    parser.add_argument("--intent-threshold", type=float, default=0.85,
                        help="Gate threshold for avg_intent_classification_accuracy (default: 0.85)")
    parser.add_argument("--compliance-threshold", type=float, default=0.90,
                        help="Gate threshold for avg_compliance_status_match (default: 0.90)")
    parser.add_argument("--factuality-threshold", type=float, default=0.80,
                        help="Gate threshold for avg_response_factuality (default: 0.80)")
    parser.add_argument("--queue-failures", action="store_true",
                        help="Route low-scoring items to the annotation queue")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: exit 1 if certification gate fails")
    parser.add_argument("--system-prompt-file", type=str, default=None,
                        help="Path to a markdown file appended to the strategist's system prompt for a prompt A/B run (e.g. prompts/strategy_v2.md)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview dataset items without running the experiment")
    return parser.parse_args()


def select_evaluators(evaluator_mode: str, intent_thresh: float,
                      compliance_thresh: float, factuality_thresh: float):
    from src.evals.evaluators import (
        average_score_evaluator,
        brief_contains,
        brief_length_sanity,
        brief_quality_judge,
        compliance_adherence_judge,
        compliance_status_match,
        intent_classification_accuracy,
        promo_certification_gate,
        response_factuality_judge,
        sku_validity,
        tool_call_correctness_judge,
        tool_call_match,
    )

    item_evaluators = []
    run_evaluators = []

    # Deterministic evaluators (fast, free)
    if evaluator_mode in ("all", "deterministic", "accuracy"):
        item_evaluators += [
            intent_classification_accuracy,
            tool_call_match,
            compliance_status_match,
            brief_contains,
            sku_validity,
            brief_length_sanity,
        ]
        run_evaluators += [
            average_score_evaluator("intent_classification_accuracy"),
            average_score_evaluator("tool_call_match"),
            average_score_evaluator("compliance_status_match"),
            average_score_evaluator("brief_contains"),
            average_score_evaluator("sku_validity"),
        ]

    # LLM-as-judge evaluators (slow, ~$0.10-0.20/item)
    if evaluator_mode in ("all", "judge"):
        item_evaluators += [
            tool_call_correctness_judge,
            response_factuality_judge,
            compliance_adherence_judge,
            brief_quality_judge,
        ]
        run_evaluators += [
            average_score_evaluator("tool_call_correctness"),
            average_score_evaluator("response_factuality"),
            average_score_evaluator("compliance_adherence"),
        ]

    # Gate always runs when deterministic scores are available
    if evaluator_mode in ("all", "deterministic", "accuracy"):
        run_evaluators.append(
            promo_certification_gate(
                intent_threshold=intent_thresh,
                compliance_threshold=compliance_thresh,
                factuality_threshold=factuality_thresh,
            )
        )

    return item_evaluators, run_evaluators


def _queue_failed_items(item_results, lf_host: str, auth: str) -> None:
    """Route low-scoring traces to the PromoPlanner Human Review annotation queue."""
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }

    # Find queue ID
    try:
        req = urllib.request.Request(
            f"{lf_host}/api/public/annotation-queues?limit=100",
            headers=headers,
        )
        resp = urllib.request.urlopen(req, timeout=10)
        queues = json.loads(resp.read()).get("data", [])
        queue_id = next((q["id"] for q in queues if q["name"] == REVIEW_QUEUE_NAME), None)
        if not queue_id:
            print(f"  Warning: annotation queue '{REVIEW_QUEUE_NAME}' not found. "
                  "Run seed_annotation_queue.py first.", file=sys.stderr)
            return
    except Exception as e:
        print(f"  Warning: could not list annotation queues: {e}", file=sys.stderr)
        return

    queued = 0
    for ir in item_results:
        if not hasattr(ir, "trace_id") or not ir.trace_id:
            continue

        should_queue = any(
            (ev.name in ("intent_classification_accuracy", "compliance_status_match") and ev.value == 0.0)
            or (ev.name in ("response_factuality", "tool_call_correctness") and ev.value is not None and ev.value < 0.5)
            for ev in ir.evaluations
        )
        if not should_queue:
            continue

        try:
            body = json.dumps({
                "objectId": ir.trace_id,
                "objectType": "TRACE",
                "status": "PENDING",
            }).encode()
            req = urllib.request.Request(
                f"{lf_host}/api/public/annotation-queues/{queue_id}/items",
                data=body,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            queued += 1
        except Exception as e:
            print(f"  Warning: failed to queue trace {str(ir.trace_id)[:12]}...: {e}",
                  file=sys.stderr)

    if queued:
        print(f"\n  Queued {queued} items for human review in '{REVIEW_QUEUE_NAME}'",
              file=sys.stderr)


def _post_run_level_scores(item_results, run_evaluations, lf_host: str, auth: str) -> None:
    """POST run-level scores to Langfuse REST API.

    The SDK computes run_evaluators locally but does not persist them.
    We attach them to the first experiment trace so they appear in the UI.
    """
    first_trace_id = next(
        (ir.trace_id for ir in item_results if hasattr(ir, "trace_id") and ir.trace_id),
        None,
    )
    if not first_trace_id:
        return

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }
    for ev in run_evaluations:
        if ev.value is None:
            continue
        try:
            body = json.dumps({
                "traceId": first_trace_id,
                "name": ev.name,
                "value": ev.value,
                "comment": ev.comment or "",
                "dataType": "NUMERIC",
            }).encode()
            req = urllib.request.Request(
                f"{lf_host}/api/public/scores",
                data=body,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"  Warning: failed to persist run score '{ev.name}': {e}", file=sys.stderr)


def _print_summary(result, args, run_name: str) -> None:
    """Print a rich-formatted summary table."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        console.print()

        table = Table(title="Experiment Results", show_header=True, header_style="bold cyan")
        table.add_column("Score", style="white", min_width=35)
        table.add_column("Value", justify="right", min_width=12)
        table.add_column("Comment", style="dim", min_width=40)

        for ev in sorted(result.run_evaluations, key=lambda e: e.name or ""):
            if ev.value is None:
                continue
            name = ev.name or ""
            if name == "certification_gate":
                style = "bold green" if ev.value == 1.0 else "bold red"
                display = "PASSED" if ev.value == 1.0 else "FAILED"
                table.add_row(f"[{style}]{name}[/{style}]",
                              f"[{style}]{display}[/{style}]",
                              ev.comment or "")
            elif name.startswith("avg_"):
                val_str = f"{ev.value:.1%}"
                style = "green" if ev.value >= 0.75 else "yellow" if ev.value >= 0.5 else "red"
                table.add_row(name, f"[{style}]{val_str}[/{style}]", ev.comment or "")
            else:
                table.add_row(name, f"{ev.value:.3f}", ev.comment or "")

        console.print(table)
        console.print(f"\n[dim]Run name:[/dim] {run_name}")
        console.print(f"[dim]Dataset:[/dim]  {args.dataset}")
        console.print(f"[dim]Items:[/dim]    {len(result.item_results)}")
        console.print(
            f"\n[dim]View in Langfuse:[/dim] Datasets > {args.dataset} > Runs\n"
        )
    except ImportError:
        print("\n" + "=" * 60, file=sys.stderr)
        print("EXPERIMENT SUMMARY", file=sys.stderr)
        for ev in result.run_evaluations:
            if ev.value is not None:
                print(f"  {ev.name}: {ev.value:.3f} - {ev.comment}", file=sys.stderr)


def main() -> int:
    args = parse_args()

    # Load env before importing langfuse so keys are available
    from src.config import load_env, resolve_backend
    env = load_env()

    backend_name = resolve_backend()

    if not env.anthropic_api_key:
        print("Error: ANTHROPIC_API_KEY required in .env", file=sys.stderr)
        return 1
    if not env.langfuse_public_key or not env.langfuse_secret_key:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required in .env "
              "(backend=langfuse)", file=sys.stderr)
        return 1

    try:
        from src.experiment_backends import get_backend
        backend = get_backend(backend_name)
    except Exception as e:
        print(f"Error: could not initialize backend '{backend_name}': {e}", file=sys.stderr)
        return 1

    # Build run name and label
    label = (args.label or "").strip().strip("-") or None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.run_name:
        run_name = args.run_name
    elif label:
        run_name = f"promo-{label}-{timestamp}"
    else:
        run_name = f"promo-baseline-{timestamp}"

    # Load optional system prompt file (recorded in metadata; see prompts/strategy_v2.md)
    system_prompt_content = None
    if args.system_prompt_file:
        try:
            with open(args.system_prompt_file, encoding="utf-8") as f:
                system_prompt_content = f.read()
        except FileNotFoundError:
            print(f"Warning: system prompt file not found: {args.system_prompt_file}",
                  file=sys.stderr)

    print("PromoPlanner Experiment Runner", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Backend:     {backend_name}", file=sys.stderr)
    print(f"  Langfuse:    {env.langfuse_host}", file=sys.stderr)
    print(f"  Public key:  {(env.langfuse_public_key or '')[:12]}...", file=sys.stderr)
    print(f"  Dataset:     {args.dataset}", file=sys.stderr)
    print(f"  Run name:    {run_name}", file=sys.stderr)
    if label:
        print(f"  Label:       {label}", file=sys.stderr)
    print(f"  Evaluators:  {args.evaluators}", file=sys.stderr)
    print(f"  Concurrency: {args.max_concurrency}", file=sys.stderr)
    if args.sample:
        print(f"  Sample:      {args.sample} items", file=sys.stderr)
    if system_prompt_content:
        print(f"  Sys prompt:  {args.system_prompt_file} ({len(system_prompt_content)} chars)",
              file=sys.stderr)

    # Load dataset via backend adapter
    try:
        dataset = backend.load_dataset(args.dataset)
    except Exception as e:
        print(f"\nError loading dataset '{args.dataset}': {e}", file=sys.stderr)
        print("Run scripts/seed_dataset.py first.", file=sys.stderr)
        return 1

    items = dataset.items
    if args.sample and args.sample < len(items):
        items = random.sample(items, args.sample)
        print(f"  Items:       {len(items)} (sampled from {len(dataset.items)})", file=sys.stderr)
    else:
        print(f"  Items:       {len(items)}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - no experiment will be run **\n", file=sys.stderr)
        for item in items[:10]:
            inp = item.input if isinstance(item.input, dict) else {"raw": str(item.input)}
            preview = inp.get("query", str(inp))[:80]
            print(f"  [{item.id[:8]}] {preview}", file=sys.stderr)
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more", file=sys.stderr)
        return 0

    # Select evaluators
    item_evaluators, run_evaluators = select_evaluators(
        args.evaluators,
        args.intent_threshold,
        args.compliance_threshold,
        args.factuality_threshold,
    )
    print("\n  Running experiment...\n", file=sys.stderr)

    # Task closure: run the PromoPlanner orchestrator on each dataset item.
    # Per-call attribution flows via a v4 RunnableConfig built by
    # make_observability_run_config() (handler + metadata + tags); it is passed
    # to graph.invoke via run_orchestrator.
    from src.observability import (
        make_observability_run_config,
        with_observability_context,
    )

    def task(*, item, **kwargs):
        from src.agents.orchestrator import run_orchestrator
        inp = item.input if hasattr(item, "input") else item.get("input", {})
        query = inp.get("query", str(inp)) if isinstance(inp, dict) else str(inp)

        if system_prompt_content:
            # Full content — strategy_crew.py reads this and appends it to the
            # strategist's backstory. (Was truncated to 500 chars, which both
            # lost most of the prompt and was never read by anything.)
            os.environ["PROMO_SYSTEM_PROMPT_OVERRIDE"] = system_prompt_content

        item_id = getattr(item, "id", None)
        extra_metadata = {
            "run_name": run_name,
            "label": label or "baseline",
            "item_id": item_id,
        }
        run_config = make_observability_run_config(
            agent_name="PromoPlanner",
            session_id=run_name,
            tags=["experiment", label or "baseline"],
            extra_metadata=extra_metadata,
            backend=backend_name,
        )
        with with_observability_context(
            agent_name="PromoPlanner",
            session_id=run_name,
            tags=["experiment", label or "baseline"],
            extra_metadata=extra_metadata,
            backend=backend_name,
        ):
            state = run_orchestrator(query, config=run_config)
        return dict(state)

    # Run experiment via backend adapter
    result = backend.run_experiment(
        dataset=dataset,
        run_name=run_name,
        task=task,
        evaluators=item_evaluators,
        run_evaluators=run_evaluators,
        max_concurrency=args.max_concurrency,
        metadata={
            "run_name": run_name,
            "label": label or "baseline",
            "evaluator_mode": args.evaluators,
            "sample_size": args.sample or len(items),
            "intent_threshold": args.intent_threshold,
            "compliance_threshold": args.compliance_threshold,
            "factuality_threshold": args.factuality_threshold,
            "system_prompt_file": args.system_prompt_file or "",
            "agent_name": "PromoPlanner",
            "backend": backend_name,
        },
    )

    if result.run_evaluations and result.item_results:
        backend.persist_run_scores(result.item_results, result.run_evaluations)

    _print_summary(result, args, run_name)

    if args.queue_failures and result.item_results:
        backend.queue_failed_items(result.item_results, REVIEW_QUEUE_NAME)

    if hasattr(backend, "flush"):
        backend.flush()

    # CI mode: exit 1 if gate failed
    if args.ci:
        for ev in result.run_evaluations:
            if ev.name == "certification_gate" and ev.value != 1.0:
                print(f"\nCertification FAILED - {ev.comment}", file=sys.stderr)
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
