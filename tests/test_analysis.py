"""Tests for background analysis module."""
from __future__ import annotations

import pytest
from pairtner import db as db_mod
from pairtner.analysis import (
    should_run_analysis,
    run_analysis,
    _parse_analysis,
    MIN_MESSAGES_FOR_ANALYSIS,
)


# ---------------------------------------------------------------------------
# _parse_analysis
# ---------------------------------------------------------------------------

class TestParseAnalysis:
    def test_full_result(self):
        result = {
            "patterns_observed": ["tends to catastrophize"],
            "horsemen_detected": [{"type": "criticism", "evidence": "you always...", "suggestion": "use I-statements"}],
            "partner_themes": ["feels unheard"],
            "voice_traits": {"register": "casual", "emoji_use": "heavy"},
        }
        parsed = _parse_analysis(result)
        assert parsed is not None
        assert len(parsed["patterns_observed"]) == 1
        assert len(parsed["horsemen_detected"]) == 1
        assert parsed["voice_traits"]["register"] == "casual"

    def test_empty_result(self):
        result = {
            "patterns_observed": [],
            "horsemen_detected": [],
            "partner_themes": [],
            "voice_traits": {},
        }
        parsed = _parse_analysis(result)
        assert parsed is None  # All empty -> None

    def test_partial_result(self):
        result = {
            "patterns_observed": ["one pattern"],
        }
        parsed = _parse_analysis(result)
        assert parsed is not None
        assert parsed["patterns_observed"] == ["one pattern"]
        assert parsed["horsemen_detected"] == []
        assert parsed["voice_traits"] == {}

    def test_string_json_parsing(self):
        result = {
            "patterns_observed": '["pattern one"]',
            "horsemen_detected": "[]",
            "partner_themes": "invalid json",
            "voice_traits": '{"register": "formal"}',
        }
        parsed = _parse_analysis(result)
        assert parsed is not None
        assert parsed["patterns_observed"] == ["pattern one"]
        assert parsed["partner_themes"] == []  # invalid JSON -> empty
        assert parsed["voice_traits"]["register"] == "formal"


# ---------------------------------------------------------------------------
# should_run_analysis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_should_run_no_messages(test_db):
    """No messages -> should not run."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    assert not await should_run_analysis(test_db, 1001, interval=5)


@pytest.mark.asyncio
async def test_should_run_enough_messages(test_db):
    """Enough messages -> should run."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    for i in range(10):
        await db_mod.log_message(test_db, 1001, "user", f"message {i}")
    assert await should_run_analysis(test_db, 1001, interval=5)


# ---------------------------------------------------------------------------
# run_analysis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_analysis_not_enough_messages(test_db):
    """Too few messages -> returns None."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.log_message(test_db, 1001, "user", "one message")

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    result = await run_analysis(test_db, llm, 1001)
    assert result is None
    assert len(llm.calls) == 0  # No LLM call made


@pytest.mark.asyncio
async def test_run_analysis_with_enough_messages(test_db):
    """Enough messages -> runs analysis and saves results."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.ensure_user_profile(test_db, 1001)
    for i in range(10):
        await db_mod.log_message(test_db, 1001, "user", f"I feel stressed about work {i}")
        await db_mod.log_message(test_db, 1001, "assistant", f"I hear you {i}")

    from tests.conftest import MockLLMClient
    llm = MockLLMClient(responses=[{
        "reply": "",
        "new_facts": [],
        "patterns_observed": ["frequently mentions work stress"],
        "horsemen_detected": [],
        "partner_themes": ["feels overwhelmed"],
        "voice_traits": {"register": "casual", "emoji_use": "none"},
    }])

    result = await run_analysis(test_db, llm, 1001)
    assert result is not None
    assert len(result["patterns_observed"]) == 1
    assert result["voice_traits"]["register"] == "casual"

    # Check that voice traits were saved to profile
    profile = await db_mod.get_user_profile(test_db, 1001)
    assert profile is not None
    import json
    voice = json.loads(profile["learned_voice"]) if profile.get("learned_voice") else {}
    assert voice.get("register") == "casual"


@pytest.mark.asyncio
async def test_analysis_records_timestamp(test_db):
    """After analysis, should_run returns False until more messages arrive."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.ensure_user_profile(test_db, 1001)
    for i in range(10):
        await db_mod.log_message(test_db, 1001, "user", f"msg {i}")

    from tests.conftest import MockLLMClient
    llm = MockLLMClient(responses=[{
        "reply": "",
        "new_facts": [],
        "patterns_observed": ["a pattern"],
        "horsemen_detected": [],
        "partner_themes": [],
        "voice_traits": {},
    }])

    await run_analysis(test_db, llm, 1001)

    # After analysis, not enough new messages
    assert not await should_run_analysis(test_db, 1001, interval=5)

    # Add more messages
    for i in range(6):
        await db_mod.log_message(test_db, 1001, "user", f"new msg {i}")
    assert await should_run_analysis(test_db, 1001, interval=5)


# ---------------------------------------------------------------------------
# update_voice_traits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_voice_traits_merge(test_db):
    """Voice traits should merge, not replace."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.ensure_user_profile(test_db, 1001)

    await db_mod.update_voice_traits(test_db, 1001, {"register": "casual"})
    await db_mod.update_voice_traits(test_db, 1001, {"emoji_use": "heavy"})

    profile = await db_mod.get_user_profile(test_db, 1001)
    import json
    voice = json.loads(profile["learned_voice"])
    assert voice["register"] == "casual"
    assert voice["emoji_use"] == "heavy"
