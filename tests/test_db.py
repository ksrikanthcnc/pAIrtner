"""Database layer tests."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    add_fact,
    create_pending,
    create_user,
    delete_all_facts,
    delete_fact,
    ensure_user,
    get_deliverable,
    get_facts,
    get_pending,
    get_recent_messages,
    get_shared_facts_for_pair,
    get_usage_count,
    get_user,
    increment_usage,
    init_db,
    log_message,
    mark_as_sent,
    set_partner,
    update_pending_edited_draft,
    update_pending_status,
)


@pytest.mark.asyncio
async def test_schema_creation(tmp_path):
    """init_db creates all tables and indexes without errors."""
    db = str(tmp_path / "schema.db")
    await init_db(db)
    # Running it again should be idempotent
    await init_db(db)

    # Verify tables exist by trying to query them
    user = await get_user(db, 999)
    assert user is None

    facts = await get_facts(db, 999)
    assert facts == []


@pytest.mark.asyncio
async def test_user_crud(test_db):
    await create_user(test_db, 42, "TestUser")
    user = await get_user(test_db, 42)
    assert user is not None
    assert user["name"] == "TestUser"
    assert user["lang"] == "en"
    assert user["partner_id"] is None


@pytest.mark.asyncio
async def test_ensure_user_creates_if_missing(test_db):
    user = await ensure_user(test_db, 55, "New")
    assert user["tg_id"] == 55
    assert user["name"] == "New"

    # Calling again returns existing
    user2 = await ensure_user(test_db, 55, "Different")
    assert user2["name"] == "New"  # Name not updated


@pytest.mark.asyncio
async def test_pairing(test_db):
    await create_user(test_db, 1, "A")
    await create_user(test_db, 2, "B")
    await set_partner(test_db, 1, 2)

    a = await get_user(test_db, 1)
    b = await get_user(test_db, 2)
    assert a["partner_id"] == 2
    assert b["partner_id"] == 1


@pytest.mark.asyncio
async def test_fact_crud(test_db):
    await create_user(test_db, 10, "X")

    fid = await add_fact(test_db, 10, "Likes coffee", "private")
    assert fid is not None

    facts = await get_facts(test_db, 10)
    assert len(facts) == 1
    assert facts[0]["text"] == "Likes coffee"
    assert facts[0]["visibility"] == "private"

    # Delete
    ok = await delete_fact(test_db, fid, 10)
    assert ok is True

    facts = await get_facts(test_db, 10)
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_delete_fact_wrong_owner(test_db):
    await create_user(test_db, 10, "X")
    await create_user(test_db, 20, "Y")

    fid = await add_fact(test_db, 10, "Secret", "private")

    # Wrong owner should fail
    ok = await delete_fact(test_db, fid, 20)
    assert ok is False

    # Fact still exists
    facts = await get_facts(test_db, 10)
    assert len(facts) == 1


@pytest.mark.asyncio
async def test_delete_all_facts(test_db):
    await create_user(test_db, 10, "X")
    await add_fact(test_db, 10, "A", "private")
    await add_fact(test_db, 10, "B", "shared")

    count = await delete_all_facts(test_db, 10)
    assert count == 2

    facts = await get_facts(test_db, 10)
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_shared_facts_for_pair(test_db, paired_users):
    a_id, b_id = paired_users

    shared = await get_shared_facts_for_pair(test_db, a_id, b_id)
    texts = [f["text"] for f in shared]
    assert "We agreed to cook together on weekends" in texts
    assert "We decided to try date nights" in texts
    assert len(shared) == 2


@pytest.mark.asyncio
async def test_pending_crud(test_db, paired_users):
    a_id, b_id = paired_users

    pid = await create_pending(test_db, a_id, b_id, "Hey babe")
    pending = await get_pending(test_db, pid)
    assert pending is not None
    assert pending["status"] == "awaiting_approval"
    assert pending["draft"] == "Hey babe"


@pytest.mark.asyncio
async def test_pending_edit_draft(test_db, paired_users):
    a_id, b_id = paired_users

    pid = await create_pending(test_db, a_id, b_id, "Original")
    await update_pending_edited_draft(test_db, pid, "Edited version")

    pending = await get_pending(test_db, pid)
    assert pending["edited_draft"] == "Edited version"


@pytest.mark.asyncio
async def test_message_log(test_db):
    await create_user(test_db, 10, "X")

    await log_message(test_db, 10, "user", "Hello")
    await log_message(test_db, 10, "assistant", "Hi there!")

    msgs = await get_recent_messages(test_db, 10)
    assert len(msgs) == 2
    # Most recent first
    assert msgs[0]["content"] == "Hi there!"
    assert msgs[1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_usage_counter(test_db):
    count = await get_usage_count(test_db, "test-model")
    assert count == 0

    await increment_usage(test_db, "test-model")
    await increment_usage(test_db, "test-model")

    count = await get_usage_count(test_db, "test-model")
    assert count == 2


@pytest.mark.asyncio
async def test_deliverable_only_approved_past(test_db, paired_users):
    """Only approved rows with send_after in the past should be deliverable."""
    a_id, b_id = paired_users

    pid = await create_pending(test_db, a_id, b_id, "Test msg")

    # Not deliverable while awaiting
    assert await get_deliverable(test_db) == []

    # Approve with send_after in the past
    await update_pending_status(test_db, pid, "approved", "2020-01-01T00:00:00")
    deliverable = await get_deliverable(test_db)
    assert len(deliverable) == 1
    assert deliverable[0]["id"] == pid

    # Mark sent
    await mark_as_sent(test_db, pid)
    assert await get_deliverable(test_db) == []

    pending = await get_pending(test_db, pid)
    assert pending["status"] == "sent"
    assert pending["sent_at"] is not None
