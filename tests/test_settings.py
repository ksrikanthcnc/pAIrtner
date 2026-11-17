"""Settings menu tests (FEAT-004)."""

from __future__ import annotations

import pytest

from pairtner.db import (
    ensure_user_profile,
    get_user_profile,
)
from pairtner.handlers import BotService


# ---------------------------------------------------------------------------
# /settings command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_shows_menu(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_settings(a_id)
    assert len(responses) == 1
    assert "Settings" in responses[0].text or "settings" in responses[0].text.lower()
    assert responses[0].buttons is not None
    # Should have 3 rows of buttons
    assert len(responses[0].buttons) == 3


# ---------------------------------------------------------------------------
# Settings sub-menus via callbacks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_voice_callback(service: BotService, test_db, paired_users):
    """Voice settings button shows the voice picker."""
    a_id, _ = paired_users
    responses = await service.handle_callback(a_id, "stv")
    assert len(responses) == 1
    # Should show the style picker (same as /style)
    assert "Style" in responses[0].text or "Voice" in responses[0].text or "voice" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_settings_dampening_callback(service: BotService, test_db, paired_users):
    """Dampening settings button shows current level with +/- buttons."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    responses = await service.handle_callback(a_id, "std")
    assert len(responses) == 1
    assert responses[0].buttons is not None
    # Should have +/- buttons
    button_texts = [b.text for row in responses[0].buttons for b in row]
    assert "➖" in button_texts
    assert "➕" in button_texts


@pytest.mark.asyncio
async def test_dampening_up(service: BotService, test_db, paired_users):
    """Dampening + button increases the base level."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    profile_before = await get_user_profile(test_db, a_id)
    base_before = profile_before["dampening_base"]

    responses = await service.handle_callback(a_id, "sd_up")
    assert len(responses) == 1

    profile_after = await get_user_profile(test_db, a_id)
    assert profile_after["dampening_base"] == min(1.0, round(base_before + 0.1, 1))


@pytest.mark.asyncio
async def test_dampening_down(service: BotService, test_db, paired_users):
    """Dampening - button decreases the base level."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    profile_before = await get_user_profile(test_db, a_id)
    base_before = profile_before["dampening_base"]

    responses = await service.handle_callback(a_id, "sd_down")
    assert len(responses) == 1

    profile_after = await get_user_profile(test_db, a_id)
    assert profile_after["dampening_base"] == max(0.1, round(base_before - 0.1, 1))


@pytest.mark.asyncio
async def test_dampening_max_cap(service: BotService, test_db, paired_users):
    """Dampening base cannot exceed 1.0."""
    from pairtner.db import update_user_profile

    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, dampening_base=1.0)

    await service.handle_callback(a_id, "sd_up")
    profile = await get_user_profile(test_db, a_id)
    assert profile["dampening_base"] == 1.0


@pytest.mark.asyncio
async def test_dampening_min_cap(service: BotService, test_db, paired_users):
    """Dampening base cannot go below 0.1."""
    from pairtner.db import update_user_profile

    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, dampening_base=0.1)

    await service.handle_callback(a_id, "sd_down")
    profile = await get_user_profile(test_db, a_id)
    assert profile["dampening_base"] == 0.1


@pytest.mark.asyncio
async def test_settings_goals_callback(service: BotService, test_db, paired_users):
    """Goals settings button shows goals (reuse /goals)."""
    a_id, _ = paired_users
    responses = await service.handle_callback(a_id, "stg")
    assert len(responses) == 1
    # Should show goals empty message (no goals set)
    assert "goals" in responses[0].text.lower() or "No active" in responses[0].text


@pytest.mark.asyncio
async def test_settings_profile_callback(service: BotService, test_db, paired_users):
    """Profile settings button shows current profile with edit buttons."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    responses = await service.handle_callback(a_id, "stp")
    assert len(responses) == 1
    assert responses[0].buttons is not None
    # Should have 4 profile field buttons
    assert len(responses[0].buttons) == 4


@pytest.mark.asyncio
async def test_profile_edit_flow(service: BotService, test_db, paired_users):
    """Profile field edit: tap button → send answer → updated."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    # Start editing relationship_type
    responses = await service.handle_callback(a_id, "sp:relationship_type")
    assert len(responses) == 1
    # Should ask the question
    assert "relationship" in responses[0].text.lower() or "type" in responses[0].text.lower()

    # User answers
    responses = await service.handle_message(a_id, "Alice", "married")
    assert len(responses) == 1
    assert "updated" in responses[0].text.lower() or "Profile" in responses[0].text

    profile = await get_user_profile(test_db, a_id)
    assert profile["relationship_type"] == "married"


@pytest.mark.asyncio
async def test_settings_mood_callback(service: BotService, test_db, paired_users):
    """Mood settings button shows mood presets."""
    a_id, _ = paired_users
    responses = await service.handle_callback(a_id, "stm")
    assert len(responses) == 1
    assert responses[0].buttons is not None
    # Should have mood buttons
    button_texts = [b.text for row in responses[0].buttons for b in row]
    assert any("Stressed" in t for t in button_texts)
    assert any("Happy" in t for t in button_texts)


@pytest.mark.asyncio
async def test_mood_set_via_callback(service: BotService, test_db, paired_users):
    """Setting mood via settings callback works."""
    a_id, _ = paired_users
    responses = await service.handle_callback(a_id, "sm:stressed")
    assert len(responses) == 1
    assert "stressed" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_mood_clear_via_callback(service: BotService, test_db, paired_users):
    """Clearing mood via settings callback works."""
    a_id, _ = paired_users
    responses = await service.handle_callback(a_id, "sm_clear")
    assert len(responses) == 1
    assert "No active mood" in responses[0].text or "mood" in responses[0].text.lower()
