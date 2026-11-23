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


# ---------------------------------------------------------------------------
# Settings sub-menus via callbacks
# ---------------------------------------------------------------------------


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
async def test_settings_shows_text_summary(service: BotService, test_db, paired_users):
    """/settings is a plain-text summary with no button grid."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    responses = await service.handle_settings(a_id)
    assert len(responses) == 1
    assert "settings" in responses[0].text.lower()
    assert "tell me what you want" in responses[0].text.lower()
    assert not responses[0].buttons


@pytest.mark.asyncio
async def test_removed_style_callbacks_are_inert(service: BotService, test_db, paired_users):
    """Old style/dampening/mood callbacks no longer change the profile."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    before = await get_user_profile(test_db, a_id)
    for data in ("sv:telugu", "si:0.8", "sd_up", "sd_down", "sm:happy", "stv", "std", "stm"):
        await service.handle_callback(a_id, data)
    after = await get_user_profile(test_db, a_id)
    assert after["default_voice"] == before["default_voice"]
    assert after["dampening_base"] == before["dampening_base"]
