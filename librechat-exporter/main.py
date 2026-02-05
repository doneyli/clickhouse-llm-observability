#!/usr/bin/env python3
"""
LibreChat Exporter - Export conversations to ClickHouse via OTLP.

This service reads LibreChat conversations from MongoDB and exports them
as OpenTelemetry spans with gen_ai.* semantic conventions. The traces
are sent to ClickHouse via OTLP, making them available to:
- HyperDX for visualization and search
- langfuse-evaluator for quality evaluations

Usage:
    # List recent conversations
    python main.py --list-conversations

    # Export last 24 hours of conversations
    python main.py --hours 24

    # Export specific conversation
    python main.py --conversation-id abc123

    # Continuous export mode (polls every N seconds)
    python main.py --watch --interval 60
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Set

from mongodb_client import LibreChatMongoClient, ConversationPair
from otel_exporter import LibreChatOTLPExporter
from langfuse_exporter import LibreChatLangfuseExporter
from langfuse_config import is_langfuse_enabled


# File to persist exported message IDs (prevents duplicates across restarts)
EXPORTED_IDS_FILE = os.getenv("EXPORTED_IDS_FILE", "/tmp/librechat_exported_ids.json")


def load_exported_ids() -> Set[str]:
    """Load previously exported message IDs from file."""
    try:
        if Path(EXPORTED_IDS_FILE).exists():
            with open(EXPORTED_IDS_FILE, "r") as f:
                data = json.load(f)
                ids = set(data.get("exported_ids", []))
                print(f"Loaded {len(ids)} previously exported message IDs")
                return ids
    except Exception as e:
        print(f"Warning: Could not load exported IDs: {e}")
    return set()


def save_exported_ids(ids: Set[str]):
    """Save exported message IDs to file."""
    try:
        with open(EXPORTED_IDS_FILE, "w") as f:
            json.dump({"exported_ids": list(ids)}, f)
    except Exception as e:
        print(f"Warning: Could not save exported IDs: {e}")


def list_recent_conversations(client: LibreChatMongoClient, limit: int = 10):
    """List recent conversations from LibreChat."""
    print(f"\n{'='*60}")
    print("Recent Conversations")
    print('='*60)

    conversations = client.get_recent_conversations(limit)

    for conv in conversations:
        title = conv.get('title', 'Untitled')
        if title and len(title) > 50:
            title = title[:47] + "..."

        print(f"\n  ID: {conv.get('conversationId', 'N/A')}")
        print(f"  Title: {title or 'Untitled'}")
        print(f"  Model: {conv.get('model', 'N/A')}")
        print(f"  Updated: {conv.get('updatedAt', 'N/A')}")

    print(f"\n{'='*60}")
    print(f"Total: {len(conversations)} conversations")


def export_conversations(
    mongo_client: LibreChatMongoClient,
    otel_exporter: LibreChatOTLPExporter,
    langfuse_exporter: LibreChatLangfuseExporter = None,
    hours_ago: int = 24,
    limit: int = 100,
    conversation_id: str = None,
    exported_ids: Set[str] = None,
) -> int:
    """
    Export conversations from MongoDB to ClickHouse and Langfuse.

    Args:
        mongo_client: Connected MongoDB client
        otel_exporter: Configured OTLP exporter
        langfuse_exporter: Configured Langfuse exporter (optional)
        hours_ago: How far back to look for conversations
        limit: Maximum conversations to export
        conversation_id: Specific conversation to export (optional)
        exported_ids: Set of already-exported message IDs to skip

    Returns:
        Number of conversations exported
    """
    if exported_ids is None:
        exported_ids = set()

    # Get conversation pairs from MongoDB
    pairs = mongo_client.get_conversation_pairs(
        hours_ago=hours_ago,
        limit=limit,
        conversation_id=conversation_id,
    )

    # Filter out already-exported pairs
    new_pairs = [p for p in pairs if p.message_id not in exported_ids]

    if not new_pairs:
        print("No new conversations to export")
        return 0

    print(f"\nExporting {len(new_pairs)} conversation pairs...")

    # Export each pair
    exported_count = 0
    langfuse_count = 0
    for pair in new_pairs:
        try:
            # Export to OTLP/ClickStack
            trace_id = otel_exporter.export_conversation_pair(pair)
            exported_ids.add(pair.message_id)
            exported_count += 1

            # Also export to Langfuse if enabled
            if langfuse_exporter and langfuse_exporter.is_enabled():
                langfuse_trace_id = langfuse_exporter.export_conversation_pair(pair)
                if langfuse_trace_id:
                    langfuse_count += 1

            # Progress indicator
            user_preview = pair.user_text[:50] + "..." if len(pair.user_text) > 50 else pair.user_text
            print(f"  [{exported_count}/{len(new_pairs)}] {user_preview}")
            print(f"      → OTLP Trace: {trace_id[:16]}... Model: {pair.model or 'N/A'}")

        except Exception as e:
            print(f"  Error exporting pair {pair.message_id}: {e}")

    # Flush to ensure all spans are sent
    otel_exporter.flush()
    if langfuse_exporter:
        langfuse_exporter.flush()

    print(f"\nExported {exported_count} conversation pairs to ClickHouse")
    if langfuse_count > 0:
        print(f"Exported {langfuse_count} conversation pairs to Langfuse")
    return exported_count


def watch_mode(
    mongo_client: LibreChatMongoClient,
    otel_exporter: LibreChatOTLPExporter,
    langfuse_exporter: LibreChatLangfuseExporter = None,
    interval: int = 60,
    hours_ago: int = 1,
):
    """
    Continuously watch for new conversations and export them.

    Args:
        mongo_client: Connected MongoDB client
        otel_exporter: Configured OTLP exporter
        langfuse_exporter: Configured Langfuse exporter (optional)
        interval: Seconds between polls
        hours_ago: How far back to look each poll
    """
    print(f"\n{'='*60}")
    print(f"Watch Mode - Polling every {interval} seconds")
    print(f"Looking back {hours_ago} hour(s) each poll")
    if langfuse_exporter and langfuse_exporter.is_enabled():
        print("Langfuse export: ENABLED")
    print(f"Press Ctrl+C to stop")
    print('='*60)

    # Load previously exported IDs to avoid duplicates
    exported_ids: Set[str] = load_exported_ids()
    total_exported = 0

    try:
        while True:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{timestamp}] Checking for new conversations...")

            count = export_conversations(
                mongo_client=mongo_client,
                otel_exporter=otel_exporter,
                langfuse_exporter=langfuse_exporter,
                hours_ago=hours_ago,
                limit=100,
                exported_ids=exported_ids,
            )

            # Save exported IDs after each successful batch
            if count > 0:
                save_exported_ids(exported_ids)

            total_exported += count
            print(f"Total exported this session: {total_exported}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\nStopping watch mode. Total exported: {total_exported}")
        save_exported_ids(exported_ids)


def main():
    parser = argparse.ArgumentParser(
        description="Export LibreChat conversations to ClickHouse via OTLP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list-conversations           List recent conversations
  %(prog)s --hours 24                     Export last 24 hours
  %(prog)s --conversation-id abc123       Export specific conversation
  %(prog)s --watch --interval 60          Continuous export mode
        """
    )

    # Actions
    parser.add_argument(
        "--list-conversations",
        action="store_true",
        help="List recent conversations and exit"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously watch for new conversations"
    )

    # Filters
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="How many hours back to look (default: 24)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum conversations to export (default: 100)"
    )
    parser.add_argument(
        "--conversation-id",
        type=str,
        help="Export specific conversation by ID"
    )

    # Watch mode options
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between polls in watch mode (default: 60)"
    )

    # Connection options
    parser.add_argument(
        "--mongo-uri",
        type=str,
        default=os.getenv("MONGO_URI", "mongodb://librechat-mongodb:27017/LibreChat"),
        help="MongoDB connection URI"
    )
    parser.add_argument(
        "--otlp-endpoint",
        type=str,
        default=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://clickstack:4318/v1/traces"),
        help="OTLP endpoint URL"
    )
    parser.add_argument(
        "--service-name",
        type=str,
        default="librechat-conversations",
        help="Service name for traces (default: librechat-conversations)"
    )

    args = parser.parse_args()

    # Initialize clients
    print("LibreChat Exporter")
    print("="*60)

    mongo_client = LibreChatMongoClient(uri=args.mongo_uri)
    mongo_client.connect()

    # List mode - just show conversations and exit
    if args.list_conversations:
        list_recent_conversations(mongo_client, limit=10)
        mongo_client.close()
        return

    # Initialize OTLP exporter for export modes
    otel_exporter = LibreChatOTLPExporter(
        otlp_endpoint=args.otlp_endpoint,
        service_name=args.service_name,
    )
    otel_exporter.setup()

    # Initialize Langfuse exporter if configured
    langfuse_exporter = None
    if is_langfuse_enabled():
        langfuse_exporter = LibreChatLangfuseExporter()
        langfuse_exporter.setup()

    try:
        if args.watch:
            # Watch mode - continuous export
            watch_mode(
                mongo_client=mongo_client,
                otel_exporter=otel_exporter,
                langfuse_exporter=langfuse_exporter,
                interval=args.interval,
                hours_ago=min(args.hours, 2),  # Cap lookback in watch mode
            )
        else:
            # One-time export
            export_conversations(
                mongo_client=mongo_client,
                otel_exporter=otel_exporter,
                langfuse_exporter=langfuse_exporter,
                hours_ago=args.hours,
                limit=args.limit,
                conversation_id=args.conversation_id,
            )
    finally:
        otel_exporter.shutdown()
        if langfuse_exporter:
            langfuse_exporter.shutdown()
        mongo_client.close()


if __name__ == "__main__":
    main()
