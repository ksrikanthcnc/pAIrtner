"""Onboarding flow tests."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    create_user,
    ensure_user_profile,
    get_user_profile,
    init_db,
    set_partner,
    update_user_profile,
)
from couples_bot.onboarding import (
    get_onboarding_question,
    process_onboarding_answer,
    should_start_onboarding,
)


@pytest.mark.asyncio
async def test_should_start_onboarding_unpaired(test_db):
    """Unpaired users should not start onboarding."""
    await create_user(test_db, 1, "Solo")
    assert await should_start_onboarding(test_db, 1) is False


@pytest.mark.asyncio
async def test_should_start_onboarding_paired_no_profile(test_db, paired_users):
    """Paired user with no profile should start onboarding."""
    a_id, _ = paired_users
    # No profile row yet → should start
    assert await should_start_onboarding(test_db, a_id) is True


@pytest.mark.asyncio
async def test_should_start_onboarding_already_done(test_db, paired_users):
    """Onboarding done → should not start."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_done=1)
    assert await should_start_onboarding(test_db, a_id) is False


@pytest.mark.asyncio
async def test_should_start_onboarding_in_progress(test_db, paired_users):
    """Onboarding in progress (step > 0) → should_start returns False."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_step=2)
    assert await should_start_onboarding(test_db, a_id) is False


@pytest.mark.asyncio
async def test_get_onboarding_question_first(test_db, paired_users):
    """First question returned for a fresh profile."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    q = await get_onboarding_question(test_db, a_id)
    assert q is not None
    text, step = q
    assert step == 1
    assert "relationship" in text.lower()


@pytest.mark.asyncio
async def test_get_onboarding_question_done(test_db, paired_users):
    """Returns None when onboarding is done."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_done=1)
    assert await get_onboarding_question(test_db, a_id) is None


@pytest.mark.asyncio
async def test_full_onboarding_flow(test_db, paired_users):
    """Walk through all 6 onboarding questions."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    answers = ["married", "5", "yes", "28-32", "Her kindness", "Better communication"]

    for i, ans in enumerate(answers):
        consumed, next_text = await process_onboarding_answer(test_db, a_id, ans)
        assert consumed is True
        if i < len(answers) - 1:
            assert next_text is not None
        else:
            # Last answer → completion message
            assert next_text is not None
            assert "thanks" in next_text.lower() or "got it" in next_text.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1
    assert profile["relationship_type"] == "married"
    assert profile["duration_years"] == 5
    assert profile["has_kids"] == 1
    assert profile["age_range"] == "28-32"
    assert "kindness" in profile["love_story_brief"].lower()
    assert "communication" in profile["love_story_brief"].lower()


@pytest.mark.asyncio
async def test_skip_onboarding(test_db, paired_users):
    """User can /skip at any point to end onboarding."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    # Answer first question
    consumed, _ = await process_onboarding_answer(test_db, a_id, "dating")
    assert consumed is True

    # Skip the rest
    consumed, msg = await process_onboarding_answer(test_db, a_id, "/skip")
    assert consumed is True
    assert msg is not None
    assert "skip" in msg.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1


@pytest.mark.asyncio
async def test_skip_onboarding_from_start(test_db, paired_users):
    """User can /skip even before answering any question."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    consumed, msg = await process_onboarding_answer(test_db, a_id, "/skip")
    assert consumed is True
    assert "skip" in msg.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1


@pytest.mark.asyncio
async def test_resume_interrupted_onboarding(test_db, paired_users):
    """Interrupted onboarding resumes from the right step."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    # Answer first two questions
    await process_onboarding_answer(test_db, a_id, "married")
    await process_onboarding_answer(test_db, a_id, "3")

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_step"] == 2
    assert profile["onboarding_done"] == 0

    # Get next question — should be Q3 (kids)
    q = await get_onboarding_question(test_db, a_id)
    assert q is not None
    _, step = q
    assert step == 3


@pytest.mark.asyncio
async def test_invalid_duration_reasks(test_db, paired_users):
    """Invalid duration input re-asks the same question."""
    a_id, _ = paired_users
    await ensure_user_profile(test_db, a_id)

    # Q1: relationship type
    await process_onboarding_answer(test_db, a_id, "married")
    # Q2: duration — invalid
    consumed, text = await process_onboarding_answer(test_db, a_id, "many")
    assert consumed is True
    assert "duration" in text.lower() or "year" in text.lower()

    # Step should NOT have advanced past 1
    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_step"] == 1


@pytest.mark.asyncio
async def test_onboarding_triggered_after_join(test_db, service, mock_llm):
    """Onboarding questions sent to both partners after /join."""
    # User A starts
    resp = await service.handle_start(1001, "Alice")
    # Extract pairing code
    code = None
    for r in resp:
        for word in r.text.split():
            if len(word) == 6 and word.isalnum() and word.isupper():
                code = word
    assert code is not None

    # User B joins
    responses = await service.handle_join(1002, "Bob", code)
    texts = [r.text for r in responses]
    joined = " ".join(texts)

    # At least one response should contain onboarding welcome
    has_onboarding = any(
        "relationship" in r.text.lower() or "onboard" in r.text.lower()
        for r in responses
    )
    assert has_onboarding, f"Expected onboarding in responses: {texts}"
