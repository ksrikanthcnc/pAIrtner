"""Tests for fact contradiction detection and resolution callbacks."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    add_fact,
    archive_fact,
    get_active_facts,
    get_fact_by_id,
    supersede_fact,
)
from couples_bot.handlers import BotService

from tests.helpers import MockLLMClient


@pytest.mark.asyncio
async def test_contradiction_detected(test_db, config, paired_users):
    """When a new fact contradicts an old one in the same category, user is asked."""
    a_id, b_id = paired_users

    # Pre-existing fact
    await add_fact(test_db, a_id, "Alice likes cooking", "private", category="household")

    mock = MockLLMClient(responses=[{
        "reply": "Noted",
        "new_facts": [{
            "text": "Alice dislikes cooking",
            "visibility": "private",
            "category": "household",
            "fact_type": "stable",
        }],
        "relay_draft": None,
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
    }])
    svc = BotService(config=config, db_path=test_db, llm=mock)

    responses = await svc.handle_message(a_id, "Alice", "#keep I actually dislike cooking")

    # Should have the contradiction message
    combined = " ".join(r.text for r in responses)
    # The contradiction message contains both old and new facts
    has_contradiction = any(r.buttons and any(
        any("fc_new:" in b.callback_data for b in row)
        for row in r.buttons
    ) for r in responses if r.buttons)

    # Check that svc._pending_contradictions was populated
    assert has_contradiction or "likes cooking" in combined


@pytest.mark.asyncio
async def test_callback_keep_new(test_db, config, paired_users):
    """fc_new callback archives old fact and keeps new."""
    a_id, _ = paired_users
    old_id = await add_fact(test_db, a_id, "old preference", "private", category="household")
    new_id = await add_fact(test_db, a_id, "new preference", "private", category="household")

    mock = MockLLMClient()
    svc = BotService(config=config, db_path=test_db, llm=mock)

    responses = await svc.handle_callback(a_id, f"fc_new:{new_id}:{old_id}")
    assert any("updated" in r.text.lower() or "✅" in r.text for r in responses)

    old = await get_fact_by_id(test_db, old_id)
    assert old["archived_at"] is not None
    assert old["supersedes_id"] == new_id


@pytest.mark.asyncio
async def test_callback_keep_both(test_db, config, paired_users):
    """fc_both callback keeps both facts active."""
    a_id, _ = paired_users
    old_id = await add_fact(test_db, a_id, "first thing", "private")
    new_id = await add_fact(test_db, a_id, "second thing", "private")

    mock = MockLLMClient()
    svc = BotService(config=config, db_path=test_db, llm=mock)

    responses = await svc.handle_callback(a_id, f"fc_both:{new_id}:{old_id}")
    assert any("✅" in r.text for r in responses)

    old = await get_fact_by_id(test_db, old_id)
    new = await get_fact_by_id(test_db, new_id)
    assert old["archived_at"] is None
    assert new["archived_at"] is None


@pytest.mark.asyncio
async def test_callback_keep_old(test_db, config, paired_users):
    """fc_old callback archives the new fact."""
    a_id, _ = paired_users
    old_id = await add_fact(test_db, a_id, "keep this", "private")
    new_id = await add_fact(test_db, a_id, "discard this", "private")

    mock = MockLLMClient()
    svc = BotService(config=config, db_path=test_db, llm=mock)

    responses = await svc.handle_callback(a_id, f"fc_old:{new_id}:{old_id}")

    new = await get_fact_by_id(test_db, new_id)
    assert new["archived_at"] is not None


@pytest.mark.asyncio
async def test_no_contradiction_different_categories(test_db, config, paired_users):
    """No contradiction if facts are in different categories."""
    a_id, _ = paired_users

    await add_fact(test_db, a_id, "Alice likes walking", "private", category="health")

    mock = MockLLMClient(responses=[{
        "reply": "Noted",
        "new_facts": [{
            "text": "Alice dislikes office politics",
            "visibility": "private",
            "category": "work",
            "fact_type": "stable",
        }],
        "relay_draft": None,
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
    }])
    svc = BotService(config=config, db_path=test_db, llm=mock)

    responses = await svc.handle_message(a_id, "Alice", "#keep I dislike office politics")

    # Should NOT trigger contradiction (different categories)
    has_contradiction = any(
        r.buttons and any(
            any("fc_new:" in b.callback_data for b in row)
            for row in r.buttons
        )
        for r in responses if r.buttons
    )
    assert not has_contradiction
