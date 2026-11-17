"""Tests for horsemen detection — LLM parsing, pattern recording, jargon filtering."""

from __future__ import annotations

import json

import pytest

from pairtner import db as db_mod
from pairtner.llm import parse_llm_response
from pairtner.profile_updater import process_llm_extras


# ---------------------------------------------------------------------------
# LLM response parsing — horsemen_detected
# ---------------------------------------------------------------------------

class TestHorsemenParsing:
    def test_parse_with_horsemen(self):
        raw = json.dumps({
            "reply": "I notice something in how you said that.",
            "new_facts": [],
            "relay_draft": None,
            "horsemen_detected": [
                {
                    "type": "criticism",
                    "evidence": "You always do this",
                    "suggestion": "Try expressing what you need instead",
                },
            ],
        })
        result = parse_llm_response(raw)
        assert len(result["horsemen_detected"]) == 1
        assert result["horsemen_detected"][0]["type"] == "criticism"

    def test_parse_without_horsemen(self):
        raw = json.dumps({
            "reply": "That sounds nice!",
            "new_facts": [],
            "relay_draft": None,
        })
        result = parse_llm_response(raw)
        assert result["horsemen_detected"] == []

    def test_parse_empty_horsemen(self):
        raw = json.dumps({
            "reply": "Hello",
            "new_facts": [],
            "relay_draft": None,
            "horsemen_detected": [],
        })
        result = parse_llm_response(raw)
        assert result["horsemen_detected"] == []

    def test_parse_multiple_horsemen(self):
        raw = json.dumps({
            "reply": "I notice some patterns.",
            "new_facts": [],
            "relay_draft": None,
            "horsemen_detected": [
                {"type": "criticism", "evidence": "You never...", "suggestion": "Try I feel..."},
                {"type": "contempt", "evidence": "Eye-rolling tone", "suggestion": "Express frustration directly"},
            ],
        })
        result = parse_llm_response(raw)
        assert len(result["horsemen_detected"]) == 2

    def test_parse_invalid_json_fallback(self):
        result = parse_llm_response("not json at all")
        assert result["horsemen_detected"] == []
        assert result["reply"] == "not json at all"


# ---------------------------------------------------------------------------
# Pattern recording with pattern_type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_pattern_horsemen_type(test_db, paired_users):
    a_id, _ = paired_users
    row_id = await db_mod.record_pattern(
        test_db, a_id, "criticism: You always forget", pattern_type="horsemen",
    )
    assert row_id > 0

    patterns = await db_mod.get_user_patterns_by_type(test_db, a_id, "horsemen")
    assert len(patterns) == 1
    assert patterns[0]["pattern_type"] == "horsemen"
    assert "criticism" in patterns[0]["pattern"]


@pytest.mark.asyncio
async def test_record_pattern_default_type(test_db, paired_users):
    a_id, _ = paired_users
    row_id = await db_mod.record_pattern(test_db, a_id, "uses blame language")
    assert row_id > 0

    patterns = await db_mod.get_user_patterns_by_type(
        test_db, a_id, "communication",
    )
    assert len(patterns) >= 1


@pytest.mark.asyncio
async def test_pattern_type_upsert(test_db, paired_users):
    """Same text + same pattern_type should increment frequency."""
    a_id, _ = paired_users
    await db_mod.record_pattern(
        test_db, a_id, "defensive response", pattern_type="horsemen",
    )
    await db_mod.record_pattern(
        test_db, a_id, "defensive response", pattern_type="horsemen",
    )

    patterns = await db_mod.get_user_patterns_by_type(test_db, a_id, "horsemen")
    assert len(patterns) == 1
    assert patterns[0]["frequency"] == 2


@pytest.mark.asyncio
async def test_different_pattern_types_separate(test_db, paired_users):
    """Same text with different pattern_type should be separate rows."""
    a_id, _ = paired_users
    await db_mod.record_pattern(
        test_db, a_id, "tends to generalize", pattern_type="communication",
    )
    await db_mod.record_pattern(
        test_db, a_id, "tends to generalize", pattern_type="horsemen",
    )

    comm = await db_mod.get_user_patterns_by_type(test_db, a_id, "communication")
    horsemen = await db_mod.get_user_patterns_by_type(test_db, a_id, "horsemen")
    assert len(comm) >= 1
    assert len(horsemen) >= 1


# ---------------------------------------------------------------------------
# Profile updater: horsemen handling + interaction recording
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_horsemen_records_patterns(test_db, paired_users, config):
    a_id, _ = paired_users
    llm_response = {
        "reply": "I understand.",
        "detected_mood": "angry",
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
        "horsemen_detected": [
            {"type": "criticism", "evidence": "You always forget", "suggestion": "Try I feel"},
        ],
    }
    await process_llm_extras(test_db, a_id, llm_response, config)

    patterns = await db_mod.get_user_patterns_by_type(test_db, a_id, "horsemen")
    assert len(patterns) == 1
    assert "criticism" in patterns[0]["pattern"]


@pytest.mark.asyncio
async def test_process_horsemen_records_negative_interaction(test_db, paired_users, config):
    a_id, b_id = paired_users
    couple_id = db_mod.make_couple_id(a_id, b_id)

    llm_response = {
        "reply": "I notice something.",
        "detected_mood": "angry",
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
        "horsemen_detected": [
            {"type": "contempt", "evidence": "Mocking tone", "suggestion": "Be direct"},
        ],
    }
    await process_llm_extras(test_db, a_id, llm_response, config)

    pos, neg = await db_mod.get_interaction_ratio(test_db, couple_id)
    assert neg >= 1


@pytest.mark.asyncio
async def test_process_happy_mood_records_positive(test_db, paired_users, config):
    a_id, b_id = paired_users
    couple_id = db_mod.make_couple_id(a_id, b_id)

    llm_response = {
        "reply": "That's great!",
        "detected_mood": "happy",
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
        "horsemen_detected": [],
    }
    await process_llm_extras(test_db, a_id, llm_response, config)

    pos, neg = await db_mod.get_interaction_ratio(test_db, couple_id)
    assert pos >= 1


@pytest.mark.asyncio
async def test_horsemen_disabled_skips_recording(test_db, paired_users):
    """When horsemen_enabled=False, patterns should not be recorded."""
    a_id, _ = paired_users

    class NoHorsemenConfig:
        horsemen_enabled = False
        mood_default_expiry_hours = 2
        mood_styles = {}

    llm_response = {
        "reply": "OK",
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
        "horsemen_detected": [
            {"type": "criticism", "evidence": "test", "suggestion": "test"},
        ],
    }
    await process_llm_extras(test_db, a_id, llm_response, NoHorsemenConfig())

    patterns = await db_mod.get_user_patterns_by_type(test_db, a_id, "horsemen")
    assert len(patterns) == 0


# ---------------------------------------------------------------------------
# Jargon check — user-facing output must not contain Gottman terms
# ---------------------------------------------------------------------------

class TestJargonFree:
    """Ensure Gottman jargon never appears in user-facing strings."""

    JARGON_TERMS = [
        "horsemen", "horseman", "four horsemen",
        "gottman", "gottman's",
    ]

    def test_prompt_json_format_no_jargon_in_reply_instruction(self):
        """The JSON schema instructs LLM to never use jargon in replies."""
        from pairtner import pack

        # The instruction should contain the prohibition
        assert "NEVER use the term 'horsemen'" in pack.current().contract()

    def test_i18n_strings_no_jargon(self):
        """No i18n string should contain Gottman jargon."""
        from pairtner.i18n import _STRINGS
        for lang, strings in _STRINGS.items():
            for key, value in strings.items():
                for term in self.JARGON_TERMS:
                    assert term.lower() not in value.lower(), (
                        f"Jargon '{term}' found in {lang}.{key}"
                    )

    def test_nudge_prompt_no_jargon(self):
        """The nudge prompt file should not contain Gottman jargon."""
        from pairtner.prompts import load_task_prompt

        content = load_task_prompt("nudge")
        if content:
            for term in self.JARGON_TERMS:
                assert term.lower() not in content.lower()

    def test_appreciation_prompt_no_jargon(self):
        """The appreciation prompt file should not reference clinical terms."""
        from pairtner.prompts import load_task_prompt

        content = load_task_prompt("appreciation")
        if content:
            for term in self.JARGON_TERMS:
                assert term.lower() not in content.lower()
