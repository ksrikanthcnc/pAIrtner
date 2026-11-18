"""Tests for profile_updater: process_llm_extras persists intelligence."""

from __future__ import annotations

import pytest

from pairtner.config import Config
from pairtner.db import (
    create_user,
    get_active_mood,
    get_partner_perceptions,
    get_user_patterns,
    set_partner,
)
from pairtner.core.profile_updater import process_llm_extras


def _config(**overrides) -> Config:
    defaults = dict(
        telegram_token="test-token",
        gemini_api_key="test-key",
        mood_default_expiry_hours=2,
        mood_styles={
            "stressed": {"dampening_override": 0.9, "response_length": "short"},
            "happy": {"dampening_override": 0.3, "response_length": "normal"},
        },
    )
    defaults.update(overrides)
    return Config(**defaults)


@pytest.mark.asyncio
async def test_process_detected_mood(test_db):
    """Non-neutral mood is stored via set_user_mood."""
    uid = 5001
    await create_user(test_db, uid, "Tester")
    cfg = _config()

    await process_llm_extras(test_db, uid, {
        "detected_mood": "stressed",
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
    }, cfg)

    mood = await get_active_mood(test_db, uid)
    assert mood is not None
    assert mood["mood"] == "stressed"


@pytest.mark.asyncio
async def test_process_neutral_mood_ignored(test_db):
    """Neutral mood is not stored."""
    uid = 5002
    await create_user(test_db, uid, "Tester2")
    cfg = _config()

    await process_llm_extras(test_db, uid, {
        "detected_mood": "neutral",
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
    }, cfg)

    mood = await get_active_mood(test_db, uid)
    assert mood is None


@pytest.mark.asyncio
async def test_process_patterns(test_db):
    """Observed patterns are recorded."""
    uid = 5003
    await create_user(test_db, uid, "Tester3")
    cfg = _config()

    await process_llm_extras(test_db, uid, {
        "detected_mood": None,
        "patterns_observed": ["generalizes often", "uses blame language"],
        "partner_themes": [],
        "articulation_note": None,
    }, cfg)

    patterns = await get_user_patterns(test_db, uid)
    texts = [p["pattern"] for p in patterns]
    assert "generalizes often" in texts
    assert "uses blame language" in texts


@pytest.mark.asyncio
async def test_process_partner_themes(test_db):
    """Partner themes create perceptions about the partner."""
    uid = 5004
    partner_id = 5005
    await create_user(test_db, uid, "Tester4")
    await create_user(test_db, partner_id, "Partner4")
    await set_partner(test_db, uid, partner_id)
    cfg = _config()

    await process_llm_extras(test_db, uid, {
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": ["feels unheard about finances"],
        "articulation_note": None,
    }, cfg)

    # Perceptions are stored ABOUT the partner (from user's perspective)
    perceptions = await get_partner_perceptions(test_db, partner_id)
    themes = [p["theme"] for p in perceptions]
    assert "feels unheard about finances" in themes


@pytest.mark.asyncio
async def test_process_articulation_note(test_db):
    """Articulation note is recorded as a pattern."""
    uid = 5006
    await create_user(test_db, uid, "Tester6")
    cfg = _config()

    await process_llm_extras(test_db, uid, {
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": "Try expressing needs with I-statements",
    }, cfg)

    patterns = await get_user_patterns(test_db, uid)
    texts = [p["pattern"] for p in patterns]
    assert "Try expressing needs with I-statements" in texts


@pytest.mark.asyncio
async def test_process_all_fields_together(test_db):
    """All fields processed in a single call."""
    uid = 5007
    partner_id = 5008
    await create_user(test_db, uid, "Tester7")
    await create_user(test_db, partner_id, "Partner7")
    await set_partner(test_db, uid, partner_id)
    cfg = _config()

    await process_llm_extras(test_db, uid, {
        "detected_mood": "happy",
        "patterns_observed": ["positive framing"],
        "partner_themes": ["appreciates effort"],
        "articulation_note": "Good use of I-statements",
    }, cfg)

    mood = await get_active_mood(test_db, uid)
    assert mood["mood"] == "happy"

    patterns = await get_user_patterns(test_db, uid)
    assert len(patterns) >= 2  # "positive framing" + articulation note

    perceptions = await get_partner_perceptions(test_db, partner_id)
    assert any(p["theme"] == "appreciates effort" for p in perceptions)


@pytest.mark.asyncio
async def test_process_never_raises(test_db):
    """process_llm_extras swallows all exceptions (fire-and-forget)."""
    # Pass garbage data — should not raise
    await process_llm_extras(test_db, 99999, {
        "detected_mood": 12345,  # wrong type
        "patterns_observed": "not a list",
        "partner_themes": None,
        "articulation_note": 42,
    }, _config())
    # If we get here, it didn't raise
