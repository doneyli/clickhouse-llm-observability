#!/usr/bin/env python3
"""Seed the PromoPlanner Human Review annotation queue with ambiguous-score traces."""

import sys

from rich.console import Console

from src.config import load_config, load_env

console = Console()


def seed_annotation_queue() -> int:
    env = load_env()
    cfg = load_config()

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print(
            "[bold yellow]MANUAL: No Langfuse keys found.[/bold yellow]\n"
            "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env first."
        )
        return 0

    try:
        import httpx
        from langfuse import Langfuse
    except ImportError:
        console.print("[red]langfuse or httpx not installed[/red]")
        return 1

    host = env.langfuse_host or cfg.langfuse.host

    queue_name = "PromoPlanner Human Review"

    # Create the annotation queue via API. Idempotent: on re-run the queue
    # already exists, so we look it up by name and reuse it rather than giving
    # up (which would leave the queue unmanaged) or creating a duplicate.
    queue_id = None
    try:
        resp = httpx.post(
            f"{host}/api/public/annotation-queues",
            auth=(env.langfuse_public_key, env.langfuse_secret_key),
            json={
                "name": queue_name,
                "description": "Traces with ambiguous factuality scores (0.6-0.8) for human review",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            queue_id = resp.json().get("id")
            console.print(f"[green]Annotation queue created: {queue_name} (id: {queue_id})[/green]")
        elif resp.status_code == 409:
            console.print(f"[yellow]Queue already exists: {queue_name} - reusing it[/yellow]")
        else:
            console.print(f"[yellow]Queue API returned {resp.status_code} - trying manual approach[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Queue API failed: {e}[/yellow]")

    # If the queue was not returned above (e.g. it already existed), find it by
    # name so the item-seeding below is idempotent across re-runs.
    if queue_id is None:
        try:
            list_resp = httpx.get(
                f"{host}/api/public/annotation-queues",
                auth=(env.langfuse_public_key, env.langfuse_secret_key),
                params={"limit": 100},
                timeout=10,
            )
            if list_resp.status_code == 200:
                for q in list_resp.json().get("data", []):
                    if q.get("name") == queue_name:
                        queue_id = q.get("id")
                        break
        except Exception as e:
            console.print(f"[yellow]Queue lookup failed: {e}[/yellow]")

    # Find traces with ambiguous scores (factuality 0.6-0.8) from synthetic history
    # If not available via API, emit manual instructions
    try:
        traces_resp = httpx.get(
            f"{host}/api/public/traces",
            auth=(env.langfuse_public_key, env.langfuse_secret_key),
            params={
                "tags": "synthetic",
                "limit": 100,
            },
            timeout=15,
        )
        if traces_resp.status_code == 200:
            all_traces = traces_resp.json().get("data", [])
            # Filter for PromoPlanner traces with ambiguous scores
            ambiguous = []
            for trace in all_traces:
                scores = trace.get("scores", [])
                for score in scores:
                    if score.get("name") == "response-factuality":
                        val = score.get("value", 1.0)
                        if 0.6 <= val <= 0.8:
                            ambiguous.append(trace["id"])
                            break
            # If no ambiguous scores found, just take first 10 PromoPlanner traces
            if not ambiguous:
                pp_traces = [t["id"] for t in all_traces if "PromoPlanner" in str(t.get("tags", []))][:10]
                ambiguous = pp_traces

            # Fetch trace ids already in the queue so re-runs don't add
            # duplicate items (the endpoint has no dedup of its own).
            existing_trace_ids: set[str] = set()
            if queue_id:
                page = 1
                while page <= 20:
                    try:
                        items_resp = httpx.get(
                            f"{host}/api/public/annotation-queues/{queue_id}/items",
                            auth=(env.langfuse_public_key, env.langfuse_secret_key),
                            params={"limit": 100, "page": page},
                            timeout=10,
                        )
                    except Exception:
                        break
                    if items_resp.status_code != 200:
                        break
                    items = items_resp.json().get("data", [])
                    if not items:
                        break
                    existing_trace_ids.update(
                        item["objectId"] for item in items if item.get("objectId")
                    )
                    page += 1

            # Add to queue, skipping any trace already present (idempotent).
            added = 0
            skipped = 0
            for trace_id in ambiguous[:10]:
                if not queue_id:
                    break
                if trace_id in existing_trace_ids:
                    skipped += 1
                    continue
                try:
                    q_resp = httpx.post(
                        f"{host}/api/public/annotation-queues/{queue_id}/items",
                        auth=(env.langfuse_public_key, env.langfuse_secret_key),
                        json={"traceId": trace_id},
                        timeout=10,
                    )
                    if q_resp.status_code in (200, 201):
                        added += 1
                        existing_trace_ids.add(trace_id)
                except Exception:
                    pass
            console.print(
                f"[green]Added {added} traces to annotation queue "
                f"({skipped} already present)[/green]"
            )
        else:
            console.print(f"[yellow]Could not fetch traces: {traces_resp.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Trace fetch failed: {e}[/yellow]")

    console.print(
        "\n[bold yellow]MANUAL (if queue not created automatically):[/bold yellow]\n"
        "1. Go to Langfuse UI -> Annotation Queues -> New Queue\n"
        f"2. Name it: '{queue_name}'\n"
        "3. Filter traces by: score 'response-factuality' between 0.6 and 0.8\n"
        "4. Add 10 traces to the queue for human review"
    )

    return 0


if __name__ == "__main__":
    sys.exit(seed_annotation_queue())
