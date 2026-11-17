"""Tests for GET /api/v1/history and GET /api/v1/facts endpoints."""
from __future__ import annotations

import pytest
from pairtner import db as db_mod


@pytest.mark.asyncio
async def test_get_full_history(test_db):
    """get_full_history returns messages oldest-first with timestamps."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.log_message(test_db, 1001, "user", "Hello!")
    await db_mod.log_message(test_db, 1001, "assistant", "Hi there!")
    await db_mod.log_message(test_db, 1001, "user", "How are you?")

    history = await db_mod.get_full_history(test_db, 1001)
    assert len(history) == 3
    # Oldest first
    assert history[0]["content"] == "Hello!"
    assert history[1]["content"] == "Hi there!"
    assert history[2]["content"] == "How are you?"
    # Has timestamps and IDs
    assert "created_at" in history[0]
    assert "id" in history[0]
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_get_full_history_limit(test_db):
    """get_full_history respects limit parameter."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    for i in range(20):
        await db_mod.log_message(test_db, 1001, "user", f"msg {i}")

    history = await db_mod.get_full_history(test_db, 1001, limit=5)
    assert len(history) == 5
    assert history[0]["content"] == "msg 0"  # oldest first


@pytest.mark.asyncio
async def test_get_full_history_offset(test_db):
    """get_full_history respects offset parameter."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    for i in range(10):
        await db_mod.log_message(test_db, 1001, "user", f"msg {i}")

    history = await db_mod.get_full_history(test_db, 1001, limit=3, offset=5)
    assert len(history) == 3
    assert history[0]["content"] == "msg 5"


@pytest.mark.asyncio
async def test_get_full_history_isolation(test_db):
    """History only returns messages for the authenticated user."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.ensure_user(test_db, 1002, "Bob")
    await db_mod.log_message(test_db, 1001, "user", "Alice's message")
    await db_mod.log_message(test_db, 1002, "user", "Bob's message")

    alice_history = await db_mod.get_full_history(test_db, 1001)
    assert len(alice_history) == 1
    assert alice_history[0]["content"] == "Alice's message"

    bob_history = await db_mod.get_full_history(test_db, 1002)
    assert len(bob_history) == 1
    assert bob_history[0]["content"] == "Bob's message"
