"""Tests for fact lifecycle: types, aging, archival, expiry, superseding."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from couples_bot.db import (
    add_fact,
    archive_fact,
    get_active_facts,
    get_expiring_facts,
    get_fact_by_id,
    get_facts,
    init_db,
    run_fact_aging,
    set_fact_expiry,
    supersede_fact,
)


@pytest.mark.asyncio
async def test_fact_type_column_exists(test_db):
    """Facts table has fact_type column with default 'stable'."""
    fid = await add_fact(test_db, 1001, "test fact", "private")
    fact = await get_fact_by_id(test_db, fid)
    assert fact is not None
    assert fact["fact_type"] == "stable"


@pytest.mark.asyncio
async def test_fact_type_values(test_db, paired_users):
    """Facts can be created with different fact_type values."""
    a_id, _ = paired_users
    t_id = await add_fact(test_db, a_id, "feeling tired", "private", fact_type="transient")
    s_id = await add_fact(test_db, a_id, "busy week ahead", "private", fact_type="situational")
    st_id = await add_fact(test_db, a_id, "loves coffee", "private", fact_type="stable")

    assert (await get_fact_by_id(test_db, t_id))["fact_type"] == "transient"
    assert (await get_fact_by_id(test_db, s_id))["fact_type"] == "situational"
    assert (await get_fact_by_id(test_db, st_id))["fact_type"] == "stable"


@pytest.mark.asyncio
async def test_get_active_facts_excludes_archived(test_db, paired_users):
    """get_active_facts should exclude archived facts."""
    a_id, _ = paired_users

    fid = await add_fact(test_db, a_id, "to be archived", "private")
    await archive_fact(test_db, fid)

    active = await get_active_facts(test_db, a_id)
    archived_ids = [f["id"] for f in active]
    assert fid not in archived_ids

    # Non-archived facts should still be present
    all_facts = await get_facts(test_db, a_id)
    assert any(f["id"] == fid for f in all_facts)


@pytest.mark.asyncio
async def test_get_active_facts_with_visibility(test_db, paired_users):
    """get_active_facts respects visibility filter."""
    a_id, _ = paired_users
    await add_fact(test_db, a_id, "private active", "private")
    await add_fact(test_db, a_id, "shared active", "shared")

    private = await get_active_facts(test_db, a_id, "private")
    shared = await get_active_facts(test_db, a_id, "shared")
    assert all(f["visibility"] == "private" for f in private)
    assert all(f["visibility"] == "shared" for f in shared)


@pytest.mark.asyncio
async def test_archive_fact(test_db, paired_users):
    """archive_fact sets archived_at."""
    a_id, _ = paired_users
    fid = await add_fact(test_db, a_id, "archivable", "private")
    await archive_fact(test_db, fid)

    fact = await get_fact_by_id(test_db, fid)
    assert fact["archived_at"] is not None


@pytest.mark.asyncio
async def test_set_fact_expiry(test_db, paired_users):
    """set_fact_expiry sets the expiry timestamp."""
    a_id, _ = paired_users
    fid = await add_fact(test_db, a_id, "expirable", "private")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await set_fact_expiry(test_db, fid, future)

    fact = await get_fact_by_id(test_db, fid)
    assert fact["expires_at"] == future


@pytest.mark.asyncio
async def test_get_expiring_facts(test_db, paired_users):
    """get_expiring_facts returns facts expiring within 24h."""
    a_id, _ = paired_users

    # Fact expiring in 12 hours (should be returned)
    fid_soon = await add_fact(test_db, a_id, "expiring soon", "private")
    soon = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    await set_fact_expiry(test_db, fid_soon, soon)

    # Fact expiring in 3 days (should NOT be returned)
    fid_later = await add_fact(test_db, a_id, "expiring later", "private")
    later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    await set_fact_expiry(test_db, fid_later, later)

    expiring = await get_expiring_facts(test_db, a_id)
    expiring_ids = [f["id"] for f in expiring]
    assert fid_soon in expiring_ids
    assert fid_later not in expiring_ids


@pytest.mark.asyncio
async def test_supersede_fact(test_db, paired_users):
    """supersede_fact archives old and links to new."""
    a_id, _ = paired_users
    old_id = await add_fact(test_db, a_id, "old version", "private")
    new_id = await add_fact(test_db, a_id, "new version", "private")

    await supersede_fact(test_db, old_id, new_id)

    old = await get_fact_by_id(test_db, old_id)
    assert old["archived_at"] is not None
    assert old["supersedes_id"] == new_id


@pytest.mark.asyncio
async def test_run_fact_aging_transient(test_db):
    """Transient facts older than 14 days are auto-archived."""
    import aiosqlite

    uid = 9001
    from couples_bot.db import create_user
    await create_user(test_db, uid, "TestUser")

    fid = await add_fact(test_db, uid, "old transient", "private", fact_type="transient")

    # Backdate the fact to 15 days ago
    old_date = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute(
            "UPDATE facts SET created_at = ? WHERE id = ?", (old_date, fid)
        )
        await db.commit()

    needs_confirm = await run_fact_aging(test_db)
    fact = await get_fact_by_id(test_db, fid)
    assert fact["archived_at"] is not None
    # Transient facts don't need confirmation
    assert fid not in [f["id"] for f in needs_confirm]


@pytest.mark.asyncio
async def test_run_fact_aging_situational(test_db):
    """Situational facts older than 30 days are returned for confirmation."""
    import aiosqlite

    uid = 9002
    from couples_bot.db import create_user
    await create_user(test_db, uid, "TestUser2")

    fid = await add_fact(test_db, uid, "old situational", "private", fact_type="situational")

    # Backdate to 31 days ago
    old_date = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute(
            "UPDATE facts SET created_at = ? WHERE id = ?", (old_date, fid)
        )
        await db.commit()

    needs_confirm = await run_fact_aging(test_db)
    fact = await get_fact_by_id(test_db, fid)
    # Situational facts are NOT auto-archived, they need confirmation
    assert fact["archived_at"] is None
    assert fid in [f["id"] for f in needs_confirm]


@pytest.mark.asyncio
async def test_run_fact_aging_stable_untouched(test_db):
    """Stable facts are never aged."""
    import aiosqlite

    uid = 9003
    from couples_bot.db import create_user
    await create_user(test_db, uid, "TestUser3")

    fid = await add_fact(test_db, uid, "stable fact", "private", fact_type="stable")

    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute(
            "UPDATE facts SET created_at = ? WHERE id = ?", (old_date, fid)
        )
        await db.commit()

    needs_confirm = await run_fact_aging(test_db)
    fact = await get_fact_by_id(test_db, fid)
    assert fact["archived_at"] is None
    assert fid not in [f["id"] for f in needs_confirm]
