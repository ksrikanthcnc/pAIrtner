"""Style command tests — /style, voice selection, intensity callbacks."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    create_user,
    ensure_user_profile,
    get_user_profile,
    set_partner,
    update_user_profile,
)


@pytest.mark.asyncio
async def test_style_shows_current(test_db, service, paired_users):
    """``/style`` with no arg shows current voice and intensity."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    responses = await service.handle_style(a_id, None)
    assert len(responses) == 1
    assert "casual" in responses[0].text.lower() or "voice" in responses[0].text.lower()
    # Should have inline buttons
    assert responses[0].buttons is not None
    assert len(responses[0].buttons) == 2  # voice row + intensity row


@pytest.mark.asyncio
async def test_style_voice_callback(test_db, service, paired_users):
    """Voice selection callback updates profile."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    responses = await service.handle_callback(a_id, "sv:telugu")
    assert len(responses) == 1
    assert "telugu" in responses[0].text.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["default_voice"] == "telugu"


@pytest.mark.asyncio
async def test_style_intensity_callback(test_db, service, paired_users):
    """Intensity selection callback updates profile."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    responses = await service.handle_callback(a_id, "si:0.8")
    assert len(responses) == 1
    assert "0.8" in responses[0].text

    profile = await get_user_profile(test_db, a_id)
    assert abs(profile["voice_intensity"] - 0.8) < 0.01


@pytest.mark.asyncio
async def test_default_voice_on_new_user(test_db, paired_users):
    """New user profile has default voice casual_en."""
    a_id, _ = paired_users
    profile = await ensure_user_profile(test_db, a_id)
    assert profile["default_voice"] == "casual_en"
    assert abs(profile["voice_intensity"] - 0.5) < 0.01


@pytest.mark.asyncio
async def test_voice_stored_persistently(test_db, service, paired_users):
    """Voice change persists across multiple reads."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    await service.handle_callback(a_id, "sv:hindi")

    profile1 = await get_user_profile(test_db, a_id)
    profile2 = await get_user_profile(test_db, a_id)
    assert profile1["default_voice"] == "hindi"
    assert profile2["default_voice"] == "hindi"
