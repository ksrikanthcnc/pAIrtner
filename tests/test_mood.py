"""Mood command tests — /mood set, show, expiry, override."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from couples_bot.db import (
    clear_expired_moods,
    create_user,
    ensure_user_profile,
    get_active_mood,
    set_user_mood,
)


@pytest.mark.asyncio
async def test_mood_set(test_db, service, paired_users):
    """/mood stressed sets the mood with confirmation."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    responses = await service.handle_mood(a_id, "stressed")
    assert len(responses) == 1
    assert "stressed" in responses[0].text.lower()

    mood = await get_active_mood(test_db, a_id)
    assert mood is not None
    assert mood["mood"] == "stressed"


@pytest.mark.asyncio
async def test_mood_show_current(test_db, service, paired_users):
    """/mood with no arg shows the current mood."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    # Set a mood first
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    await set_user_mood(test_db, a_id, "happy", expires)

    responses = await service.handle_mood(a_id, None)
    assert len(responses) == 1
    assert "happy" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_mood_none_active(test_db, service, paired_users):
    """/mood with no arg and no active mood shows usage."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    responses = await service.handle_mood(a_id, None)
    assert len(responses) == 1
    assert "no active mood" in responses[0].text.lower() or "usage" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_mood_invalid(test_db, service, paired_users):
    """/mood with invalid mood shows usage."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    responses = await service.handle_mood(a_id, "ecstatic")
    assert len(responses) == 1
    assert "usage" in responses[0].text.lower()


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
