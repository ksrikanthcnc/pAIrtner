"""Tests for chat import — format detection, normalization, extraction."""

import json
import pytest
from pairtner.core.chat_import import (
    _normalize_messages,
    _parse_ts,
    _week_key,
    _parse_import_facts,
    import_chat,
)
from tests.helpers import MockLLMClient
from pairtner.storage.schema import init_db


# ---------------------------------------------------------------------------
# Format detection + normalization
# ---------------------------------------------------------------------------

class TestNormalizeMessages:
    def test_native_format(self):
        raw = [
            {"from": "Alice", "text": "Hey!", "ts": "2024-01-15T10:30:00"},
            {"from": "Bob", "text": "Hi there", "ts": "2024-01-15T10:31:00"},
        ]
        result = _normalize_messages(raw)
        assert len(result) == 2
        assert result[0]["from"] == "Alice"
        assert result[0]["text"] == "Hey!"

    def test_telegram_format(self):
        raw = [
            {
                "from": "Alice",
                "from_id": "user123",
                "text": "Hello",
                "date": "2024-01-15T10:30:00",
                "text_entities": [],
            },
            {
                "from": "Bob",
                "from_id": "user456",
                "text": [{"type": "plain", "text": "Hi "}, "there"],
                "date": "2024-01-15T10:31:00",
                "text_entities": [],
            },
        ]
        result = _normalize_messages(raw)
        assert len(result) == 2
        assert result[1]["text"] == "Hi there"

    def test_sender_message_format(self):
        raw = [
            {"sender": "Alice", "message": "Hey", "date": "2024-01-15"},
            {"sender": "Bob", "message": "Yo", "date": "2024-01-16"},
        ]
        result = _normalize_messages(raw)
        assert len(result) == 2
        assert result[0]["from"] == "Alice"

    def test_empty_messages_filtered(self):
        raw = [
            {"from": "Alice", "text": "Hey!", "ts": "2024-01-15T10:30:00"},
            {"from": "Bob", "text": "", "ts": "2024-01-15T10:31:00"},
            {"from": "Alice", "text": "   ", "ts": "2024-01-15T10:32:00"},
        ]
        result = _normalize_messages(raw)
        assert len(result) == 1

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _normalize_messages([])

    def test_string_input(self):
        raw = json.dumps([{"from": "A", "text": "hi", "ts": ""}])
        result = _normalize_messages(raw)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_iso_format(self):
        dt = _parse_ts("2024-01-15T10:30:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1

    def test_unix_epoch(self):
        dt = _parse_ts("1705312200")
        assert dt is not None
        assert dt.year == 2024

    def test_date_time_format(self):
        dt = _parse_ts("2024-01-15 10:30:00")
        assert dt is not None

    def test_empty_returns_none(self):
        assert _parse_ts("") is None
        assert _parse_ts(None) is None

    def test_garbage_returns_none(self):
        assert _parse_ts("not-a-date") is None


# ---------------------------------------------------------------------------
# Week key
# ---------------------------------------------------------------------------

def test_week_key():
    from datetime import datetime, timezone
    dt = datetime(2024, 1, 15, tzinfo=timezone.utc)
    key = _week_key(dt)
    assert key.startswith("2024-W")


# ---------------------------------------------------------------------------
# Import facts parsing
# ---------------------------------------------------------------------------

class TestParseImportFacts:
    def test_direct_field(self):
        result = {"facts": [{"text": "Alice likes tea"}]}
        facts = _parse_import_facts(result)
        assert len(facts) == 1

    def test_from_reply_json(self):
        result = {
            "reply": json.dumps({"facts": [{"text": "Bob works late"}]}),
        }
        facts = _parse_import_facts(result)
        assert len(facts) == 1

    def test_no_facts(self):
        result = {"reply": "just text"}
        facts = _parse_import_facts(result)
        assert len(facts) == 0


# ---------------------------------------------------------------------------
# Full import (with mock LLM)
# ---------------------------------------------------------------------------

# test_db fixture comes from conftest.py


@pytest.mark.asyncio
async def test_import_chat_basic(test_db):
    """Import a small chat and verify facts are extracted."""
    llm = MockLLMClient(responses=[{
        "reply": json.dumps({
            "facts": [
                {"text": "Alice loves hiking", "visibility": "shared", "tag": "hobbies"},
                {"text": "Bob is stressed at work", "visibility": "private", "tag": "work"},
            ]
        }),
        "memory_ops": [],
        "new_facts": [],
        "relay_draft": None,
    }])

    messages = [
        {"from": "Alice", "text": "I went hiking this weekend, it was amazing!", "ts": "2024-01-15T10:00:00"},
        {"from": "Bob", "text": "Wish I could join, work has been crazy", "ts": "2024-01-15T10:01:00"},
        {"from": "Alice", "text": "You should take a break", "ts": "2024-01-15T10:02:00"},
    ]

    result = await import_chat(
        test_db, llm, 1001, messages,
        user_name="Alice", partner_name="Bob",
        consolidate=False,
    )

    assert result["status"] == "ok"
    assert result["total_messages"] == 3
    assert result["facts_extracted"] == 2


@pytest.mark.asyncio
async def test_import_chat_dry_run(test_db):
    """Dry run extracts but doesn't save."""
    llm = MockLLMClient(responses=[{
        "reply": json.dumps({
            "facts": [{"text": "Alice prefers tea", "visibility": "shared", "tag": "food"}]
        }),
        "memory_ops": [],
        "new_facts": [],
        "relay_draft": None,
    }])

    messages = [
        {"from": "Alice", "text": "I really prefer tea over coffee", "ts": "2024-06-01T10:00:00"},
    ]

    result = await import_chat(
        test_db, llm, 1001, messages,
        dry_run=True, consolidate=False,
    )

    assert result["status"] == "ok"
    assert result["facts_extracted"] == 1
    assert len(result["facts"]) == 1  # dry run returns facts


@pytest.mark.asyncio
async def test_import_empty_chat(test_db):
    """Empty input returns early."""
    llm = MockLLMClient()
    result = await import_chat(test_db, llm, 1001, [], consolidate=False)
    assert result["status"] == "empty"
    assert result["facts_extracted"] == 0
