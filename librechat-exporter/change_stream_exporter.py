#!/usr/bin/env python3
"""
Real-time LibreChat Exporter using MongoDB Change Streams.

Instead of polling, this watches MongoDB for new messages in real-time
and exports them to ClickHouse immediately.

Usage:
    python change_stream_exporter.py

This provides sub-second latency from LibreChat message → ClickHouse.
"""

import os
import signal
import sys
from datetime import datetime
from typing import Optional

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from mongodb_client import ConversationPair
from otel_exporter import LibreChatOTLPExporter


class ChangeStreamExporter:
    """Real-time exporter using MongoDB Change Streams."""

    def __init__(
        self,
        mongo_uri: str = None,
        database: str = None,
        otlp_endpoint: str = None,
        service_name: str = "librechat-conversations",
    ):
        self.mongo_uri = mongo_uri or os.getenv(
            "MONGO_URI", "mongodb://chat-mongodb:27017/LibreChat"
        )
        self.database_name = database or os.getenv("MONGO_DATABASE", "LibreChat")
        self.otlp_endpoint = otlp_endpoint
        self.service_name = service_name

        self._client = None
        self._db = None
        self._otel_exporter = None
        self._running = False

        # Track pending user messages (waiting for assistant response)
        self._pending_user_messages = {}  # messageId -> message doc

    def connect(self):
        """Connect to MongoDB and initialize OTLP exporter."""
        print(f"Connecting to MongoDB: {self.mongo_uri}")
        self._client = MongoClient(self.mongo_uri)
        self._db = self._client[self.database_name]

        # Test connection
        self._db.list_collection_names()
        print(f"Connected to MongoDB database: {self.database_name}")

        # Initialize OTLP exporter
        self._otel_exporter = LibreChatOTLPExporter(
            otlp_endpoint=self.otlp_endpoint,
            service_name=self.service_name,
        )
        self._otel_exporter.setup()

        return self

    def watch(self):
        """
        Watch for new messages using Change Streams.

        This provides real-time export with sub-second latency.
        """
        if self._db is None:
            self.connect()

        self._running = True
        messages_collection = self._db["messages"]

        print("\n" + "=" * 60)
        print("REAL-TIME CHANGE STREAM EXPORTER")
        print("=" * 60)
        print("Watching for new messages...")
        print("Press Ctrl+C to stop")
        print("=" * 60 + "\n")

        # Watch for inserts only (new messages)
        pipeline = [{"$match": {"operationType": "insert"}}]

        try:
            with messages_collection.watch(pipeline) as stream:
                for change in stream:
                    if not self._running:
                        break

                    self._handle_change(change)

        except PyMongoError as e:
            print(f"Change stream error: {e}")
            raise

    def _handle_change(self, change: dict):
        """Process a change stream event."""
        doc = change.get("fullDocument", {})

        if not doc:
            return

        is_user = doc.get("isCreatedByUser", False)
        conv_id = doc.get("conversationId", "")
        msg_id = doc.get("messageId", "")

        if is_user:
            # User message - store and wait for assistant response
            self._pending_user_messages[conv_id] = doc
            user_text = doc.get("text", "")[:50]
            print(f"[USER] {user_text}...")

        else:
            # Assistant message - pair with pending user message
            user_doc = self._pending_user_messages.pop(conv_id, None)

            if user_doc:
                pair = self._create_pair(user_doc, doc)
                if pair:
                    self._export_pair(pair)
            else:
                # No pending user message - might be a system message or we missed it
                print(f"[ASSISTANT] (no pending user message for {conv_id[:8]}...)")

    def _create_pair(self, user_msg: dict, assistant_msg: dict) -> Optional[ConversationPair]:
        """Create a ConversationPair from user and assistant messages."""
        user_text = user_msg.get("text", "")
        if not user_text:
            return None

        # Extract assistant response
        assistant_text = ""
        assistant_thinking = ""
        tool_calls = []

        content = assistant_msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "text":
                        text = item.get("text", "")
                        if text:
                            assistant_text += text + "\n"
                    elif item_type == "think":
                        assistant_thinking = item.get("think", "")
                    elif item_type == "tool_call":
                        tool_calls.append(item.get("tool_call", {}))

        # Fallback to text field
        if not assistant_text:
            assistant_text = assistant_msg.get("text", "")

        assistant_text = assistant_text.strip()
        if not assistant_text:
            return None

        return ConversationPair(
            conversation_id=user_msg.get("conversationId", ""),
            message_id=assistant_msg.get("messageId", ""),
            user_message_id=user_msg.get("messageId", ""),
            timestamp=assistant_msg.get("createdAt", datetime.utcnow()),
            user_text=user_text,
            assistant_text=assistant_text,
            assistant_thinking=assistant_thinking if assistant_thinking else None,
            model=assistant_msg.get("model"),
            endpoint=assistant_msg.get("endpoint"),
            user_token_count=user_msg.get("tokenCount"),
            assistant_token_count=assistant_msg.get("tokenCount"),
            tool_calls=tool_calls if tool_calls else None,
        )

    def _export_pair(self, pair: ConversationPair):
        """Export a conversation pair to ClickHouse."""
        try:
            trace_id = self._otel_exporter.export_conversation_pair(pair)
            self._otel_exporter.flush()

            user_preview = pair.user_text[:40] + "..." if len(pair.user_text) > 40 else pair.user_text
            print(f"[EXPORTED] {user_preview}")
            print(f"           → Trace: {trace_id[:16]}... Model: {pair.model or 'N/A'}")

        except Exception as e:
            print(f"[ERROR] Export failed: {e}")

    def stop(self):
        """Stop watching."""
        self._running = False
        print("\nStopping change stream exporter...")

    def close(self):
        """Clean up resources."""
        self.stop()
        if self._otel_exporter:
            self._otel_exporter.shutdown()
        if self._client:
            self._client.close()


def main():
    exporter = ChangeStreamExporter(
        mongo_uri=os.getenv("MONGO_URI"),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        service_name=os.getenv("OTEL_SERVICE_NAME", "librechat-conversations"),
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        exporter.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        exporter.connect()
        exporter.watch()
    except KeyboardInterrupt:
        pass
    finally:
        exporter.close()


if __name__ == "__main__":
    main()
