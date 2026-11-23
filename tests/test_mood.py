"""Mood command tests — /mood set, show, expiry, override."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pairtner.db import (
    clear_expired_moods,
    create_user,
    ensure_user_profile,
    get_active_mood,
    set_user_mood,
)


@pytest.mark.asyncio
async def test_mood_expiry(test_db):
    """Expired moods are not returned by get_active_mood."""
    await create_user(test_db, 1, "X")
    await ensure_user_profile(test_db, 1)

    # Expired mood
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await set_user_mood(test_db, 1, "angry", past)

    mood = await get_active_mood(test_db, 1)
    assert mood is None


@pytest.mark.asyncio
async def test_clear_expired_moods(test_db):
    """clear_expired_moods removes old entries."""
    await create_user(test_db, 1, "X")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    await set_user_mood(test_db, 1, "angry", past)
    await set_user_mood(test_db, 1, "happy", future)

    deleted = await clear_expired_moods(test_db)
    assert deleted == 1

    # Active mood still present
    mood = await get_active_mood(test_db, 1)
    assert mood is not None
    assert mood["mood"] == "happy"


@pytest.mark.asyncio
async def test_mood_overrides_dampening(test_db, paired_users):
    """Active mood's dampening_override is stored correctly."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    await set_user_mood(
        test_db, a_id, "stressed", expires,
        dampening_override=0.9, response_length="short",
    )

    mood = await get_active_mood(test_db, a_id)
    assert mood["dampening_override"] == 0.9
    assert mood["response_length"] == "short"
