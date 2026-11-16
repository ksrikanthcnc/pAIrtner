"""Tests for articulation teaching: tips shown, patterns recorded."""

from __future__ import annotations

import pytest

from couples_bot.db import get_user_patterns, record_pattern
from couples_bot.handlers import BotService

from tests.helpers import MockLLMClient


@pytest.mark.asyncio
async def test_articulation_tip_shown_after_send(test_db, config, paired_users):
    """Articulation note appears as a tip after #send relay draft."""
    a_id, b_id = paired_users
    mock = MockLLMClient(responses=[{
        "reply": "I understand",
        "new_facts": [],
        "relay_draft": "Could we talk about this?",
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": "Try using 'I feel' instead of 'You always'",
    }])
    svc = BotService(config=config, db_path=test_db, llm=mock)

    responses = await svc.handle_message(a_id, "Alice", "#send You always ignore me")
    combined = " ".join(r.text for r in responses)
    assert "💡" in combined
    assert "I feel" in combined


@pytest.mark.asyncio
async def test_articulation_tip_not_shown_when_disabled(test_db, paired_users):
    """Articulation tip is suppressed when config.articulation_enabled=False."""
    a_id, b_id = paired_users
    from couples_bot.config import Config
    cfg = Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        db_path=test_db,
        articulation_enabled=False,
    )
    mock = MockLLMClient(responses=[{
        "reply": "ok",
        "new_facts": [],
        "relay_draft": "Let's discuss",
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": "Try softer language",
    }])
    svc = BotService(config=cfg, db_path=test_db, llm=mock)

    responses = await svc.handle_message(a_id, "Alice", "#send You never listen")
    combined = " ".join(r.text for r in responses)
    assert "💡" not in combined


@pytest.mark.asyncio
async def test_articulation_pattern_recorded(test_db, paired_users):
    """Articulation note is recorded as a user pattern via profile_updater."""
    a_id, b_id = paired_users
    from couples_bot.config import Config
    cfg = Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        db_path=test_db,
    )
    mock = MockLLMClient(responses=[{
        "reply": "I hear you",
        "new_facts": [],
        "relay_draft": None,
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": "Consider expressing needs directly",
    }])
    svc = BotService(config=cfg, db_path=test_db, llm=mock)

    await svc.handle_message(a_id, "Alice", "I wish things were different")

    patterns = await get_user_patterns(test_db, a_id)
    pattern_texts = [p["pattern"] for p in patterns]
    assert "Consider expressing needs directly" in pattern_texts


@pytest.mark.asyncio
async def test_record_pattern_upsert(test_db, paired_users):
    """record_pattern increments frequency on duplicate."""
    a_id, _ = paired_users
    await record_pattern(test_db, a_id, "uses blame language")
    await record_pattern(test_db, a_id, "uses blame language")
    await record_pattern(test_db, a_id, "Uses Blame Language")  # case-insensitive

    patterns = await get_user_patterns(test_db, a_id)
    blame = [p for p in patterns if "blame" in p["pattern"].lower()]
    assert len(blame) == 1
    assert blame[0]["frequency"] == 3
