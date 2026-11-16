"""Database layer tests."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    add_fact,
    create_pending,
    create_user,
    delete_all_facts,
    delete_couple_config,
    delete_fact,
    ensure_user,
    get_couple_config,
    get_deliverable,
    get_facts,
    get_partner_last_active,
    get_pending,
    get_pending_count,
    get_recent_messages,
    get_shared_facts_for_pair,
    get_usage_count,
    get_usage_today,
    get_user,
    increment_usage,
    init_db,
    log_message,
    make_couple_id,
    mark_as_sent,
    set_couple_config,
    set_partner,
    set_user_paused,
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


# ---------------------------------------------------------------------------
# FEAT-002: pause/status helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_user_paused(test_db):
    """set_user_paused toggles the paused flag."""
    await create_user(test_db, 10, "X")

    user = await get_user(test_db, 10)
    assert user["paused"] == 0

    await set_user_paused(test_db, 10, True)
    user = await get_user(test_db, 10)
    assert user["paused"] == 1

    await set_user_paused(test_db, 10, False)
    user = await get_user(test_db, 10)
    assert user["paused"] == 0


@pytest.mark.asyncio
async def test_get_pending_count(test_db, paired_users):
    """get_pending_count counts rows in awaiting_approval or approved status."""
    a_id, b_id = paired_users

    assert await get_pending_count(test_db, a_id) == 0

    pid1 = await create_pending(test_db, a_id, b_id, "msg1")
    assert await get_pending_count(test_db, a_id) == 1

    pid2 = await create_pending(test_db, a_id, b_id, "msg2")
    assert await get_pending_count(test_db, a_id) == 2

    # Approve one — still counts
    await update_pending_status(test_db, pid1, "approved", "2030-01-01T00:00:00")
    assert await get_pending_count(test_db, a_id) == 2

    # Mark as sent — should drop
    await mark_as_sent(test_db, pid1)
    assert await get_pending_count(test_db, a_id) == 1

    # Discard the other — should drop
    await update_pending_status(test_db, pid2, "discarded")
    assert await get_pending_count(test_db, a_id) == 0


@pytest.mark.asyncio
async def test_get_usage_today(test_db):
    """get_usage_today sums across all models for today."""
    assert await get_usage_today(test_db) == 0

    await increment_usage(test_db, "model-a")
    await increment_usage(test_db, "model-a")
    await increment_usage(test_db, "model-b")

    assert await get_usage_today(test_db) == 3


@pytest.mark.asyncio
async def test_get_partner_last_active(test_db, paired_users):
    """get_partner_last_active returns the most recent message timestamp."""
    a_id, b_id = paired_users

    # paired_users fixture logs messages for b_id
    last = await get_partner_last_active(test_db, b_id)
    assert last is not None

    # Non-existent user returns None
    none_val = await get_partner_last_active(test_db, 99999)
    assert none_val is None


@pytest.mark.asyncio
async def test_paused_column_migration(tmp_path):
    """Existing DBs without 'paused' column get it via migration in init_db."""
    import aiosqlite

    db_path = str(tmp_path / "migrate.db")

    # Create a DB with the OLD schema (no paused column)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE users ("
            "tg_id INTEGER PRIMARY KEY, name TEXT, partner_id INTEGER, "
            "lang TEXT DEFAULT 'en', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        await db.execute("INSERT INTO users (tg_id, name) VALUES (1, 'Test')")
        await db.commit()

    # Run init_db — should add the paused column
    await init_db(db_path)

    user = await get_user(db_path, 1)
    assert user is not None
    assert user["paused"] == 0

    # Set and read back
    await set_user_paused(db_path, 1, True)
    user = await get_user(db_path, 1)
    assert user["paused"] == 1


# ---------------------------------------------------------------------------
# FEAT-003: couple_config CRUD and make_couple_id
# ---------------------------------------------------------------------------


class TestMakeCoupleId:
    def test_canonical_ordering(self):
        assert make_couple_id(100, 200) == "100:200"
        assert make_couple_id(200, 100) == "100:200"

    def test_same_user(self):
        assert make_couple_id(42, 42) == "42:42"

    def test_deterministic(self):
        assert make_couple_id(5, 3) == make_couple_id(3, 5)


@pytest.mark.asyncio
async def test_couple_config_set_and_get(test_db):
    """set_couple_config stores a value, get_couple_config retrieves it."""
    couple = "10:20"
    await set_couple_config(test_db, couple, "gemini_key", "encrypted-token")
    val = await get_couple_config(test_db, couple, "gemini_key")
    assert val == "encrypted-token"


@pytest.mark.asyncio
async def test_couple_config_upsert(test_db):
    """set_couple_config updates on conflict."""
    couple = "10:20"
    await set_couple_config(test_db, couple, "gemini_key", "old-value")
    await set_couple_config(test_db, couple, "gemini_key", "new-value")
    val = await get_couple_config(test_db, couple, "gemini_key")
    assert val == "new-value"


@pytest.mark.asyncio
async def test_couple_config_get_missing(test_db):
    """get_couple_config returns None for missing keys."""
    val = await get_couple_config(test_db, "999:888", "gemini_key")
    assert val is None


@pytest.mark.asyncio
async def test_couple_config_delete(test_db):
    """delete_couple_config removes the entry."""
    couple = "10:20"
    await set_couple_config(test_db, couple, "gemini_key", "some-token")
    assert await delete_couple_config(test_db, couple, "gemini_key") is True
    assert await get_couple_config(test_db, couple, "gemini_key") is None


@pytest.mark.asyncio
async def test_couple_config_delete_missing(test_db):
    """delete_couple_config returns False for non-existent key."""
    assert await delete_couple_config(test_db, "1:2", "nope") is False


@pytest.mark.asyncio
async def test_couple_config_multiple_keys(test_db):
    """Different config_key values for the same couple are independent."""
    couple = "10:20"
    await set_couple_config(test_db, couple, "gemini_key", "key-val")
    await set_couple_config(test_db, couple, "other_setting", "other-val")

    assert await get_couple_config(test_db, couple, "gemini_key") == "key-val"
    assert await get_couple_config(test_db, couple, "other_setting") == "other-val"

    # Deleting one doesn't affect the other
    await delete_couple_config(test_db, couple, "gemini_key")
    assert await get_couple_config(test_db, couple, "gemini_key") is None
    assert await get_couple_config(test_db, couple, "other_setting") == "other-val"


# ---------------------------------------------------------------------------
# FEAT-004: pending_batch CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_create_and_get(test_db, paired_users):
    """Create a batch, verify it's retrievable."""
    from couples_bot.db import (
        add_item_to_batch,
        create_or_get_active_batch,
        get_active_batch_for_sender,
        get_batch,
        get_batch_items,
    )

    a_id, b_id = paired_users

    batch = await create_or_get_active_batch(
        test_db, a_id, b_id, "2030-01-01T00:00:00"
    )
    assert batch is not None
    assert batch["sender_id"] == a_id
    assert batch["receiver_id"] == b_id
    assert batch["status"] == "accumulating"

    # Getting again returns the same batch
    batch2 = await create_or_get_active_batch(
        test_db, a_id, b_id, "2030-02-01T00:00:00"
    )
    assert batch2["id"] == batch["id"]

    # get_active_batch_for_sender also returns it
    active = await get_active_batch_for_sender(test_db, a_id, b_id)
    assert active is not None
    assert active["id"] == batch["id"]

    # get_batch by id
    fetched = await get_batch(test_db, batch["id"])
    assert fetched is not None
    assert fetched["id"] == batch["id"]


@pytest.mark.asyncio
async def test_batch_items(test_db, paired_users):
    """Add items to a batch, verify retrieval."""
    from couples_bot.db import (
        add_item_to_batch,
        create_or_get_active_batch,
        get_batch_item,
        get_batch_items,
    )

    a_id, b_id = paired_users

    batch = await create_or_get_active_batch(
        test_db, a_id, b_id, "2030-01-01T00:00:00"
    )

    item1_id = await add_item_to_batch(test_db, batch["id"], "First draft")
    item2_id = await add_item_to_batch(test_db, batch["id"], "Second draft")

    items = await get_batch_items(test_db, batch["id"])
    assert len(items) == 2
    assert items[0]["draft"] == "First draft"
    assert items[1]["draft"] == "Second draft"
    assert items[0]["status"] == "pending"

    # get_batch_item by id
    item = await get_batch_item(test_db, item1_id)
    assert item is not None
    assert item["draft"] == "First draft"


@pytest.mark.asyncio
async def test_batch_status_transitions(test_db, paired_users):
    """Batch status can be updated."""
    from couples_bot.db import (
        create_or_get_active_batch,
        get_batch,
        update_batch_status,
    )

    a_id, b_id = paired_users

    batch = await create_or_get_active_batch(
        test_db, a_id, b_id, "2030-01-01T00:00:00"
    )
    assert batch["status"] == "accumulating"

    await update_batch_status(test_db, batch["id"], "reviewing")
    updated = await get_batch(test_db, batch["id"])
    assert updated["status"] == "reviewing"


@pytest.mark.asyncio
async def test_batch_item_status_with_edit(test_db, paired_users):
    """Updating item status with an edited_draft."""
    from couples_bot.db import (
        add_item_to_batch,
        create_or_get_active_batch,
        get_batch_item,
        update_batch_item_status,
    )

    a_id, b_id = paired_users

    batch = await create_or_get_active_batch(
        test_db, a_id, b_id, "2030-01-01T00:00:00"
    )
    item_id = await add_item_to_batch(test_db, batch["id"], "Original")

    await update_batch_item_status(test_db, item_id, "pending", "Edited version")
    item = await get_batch_item(test_db, item_id)
    assert item["status"] == "pending"
    assert item["edited_draft"] == "Edited version"

    await update_batch_item_status(test_db, item_id, "approved")
    item = await get_batch_item(test_db, item_id)
    assert item["status"] == "approved"


@pytest.mark.asyncio
async def test_reset_batch_timer(test_db, paired_users):
    """reset_batch_timer updates send_after."""
    from couples_bot.db import (
        create_or_get_active_batch,
        get_batch,
        reset_batch_timer,
    )

    a_id, b_id = paired_users

    batch = await create_or_get_active_batch(
        test_db, a_id, b_id, "2030-01-01T00:00:00"
    )
    assert batch["send_after"] == "2030-01-01T00:00:00"

    await reset_batch_timer(test_db, batch["id"], "2030-06-01T00:00:00")
    updated = await get_batch(test_db, batch["id"])
    assert updated["send_after"] == "2030-06-01T00:00:00"


@pytest.mark.asyncio
async def test_get_deliverable_batches(test_db, paired_users):
    """get_deliverable_batches returns the right batches."""
    from couples_bot.db import (
        add_item_to_batch,
        create_or_get_active_batch,
        get_deliverable_batches,
        update_batch_status,
    )

    a_id, b_id = paired_users

    # Batch 1: accumulating with past send_after → should be deliverable
    batch1 = await create_or_get_active_batch(
        test_db, a_id, b_id, "2020-01-01T00:00:00"
    )
    await add_item_to_batch(test_db, batch1["id"], "Draft 1")

    deliverable = await get_deliverable_batches(test_db)
    assert len(deliverable) == 1
    assert deliverable[0]["id"] == batch1["id"]

    # Change to reviewing — no longer deliverable
    await update_batch_status(test_db, batch1["id"], "reviewing")
    deliverable = await get_deliverable_batches(test_db)
    assert len(deliverable) == 0

    # Change to approved — deliverable again (no timer check)
    await update_batch_status(test_db, batch1["id"], "approved")
    deliverable = await get_deliverable_batches(test_db)
    assert len(deliverable) == 1


# ---------------------------------------------------------------------------
# FEAT-004: rate_limit CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_check_no_record(test_db):
    """check_rate_limit with no existing record returns allowed."""
    from couples_bot.db import check_rate_limit

    allowed, remaining = await check_rate_limit(test_db, 999, "minute", 10, 60)
    assert allowed is True
    assert remaining == 10


@pytest.mark.asyncio
async def test_rate_limit_increment_and_check(test_db):
    """Incrementing the counter, then checking."""
    from couples_bot.db import check_rate_limit, increment_rate_counter

    uid = 42
    await increment_rate_counter(test_db, uid, "minute", 60)
    allowed, remaining = await check_rate_limit(test_db, uid, "minute", 3, 60)
    assert allowed is True
    assert remaining == 2

    await increment_rate_counter(test_db, uid, "minute", 60)
    await increment_rate_counter(test_db, uid, "minute", 60)

    allowed, remaining = await check_rate_limit(test_db, uid, "minute", 3, 60)
    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_rate_limit_window_expired(test_db):
    """After the window expires, the counter resets."""
    from couples_bot.db import check_rate_limit, increment_rate_counter

    uid = 42
    # Use a 1-second window so it expires quickly
    await increment_rate_counter(test_db, uid, "short", 1)
    await increment_rate_counter(test_db, uid, "short", 1)

    import asyncio
    await asyncio.sleep(1.1)

    allowed, remaining = await check_rate_limit(test_db, uid, "short", 2, 1)
    assert allowed is True
    assert remaining == 2


@pytest.mark.asyncio
async def test_rate_limit_separate_windows(test_db):
    """Minute and day windows are independent."""
    from couples_bot.db import check_rate_limit, increment_rate_counter

    uid = 42
    await increment_rate_counter(test_db, uid, "minute", 60)
    await increment_rate_counter(test_db, uid, "minute", 60)
    await increment_rate_counter(test_db, uid, "day", 86400)

    allowed_m, remaining_m = await check_rate_limit(test_db, uid, "minute", 3, 60)
    allowed_d, remaining_d = await check_rate_limit(test_db, uid, "day", 200, 86400)

    assert allowed_m is True
    assert remaining_m == 1
    assert allowed_d is True
    assert remaining_d == 199
