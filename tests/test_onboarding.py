"""Onboarding flow tests — single free-text prompt with LLM extraction."""

from __future__ import annotations

import pytest

from pairtner.db import (
    create_user,
    ensure_user_profile,
    get_active_facts,
    get_user_profile,
    init_db,
    set_partner,
    update_user_profile,
)
from pairtner.core.onboarding import (
    get_onboarding_question,
    process_onboarding_answer,
    save_onboarding_as_facts,
    should_start_onboarding,
)


@pytest.fixture
async def onboard_users(test_db):
    """Paired users with onboarding NOT done."""
    a_id, b_id = 2001, 2002
    await create_user(test_db, a_id, "OnbAlice")
    await create_user(test_db, b_id, "OnbBob")
    await set_partner(test_db, a_id, b_id)
    return a_id, b_id


# ---------------------------------------------------------------------------
# should_start_onboarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_start_onboarding_unpaired(test_db):
    """Unpaired users should not start onboarding."""
    await create_user(test_db, 1, "Solo")
    assert await should_start_onboarding(test_db, 1) is False


@pytest.mark.asyncio
async def test_should_start_onboarding_paired_no_profile(test_db, onboard_users):
    """Paired user with no profile should start onboarding."""
    a_id, _ = onboard_users
    assert await should_start_onboarding(test_db, a_id) is True


@pytest.mark.asyncio
async def test_should_start_onboarding_already_done(test_db, onboard_users):
    """Onboarding done → should not start."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_done=1)
    assert await should_start_onboarding(test_db, a_id) is False


@pytest.mark.asyncio
async def test_should_start_onboarding_in_progress(test_db, onboard_users):
    """Onboarding in progress (step > 0) → should_start returns False."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_step=2)
    assert await should_start_onboarding(test_db, a_id) is False


# ---------------------------------------------------------------------------
# get_onboarding_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_onboarding_question_first(test_db, onboard_users):
    """Returns the single free-text prompt at step 0."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)
    q = await get_onboarding_question(test_db, a_id)
    assert q is not None
    text, step = q
    assert step == 0
    assert "relationship" in text.lower() or "tell me" in text.lower()


@pytest.mark.asyncio
async def test_get_onboarding_question_done(test_db, onboard_users):
    """Returns None when onboarding is done."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_done=1)
    assert await get_onboarding_question(test_db, a_id) is None


# ---------------------------------------------------------------------------
# Free-text onboarding (no LLM — regex fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_free_text_substantial(test_db, onboard_users):
    """A substantial free-text answer completes onboarding in one step."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    text = "We've been married for 10 years, have two kids, and I'm in my 30s."
    consumed, response = await process_onboarding_answer(test_db, a_id, text)
    assert consumed is True
    assert response is not None
    assert "got it" in response.lower() or "thanks" in response.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1
    assert profile["relationship_type"] == "married"
    assert profile["duration_years"] == 10
    assert profile["has_kids"] == 1


@pytest.mark.asyncio
async def test_free_text_short(test_db, onboard_users):
    """Even a short response completes onboarding (single-prompt flow)."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    consumed, response = await process_onboarding_answer(test_db, a_id, "married")
    assert consumed is True
    assert response is not None

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1
    assert profile["relationship_type"] == "married"


@pytest.mark.asyncio
async def test_free_text_creates_core_facts(test_db, onboard_users):
    """Onboarding creates core facts from the user's response."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    text = "We've been dating for 3 years and have no kids."
    await process_onboarding_answer(test_db, a_id, text)

    facts = await get_active_facts(test_db, a_id)
    assert len(facts) >= 1  # At least the raw text is saved as core
    # Check that facts include the raw text
    all_text = " ".join(f["text"] for f in facts)
    assert "dating" in all_text.lower() or "3 years" in all_text.lower()


# ---------------------------------------------------------------------------
# LLM-assisted extraction
# ---------------------------------------------------------------------------


class FakeLLM:
    """Fake LLM that returns structured JSON for onboarding extraction."""

    def __init__(self, response_json: dict):
        self._response = response_json

    async def generate(self, user_content, system_prompt, **kwargs):
        import json
        return {"reply": json.dumps(self._response), "memory_ops": [], "new_facts": []}


@pytest.mark.asyncio
async def test_llm_extraction(test_db, onboard_users):
    """LLM extraction populates profile from structured JSON."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    fake_llm = FakeLLM({
        "relationship_type": "married",
        "duration_years": 10,
        "has_kids": True,
        "age_range": "30s",
        "appreciates": "Her patience and kindness",
        "wish": "Better communication during stress",
        "summary": "Married 10 years with kids, values patience.",
    })

    text = "We've been married for 10 years and have two kids."
    consumed, response = await process_onboarding_answer(
        test_db, a_id, text, llm=fake_llm,
    )

    assert consumed is True
    assert "patience" in response.lower() or "got it" in response.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1
    assert profile["relationship_type"] == "married"
    assert profile["duration_years"] == 10
    assert profile["has_kids"] == 1
    assert profile["age_range"] == "30s"


@pytest.mark.asyncio
async def test_llm_extraction_with_appreciates(test_db, onboard_users):
    """LLM extraction includes appreciates/wish in response."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    fake_llm = FakeLLM({
        "relationship_type": "dating",
        "duration_years": 2,
        "has_kids": False,
        "age_range": None,
        "appreciates": "His humor",
        "wish": "More quality time",
        "summary": "Dating for 2 years, no kids.",
    })

    consumed, response = await process_onboarding_answer(
        test_db, a_id, "We've been dating 2 years", llm=fake_llm,
    )
    assert consumed is True
    # Response should mention what was appreciated
    assert "humor" in response.lower() or "got it" in response.lower()


@pytest.mark.asyncio
async def test_llm_failure_falls_back(test_db, onboard_users):
    """When LLM fails, regex fallback still extracts what it can."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    class BrokenLLM:
        async def generate(self, *args, **kwargs):
            raise RuntimeError("LLM is down")

    text = "Married 7 years with kids"
    consumed, response = await process_onboarding_answer(
        test_db, a_id, text, llm=BrokenLLM(),
    )
    assert consumed is True

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1
    assert profile["relationship_type"] == "married"
    assert profile["duration_years"] == 7
    assert profile["has_kids"] == 1


# ---------------------------------------------------------------------------
# /skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_onboarding(test_db, onboard_users):
    """User can /skip to end onboarding immediately."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)

    consumed, msg = await process_onboarding_answer(test_db, a_id, "/skip")
    assert consumed is True
    assert "skip" in msg.lower()

    profile = await get_user_profile(test_db, a_id)
    assert profile["onboarding_done"] == 1


# ---------------------------------------------------------------------------
# save_onboarding_as_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_onboarding_as_facts(test_db, onboard_users):
    """Profile data is converted to core facts."""
    a_id, _ = onboard_users
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(
        test_db, a_id,
        relationship_type="married",
        duration_years=5,
        has_kids=1,
        age_range="30-35",
    )

    count = await save_onboarding_as_facts(test_db, a_id)
    assert count >= 2  # At least relationship + kids

    facts = await get_active_facts(test_db, a_id, "private")
    texts = [f["text"].lower() for f in facts]
    assert any("married" in t for t in texts)


# ---------------------------------------------------------------------------
# Integration: triggered after /join
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_onboarding_triggered_after_join(test_db, service, mock_llm):
    """Onboarding questions sent to both partners after /join."""
    resp = await service.handle_start(1001, "Alice")
    code = None
    for r in resp:
        for word in r.text.split():
            if len(word) == 6 and word.isalnum() and word == word.upper():
                code = word
    assert code is not None

    responses = await service.handle_join(1002, "Bob", code)
    texts = [r.text for r in responses]

    has_onboarding = any(
        "relationship" in r.text.lower() or "onboard" in r.text.lower()
        for r in responses
    )
    assert has_onboarding, f"Expected onboarding in responses: {texts}"
