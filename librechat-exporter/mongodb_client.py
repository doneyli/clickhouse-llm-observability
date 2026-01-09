"""
MongoDB Client for reading LibreChat conversations.

Queries the messages collection and pairs user questions with assistant responses.
"""

import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from pymongo import MongoClient


@dataclass
class ConversationPair:
    """A user question paired with the assistant's response."""
    conversation_id: str
    message_id: str
    user_message_id: str
    timestamp: datetime

    # User's question
    user_text: str

    # Assistant's response
    assistant_text: str
    assistant_thinking: Optional[str] = None

    # Metadata
    model: Optional[str] = None
    endpoint: Optional[str] = None
    user_token_count: Optional[int] = None
    assistant_token_count: Optional[int] = None

    # Tool calls (MCP, etc.)
    tool_calls: Optional[List[Dict]] = None


class LibreChatMongoClient:
    """Client for querying LibreChat conversations from MongoDB."""

    def __init__(
        self,
        uri: str = None,
        database: str = None,
    ):
        self.uri = uri or os.getenv("MONGO_URI", "mongodb://librechat-mongodb:27017/LibreChat")
        self.database_name = database or os.getenv("MONGO_DATABASE", "LibreChat")
        self._client = None
        self._db = None

    def connect(self):
        """Establish connection to MongoDB."""
        print(f"Connecting to MongoDB: {self.uri}")
        self._client = MongoClient(self.uri)
        self._db = self._client[self.database_name]

        # Test connection
        self._db.list_collection_names()
        print(f"Connected to MongoDB database: {self.database_name}")
        return self

    def get_conversation_pairs(
        self,
        hours_ago: int = 24,
        limit: int = 100,
        conversation_id: Optional[str] = None,
        since_message_id: Optional[str] = None,
    ) -> List[ConversationPair]:
        """
        Get conversation pairs (user question + assistant response).

        Args:
            hours_ago: How far back to look (default: 24 hours)
            limit: Max number of pairs to return
            conversation_id: Filter by specific conversation
            since_message_id: Only get messages after this ID (for incremental export)

        Returns:
            List of ConversationPair objects
        """
        if self._db is None:
            self.connect()

        messages_collection = self._db["messages"]

        # Build query
        query = {}

        # Time filter
        since_time = datetime.utcnow() - timedelta(hours=hours_ago)
        query["createdAt"] = {"$gte": since_time}

        # Conversation filter
        if conversation_id:
            query["conversationId"] = conversation_id

        # Get all messages in time range, sorted by conversation and time
        cursor = messages_collection.find(query).sort([
            ("conversationId", 1),
            ("createdAt", 1)
        ])

        # Group messages by conversation and pair user+assistant
        messages_by_conv: Dict[str, List[Dict]] = {}
        for msg in cursor:
            conv_id = msg.get("conversationId")
            if conv_id not in messages_by_conv:
                messages_by_conv[conv_id] = []
            messages_by_conv[conv_id].append(msg)

        # Create pairs
        pairs = []
        for conv_id, messages in messages_by_conv.items():
            # Find user messages and their following assistant responses
            for i, msg in enumerate(messages):
                if msg.get("isCreatedByUser"):
                    user_msg = msg
                    # Look for the next assistant message
                    assistant_msg = None
                    for j in range(i + 1, len(messages)):
                        if not messages[j].get("isCreatedByUser"):
                            assistant_msg = messages[j]
                            break

                    if assistant_msg:
                        pair = self._create_pair(user_msg, assistant_msg)
                        if pair:
                            pairs.append(pair)

        # Sort by timestamp descending and limit
        pairs.sort(key=lambda p: p.timestamp, reverse=True)
        pairs = pairs[:limit]

        print(f"Found {len(pairs)} conversation pairs")
        return pairs

    def _create_pair(self, user_msg: Dict, assistant_msg: Dict) -> Optional[ConversationPair]:
        """Create a ConversationPair from user and assistant messages."""

        user_text = user_msg.get("text", "")
        if not user_text:
            return None

        # Extract assistant response text
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

        # Fallback to text field if content is empty
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

    def get_recent_conversations(self, limit: int = 10) -> List[Dict]:
        """Get list of recent conversations with metadata."""
        if self._db is None:
            self.connect()

        conversations = self._db["conversations"]
        cursor = conversations.find().sort("updatedAt", -1).limit(limit)

        return [
            {
                "conversationId": c.get("conversationId"),
                "title": c.get("title"),
                "model": c.get("model"),
                "endpoint": c.get("endpoint"),
                "createdAt": c.get("createdAt"),
                "updatedAt": c.get("updatedAt"),
            }
            for c in cursor
        ]

    def get_message_count(self, hours_ago: int = 24) -> int:
        """Get count of messages in time range."""
        if self._db is None:
            self.connect()

        since_time = datetime.utcnow() - timedelta(hours=hours_ago)
        return self._db["messages"].count_documents({
            "createdAt": {"$gte": since_time}
        })

    def close(self):
        """Close the connection."""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None


if __name__ == "__main__":
    # Test the client
    client = LibreChatMongoClient()
    client.connect()

    print("\n=== Recent Conversations ===")
    convs = client.get_recent_conversations(5)
    for c in convs:
        print(f"  {c['title'][:50] if c.get('title') else 'Untitled'}...")
        print(f"    Model: {c.get('model')}, Updated: {c.get('updatedAt')}")

    print("\n=== Recent Conversation Pairs ===")
    pairs = client.get_conversation_pairs(hours_ago=24, limit=3)
    for pair in pairs:
        print(f"\n  User: {pair.user_text[:80]}...")
        print(f"  Assistant: {pair.assistant_text[:80]}...")
        print(f"  Model: {pair.model}, Tokens: {pair.assistant_token_count}")

    client.close()
