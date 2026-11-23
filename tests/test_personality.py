"""Tests for the conversational personality questionnaire."""
from __future__ import annotations

import pytest

from pairtner import db as db_mod
from pairtner.features import personality as P
from pairtner.storage.helpers import _fetch_one
from tests.helpers import MockLLMClient

UID = 2001


async def _meta(db, key):
    row = await _fetch_one(
        db, "SELECT value FROM user_meta WHERE user_id = ? AND key = ?", (UID, key))
    return row["value"] if row else None


async def _ready(db, messages=10):
    await db_mod.ensure_user_profile(db, UID)
    await db_mod.update_user_profile(
        db, UID, onboarding_done=1, message_count=messages)


def _llm(*labels):
    return MockLLMClient([{"reply": l, "new_facts": [], "relay_draft": None} for l in labels])


async def _answer(db, label):
    await P.get_next_personality_question(db, UID)
    return await P.process_personality_answer(db, UID, "whatever", _llm(label))


@pytest.mark.asyncio
async def test_questions_in_order(test_db):
    await _ready(test_db)
    for i, q in enumerate(P.PERSONALITY_QUESTIONS):
        assert await P.get_next_personality_question(test_db, UID) == q["text"]
        label = next(iter(q["options"]))
        res = await P.process_personality_answer(test_db, UID, "x", _llm(label))
        assert res, f"question {i} not processed"


@pytest.mark.asyncio
async def test_q1_classified_and_stored(test_db):
    await _ready(test_db)
    res = await _answer(test_db, "help")
    assert res["default_interaction_mode"] == "solution"
    assert res["confirmation"]
    assert await _meta(test_db, "default_interaction_mode") == "solution"
    assert await _meta(test_db, "setting:interaction_mode") == "solution"


@pytest.mark.asyncio
async def test_other_keys_stored(test_db):
    await _ready(test_db)
    await _answer(test_db, "listen")
    assert (await _answer(test_db, "cool_off"))["conflict_style"] == "cool_off"
    assert (await _answer(test_db, "short"))["response_length"] == "short"
    assert await _meta(test_db, "setting:response_length") == "short"
    await _answer(test_db, "validation")
    assert await _meta(test_db, "default_interaction_mode") == "vent"
    assert (await _answer(test_db, "wait"))["camouflage_frequency"] == "low"
    assert await _meta(test_db, "setting:camouflage_frequency") == "low"


@pytest.mark.asyncio
async def test_q4_conflict_with_q1_is_adaptive(test_db):
    await _ready(test_db)
    await _answer(test_db, "help")
    await _answer(test_db, "talk")
    await _answer(test_db, "long")
    await _answer(test_db, "validation")
    assert await _meta(test_db, "default_interaction_mode") == "adaptive"


@pytest.mark.asyncio
async def test_unclear_answer_not_recorded(test_db):
    await _ready(test_db)
    await P.get_next_personality_question(test_db, UID)
    assert await P.process_personality_answer(test_db, UID, "hm", _llm("unclear")) == {}
    assert await P.get_pending_question_id(test_db, UID) is None
    assert await _meta(test_db, "default_interaction_mode") is None
    assert await P.get_next_personality_question(test_db, UID) == P.PERSONALITY_QUESTIONS[0]["text"]


@pytest.mark.asyncio
async def test_not_asked_until_enough_messages(test_db):
    await _ready(test_db, messages=5)
    assert not await P.should_ask_personality(test_db, UID)
    await db_mod.update_user_profile(test_db, UID, message_count=6)
    assert await P.should_ask_personality(test_db, UID)


@pytest.mark.asyncio
async def test_not_asked_before_onboarding_done(test_db):
    await db_mod.ensure_user_profile(test_db, UID)
    await db_mod.update_user_profile(test_db, UID, message_count=20)
    assert not await P.should_ask_personality(test_db, UID)


@pytest.mark.asyncio
async def test_gap_between_questions(test_db):
    await _ready(test_db, messages=10)
    await _answer(test_db, "help")
    assert not await P.should_ask_personality(test_db, UID)
    await db_mod.update_user_profile(test_db, UID, message_count=13)
    assert not await P.should_ask_personality(test_db, UID)
    await db_mod.update_user_profile(test_db, UID, message_count=14)
    assert await P.should_ask_personality(test_db, UID)


@pytest.mark.asyncio
async def test_no_questions_after_all_answered(test_db):
    await _ready(test_db)
    for q in P.PERSONALITY_QUESTIONS:
        await _answer(test_db, next(iter(q["options"])))
    await db_mod.update_user_profile(test_db, UID, message_count=500)
    assert await P.get_next_personality_question(test_db, UID) is None
    assert not await P.should_ask_personality(test_db, UID)


@pytest.mark.asyncio
async def test_handle_plain_no_personality_in_handler(service, mock_llm, paired_users):
    """Personality questionnaire was removed from handler — plain replies
    should NOT append personality questions."""
    uid = paired_users[0]
    db = service.db_path
    await db_mod.update_user_profile(db, uid, onboarding_done=1, message_count=10)
    first = await service._handle_plain(uid, "hello there")
    # Personality questions are no longer injected into handler responses
    assert "By the way" not in first[0].text
