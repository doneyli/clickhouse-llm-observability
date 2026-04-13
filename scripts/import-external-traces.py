#!/usr/bin/env python3
"""
Import External Traces into the Demo Environment

Fetches traces from an external Langfuse instance (e.g., a Claude Code
observability setup, a production instance, or another demo) and re-ingests
them into this demo's Langfuse instance via the batch ingestion API.

This is useful for:
  - Hydrating the demo with real Claude Code coding session traces
  - Importing production traces for evaluation and dataset creation
  - Consolidating traces from multiple Langfuse instances into one demo

Usage:
    python import-external-traces.py                          # Import up to 50 traces
    python import-external-traces.py --limit 20 --scrub       # Import 20, redact paths/code
    python import-external-traces.py --dry-run --verbose       # Preview without ingesting
    python import-external-traces.py --since 2026-04-01        # Only recent traces
    python import-external-traces.py --tag my-project          # Filter by tag

Environment variables:
    SOURCE_LANGFUSE_HOST         Source instance URL (default: http://localhost:3050)
    SOURCE_LANGFUSE_PUBLIC_KEY   Source project public key (required)
    SOURCE_LANGFUSE_SECRET_KEY   Source project secret key (required)

    TARGET_LANGFUSE_HOST         Target instance URL (default: http://localhost:3001)
    TARGET_LANGFUSE_PUBLIC_KEY   Target project public key (default: pk-lf-1234567890)
    TARGET_LANGFUSE_SECRET_KEY   Target project secret key (default: sk-lf-1234567890)

Prerequisites:
    pip install 'langfuse>=3.0,<4.0' requests
"""

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Error: requests package not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Import traces from an external Langfuse instance into this demo environment"
    )
    parser.add_argument("--limit", type=int, default=50,
                        help="Max traces to import (default: 50)")
    parser.add_argument("--tag", type=str, default=None,
                        help="Filter source traces by tag (e.g., 'claude-code')")
    parser.add_argument("--add-tag", type=str, default=None,
                        help="Additional tag to add to imported traces (e.g., 'imported-demo')")
    parser.add_argument("--since", type=str, default=None,
                        help="Only traces after this date (YYYY-MM-DD)")
    parser.add_argument("--scrub", action="store_true",
                        help="Redact file paths and long code blocks from trace content")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and transform but do not ingest into target")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Events per ingestion batch (default: 50)")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")
    return parser.parse_args()


# --------------- Source Client ---------------

def create_source_client():
    host = os.getenv("SOURCE_LANGFUSE_HOST", "http://localhost:3050")
    pk = os.getenv("SOURCE_LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("SOURCE_LANGFUSE_SECRET_KEY")

    if not pk or not sk:
        print("Error: SOURCE_LANGFUSE_PUBLIC_KEY and SOURCE_LANGFUSE_SECRET_KEY are required.", file=sys.stderr)
        print("", file=sys.stderr)
        print("  Set these to the API keys of the source Langfuse project.", file=sys.stderr)
        print("  For a self-hosted instance, extract them with:", file=sys.stderr)
        print("    docker exec <langfuse-web-container> printenv LANGFUSE_INIT_PROJECT_PUBLIC_KEY", file=sys.stderr)
        print("    docker exec <langfuse-web-container> printenv LANGFUSE_INIT_PROJECT_SECRET_KEY", file=sys.stderr)
        sys.exit(1)

    return Langfuse(public_key=pk, secret_key=sk, host=host)


# --------------- Target Session ---------------

def create_target_session():
    host = os.getenv("TARGET_LANGFUSE_HOST", "http://localhost:3001")
    pk = os.getenv("TARGET_LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
    sk = os.getenv("TARGET_LANGFUSE_SECRET_KEY", "sk-lf-1234567890")

    session = requests.Session()
    session.auth = (pk, sk)
    session.headers.update({"Content-Type": "application/json"})
    return session, host


# --------------- Fetch ---------------

def fetch_traces(client, tag_filter, since_date, limit, verbose=False):
    """Fetch traces from source with pagination, filtering by tag and date."""
    traces = []
    page = 1
    while True:
        batch = client.api.trace.list(limit=100, page=page)
        if not batch.data:
            break
        for t in batch.data:
            if tag_filter and tag_filter not in (t.tags or []):
                continue
            if since_date and t.timestamp:
                trace_date = t.timestamp
                if hasattr(trace_date, "date"):
                    trace_date = trace_date.date()
                if trace_date < since_date:
                    continue
            traces.append(t)
            if limit and len(traces) >= limit:
                break
        if limit and len(traces) >= limit:
            traces = traces[:limit]
            break
        if page >= batch.meta.total_pages:
            break
        page += 1
        if verbose and page % 5 == 0:
            print(f"  Fetching traces... page {page}/{batch.meta.total_pages}", file=sys.stderr)
    return traces


def fetch_observations(client, trace_id):
    """Fetch all observations for a single trace."""
    observations = []
    page = 1
    while True:
        batch = client.api.observations.get_many(trace_id=trace_id, limit=100, page=page)
        if not batch.data:
            break
        observations.extend(batch.data)
        if page >= batch.meta.total_pages:
            break
        page += 1
    return observations


# --------------- Scrubbing ---------------

PATH_PATTERN = re.compile(
    r'(/Users/[^\s"\'`,\]})]+|/home/[^\s"\'`,\]})]+|C:\\[^\s"\'`,\]})]+)',
    re.IGNORECASE
)

CODE_INDICATORS = ["def ", "function ", "class ", "import ", "const ", "let ", "var ",
                    "return ", "if (", "for (", "while (", "try:", "except:", "catch (",
                    "async ", "await ", "module.exports", "export default"]


def scrub_content(obj):
    """Recursively scrub file paths and long code blocks from a dict/list/string."""
    if obj is None:
        return obj
    if isinstance(obj, str):
        result = PATH_PATTERN.sub("<redacted-path>", obj)
        if len(result) > 300 and any(ind in result for ind in CODE_INDICATORS):
            result = "<redacted-code-block>"
        return result
    if isinstance(obj, dict):
        return {k: scrub_content(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_content(item) for item in obj]
    return obj


# --------------- Transform ---------------

def to_serializable(obj):
    """Convert SDK objects to JSON-serializable dicts."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_serializable(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return {k: to_serializable(v) for k, v in obj.__dict__.items()
                if not k.startswith("_")}
    return str(obj)


def transform_trace(trace, observations, do_scrub, add_tag=None):
    """Transform a trace + observations into ingestion batch events."""
    events = []
    now = datetime.now(timezone.utc).isoformat()

    trace_input = to_serializable(trace.input)
    trace_output = to_serializable(trace.output)
    trace_metadata = to_serializable(trace.metadata) if trace.metadata else {}
    trace_tags = list(trace.tags or [])

    if do_scrub:
        trace_input = scrub_content(trace_input)
        trace_output = scrub_content(trace_output)
        trace_metadata = scrub_content(trace_metadata)

    # Add import marker
    if add_tag and add_tag not in trace_tags:
        trace_tags.append(add_tag)
    if "imported" not in trace_tags:
        trace_tags.append("imported")
    trace_metadata["imported"] = True
    trace_metadata["import_source"] = os.getenv("SOURCE_LANGFUSE_HOST", "http://localhost:3050")

    # Trace-create event
    trace_body = {
        "id": trace.id,
        "name": trace.name,
        "sessionId": trace.session_id,
        "timestamp": trace.timestamp.isoformat() if trace.timestamp else now,
        "input": trace_input,
        "output": trace_output,
        "tags": trace_tags,
        "metadata": trace_metadata,
        "userId": trace.user_id or "imported",
    }
    events.append({
        "id": str(uuid.uuid4()),
        "type": "trace-create",
        "timestamp": now,
        "body": trace_body,
    })

    # Observation events
    for obs in observations:
        obs_type = getattr(obs, "type", "SPAN")
        if obs_type == "GENERATION":
            event_type = "generation-create"
        elif obs_type == "EVENT":
            event_type = "event-create"
        else:
            event_type = "span-create"

        obs_input = to_serializable(obs.input)
        obs_output = to_serializable(obs.output)
        obs_metadata = to_serializable(obs.metadata) if obs.metadata else {}

        if do_scrub:
            obs_input = scrub_content(obs_input)
            obs_output = scrub_content(obs_output)
            obs_metadata = scrub_content(obs_metadata)

        obs_body = {
            "id": obs.id,
            "traceId": trace.id,
            "name": obs.name,
            "startTime": obs.start_time.isoformat() if obs.start_time else now,
            "input": obs_input,
            "output": obs_output,
            "metadata": obs_metadata,
        }

        if obs.end_time:
            obs_body["endTime"] = obs.end_time.isoformat()
        if hasattr(obs, "parent_observation_id") and obs.parent_observation_id:
            obs_body["parentObservationId"] = obs.parent_observation_id

        # Generation-specific fields
        if event_type == "generation-create":
            if hasattr(obs, "model") and obs.model:
                obs_body["model"] = obs.model
            if hasattr(obs, "model_parameters") and obs.model_parameters:
                obs_body["modelParameters"] = to_serializable(obs.model_parameters)
            if hasattr(obs, "usage") and obs.usage:
                obs_body["usage"] = to_serializable(obs.usage)
            if hasattr(obs, "usage_details") and obs.usage_details:
                obs_body["usageDetails"] = to_serializable(obs.usage_details)
            if hasattr(obs, "cost_details") and obs.cost_details:
                obs_body["costDetails"] = to_serializable(obs.cost_details)

        events.append({
            "id": str(uuid.uuid4()),
            "type": event_type,
            "timestamp": now,
            "body": obs_body,
        })

    return events


# --------------- Ingest ---------------

def ingest_batch(session, target_host, events, verbose=False):
    """POST a batch of events to the target Langfuse ingestion API."""
    url = f"{target_host}/api/public/ingestion"
    payload = {"batch": events}

    try:
        resp = session.post(url, json=payload, timeout=30)
    except requests.exceptions.ConnectionError as e:
        print(f"  Connection error to target: {e}", file=sys.stderr)
        return 0, len(events)

    if resp.status_code in (200, 207):
        data = resp.json()
        successes = len(data.get("successes", []))
        errors = data.get("errors", [])
        if errors and verbose:
            for err in errors[:5]:
                print(f"  Ingestion error: {err}", file=sys.stderr)
        return successes, len(errors)
    else:
        print(f"  Ingestion failed: HTTP {resp.status_code} - {resp.text[:200]}", file=sys.stderr)
        return 0, len(events)


# --------------- Main ---------------

def main():
    args = parse_args()

    since_date = None
    if args.since:
        try:
            since_date = datetime.strptime(args.since, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: --since must be YYYY-MM-DD format, got '{args.since}'", file=sys.stderr)
            sys.exit(1)

    source_host = os.getenv("SOURCE_LANGFUSE_HOST", "http://localhost:3050")
    target_host = os.getenv("TARGET_LANGFUSE_HOST", "http://localhost:3001")

    print("Langfuse Trace Import", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Source:     {source_host}", file=sys.stderr)
    print(f"  Target:     {target_host}", file=sys.stderr)
    print(f"  Limit:      {args.limit}", file=sys.stderr)
    print(f"  Tag filter: {args.tag or '(none)'}", file=sys.stderr)
    print(f"  Add tag:    {args.add_tag or '(none)'}", file=sys.stderr)
    print(f"  Since:      {args.since or '(all time)'}", file=sys.stderr)
    print(f"  Scrub:      {args.scrub}", file=sys.stderr)

    if args.dry_run:
        print(f"\n  ** DRY RUN - no data will be ingested **", file=sys.stderr)

    # Connect
    source = create_source_client()
    session, target_host = create_target_session()

    # Fetch
    print(f"\nFetching traces...", file=sys.stderr)
    traces = fetch_traces(source, args.tag, since_date, args.limit, args.verbose)
    print(f"  Found {len(traces)} traces matching filters", file=sys.stderr)

    if not traces:
        print("\nNo traces to import.", file=sys.stderr)
        return

    # Transform
    all_events = []
    total_observations = 0

    for i, trace in enumerate(traces):
        if args.verbose:
            print(f"  [{i+1}/{len(traces)}] {trace.id} ({trace.name})", file=sys.stderr)

        observations = fetch_observations(source, trace.id)
        total_observations += len(observations)
        events = transform_trace(trace, observations, args.scrub, args.add_tag)
        all_events.extend(events)

        if (i + 1) % 10 == 0:
            print(f"  Processed {i+1}/{len(traces)} traces...", file=sys.stderr)

    print(f"\nTransformed: {len(traces)} traces, {total_observations} observations -> {len(all_events)} events", file=sys.stderr)

    if args.scrub:
        print("  Scrubbing: file paths and code blocks redacted", file=sys.stderr)

    # Ingest
    if args.dry_run:
        print(f"\nDry run complete. Would ingest {len(all_events)} events.", file=sys.stderr)
        if args.verbose and all_events:
            print("\nSample event:", file=sys.stderr)
            print(json.dumps(all_events[0], indent=2, default=str), file=sys.stderr)
        return

    total_success = 0
    total_errors = 0

    for batch_start in range(0, len(all_events), args.batch_size):
        batch = all_events[batch_start:batch_start + args.batch_size]
        batch_num = (batch_start // args.batch_size) + 1
        total_batches = (len(all_events) + args.batch_size - 1) // args.batch_size

        print(f"  Ingesting batch {batch_num}/{total_batches} ({len(batch)} events)...", file=sys.stderr)
        successes, errors = ingest_batch(session, target_host, batch, args.verbose)
        total_success += successes
        total_errors += errors

    # Summary
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"Import complete:", file=sys.stderr)
    print(f"  Traces:       {len(traces)}", file=sys.stderr)
    print(f"  Observations: {total_observations}", file=sys.stderr)
    print(f"  Events sent:  {len(all_events)}", file=sys.stderr)
    print(f"  Successes:    {total_success}", file=sys.stderr)
    print(f"  Errors:       {total_errors}", file=sys.stderr)

    if total_errors > 0:
        print(f"\n  Some events failed. Re-run with --verbose for details.", file=sys.stderr)

    filter_tag = args.add_tag or "imported"
    print(f"\nVerify in Langfuse UI: {target_host}", file=sys.stderr)
    print(f"  Filter traces by tag: {filter_tag}", file=sys.stderr)


if __name__ == "__main__":
    main()
