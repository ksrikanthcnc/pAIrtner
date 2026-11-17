"""Tests for FEAT-002 — voice auto-adaptation (learned voice profile)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from pairtner.config import Config
from pairtner.db import (
    create_user,
    ensure_user_profile,
    get_message_count,
    get_user_profile,
    increment_message_count,
    init_db,
    set_partner,
    update_learned_voice,
    update_user_profile,
)
from pairtner.handlers import BotService
from pairtner.llm import parse_llm_response
from pairtner.profile_updater import process_llm_extras

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path) -> str:
    db_path = str(tmp_path / "voice_test.db")
    await init_db(db_path)
    return db_path


@pytest.fixture
def cfg() -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        voice_auto_learn=True,
        voice_learn_after_messages=10,
    )


@pytest_asyncio.fixture
async def svc(db, cfg) -> BotService:
    mock_llm = MockLLMClient()
    return BotService(config=cfg, db_path=db, llm=mock_llm)


@pytest_asyncio.fixture
async def user_with_profile(db) -> int:
    uid = 5001
    await create_user(db, uid, "TestUser")
    await ensure_user_profile(db, uid)
    return uid


# ---------------------------------------------------------------------------
# (a) learned_voice column storage and retrieval
# ---------------------------------------------------------------------------


async def test_learned_voice_column_roundtrip(db, user_with_profile):
    """Store and retrieve learned_voice JSON via the dedicated helper."""
    uid = user_with_profile
    traits = {"register": "casual", "humor": "light"}
    await update_learned_voice(db, uid, json.dumps(traits))

    profile = await get_user_profile(db, uid)
    assert profile is not None
    stored = json.loads(profile["learned_voice"])
    assert stored == traits


async def test_learned_voice_initially_null(db, user_with_profile):
    """A freshly-created profile has no learned_voice."""
    profile = await get_user_profile(db, user_with_profile)
    assert profile["learned_voice"] is None


# ---------------------------------------------------------------------------
# (b) message_count increment
# ---------------------------------------------------------------------------


async def test_message_count_increment(db, user_with_profile):
    uid = user_with_profile
    assert await get_message_count(db, uid) == 0
    await increment_message_count(db, uid)
    assert await get_message_count(db, uid) == 1
    await increment_message_count(db, uid)
    assert await get_message_count(db, uid) == 2


async def test_message_count_no_profile(db):
    """get_message_count returns 0 for users without a profile."""
    assert await get_message_count(db, 9999) == 0


# ---------------------------------------------------------------------------
# (c) voice traits extracted from LLM response and stored
# ---------------------------------------------------------------------------


async def test_voice_traits_stored_via_profile_updater(db, cfg, user_with_profile):
    uid = user_with_profile
    llm_response = {
        "reply": "hi",
        "new_facts": [],
        "relay_draft": None,
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
        "horsemen_detected": [],
        "voice_traits": {
            "register": "casual",
            "sentence_length": "short",
            "emoji_use": "light",
            "humor": "frequent",
            "directness": "very_direct",
        },
    }
    await process_llm_extras(db, uid, llm_response, cfg)

    profile = await get_user_profile(db, uid)
    assert profile["learned_voice"] is not None
    traits = json.loads(profile["learned_voice"])
    assert traits["register"] == "casual"
    assert traits["directness"] == "very_direct"

    # message_count should be incremented
    assert profile["message_count"] == 1


async def test_empty_voice_traits_not_stored(db, cfg, user_with_profile):
    uid = user_with_profile
    llm_response = {
        "reply": "hi",
        "new_facts": [],
        "relay_draft": None,
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
        "articulation_note": None,
        "horsemen_detected": [],
        "voice_traits": {},
    }
    await process_llm_extras(db, uid, llm_response, cfg)

    profile = await get_user_profile(db, uid)
    # Empty dict → not stored, learned_voice stays None
    assert profile["learned_voice"] is None
    # But message_count still incremented
    assert profile["message_count"] == 1


# ---------------------------------------------------------------------------
# (d) Maturity threshold — not used before 10 messages, used after
# ---------------------------------------------------------------------------


async def test_immature_profile_uses_static_voice_only(db, cfg):
    """Before threshold, learned voice should not appear in enriched style."""
    uid = 5010
    await create_user(db, uid, "Immature")
    await ensure_user_profile(db, uid)
    # Store traits but leave message_count at 5 (below threshold=10)
    traits = {"register": "formal", "humor": "none"}
    await update_learned_voice(db, uid, json.dumps(traits))
    await update_user_profile(db, uid, message_count=5)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    voice_text, _ = await svc._get_enriched_style(uid)

    # The static voice text should NOT contain learned instruction
    assert "Match this user" not in voice_text


async def test_mature_profile_prepends_learned_voice(db, cfg):
    """After threshold, learned voice instruction is prepended."""
    uid = 5011
    await create_user(db, uid, "Mature")
    await ensure_user_profile(db, uid)
    traits = {"register": "formal", "humor": "none"}
    await update_learned_voice(db, uid, json.dumps(traits))
    await update_user_profile(db, uid, message_count=15)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    voice_text, _ = await svc._get_enriched_style(uid)

    assert "Match this user" in voice_text
    assert "register=formal" in voice_text
    assert "humor=none" in voice_text


# ---------------------------------------------------------------------------
# (e) Learned voice overrides static voice in prompt building
# ---------------------------------------------------------------------------


async def test_learned_voice_is_before_static_voice(db, cfg):
    """The learned instruction should come before the static voice content."""
    uid = 5012
    await create_user(db, uid, "VoiceOrder")
    await ensure_user_profile(db, uid)
    traits = {"register": "casual"}
    await update_learned_voice(db, uid, json.dumps(traits))
    await update_user_profile(db, uid, message_count=20)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    voice_text, _ = await svc._get_enriched_style(uid)

    # Learned instruction is prepended
    learned_pos = voice_text.index("Match this user")
    # The default voice fallback text will follow
    assert learned_pos == 0 or voice_text[:learned_pos].strip() == ""


# ---------------------------------------------------------------------------
# (f) /vibe command output — with and without mature profile
# ---------------------------------------------------------------------------


async def test_vibe_command_immature(db, cfg):
    uid = 5020
    await create_user(db, uid, "VibeTest")
    await ensure_user_profile(db, uid)
    await update_user_profile(db, uid, message_count=3)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    responses = await svc.handle_vibe(uid, None)

    assert len(responses) == 1
    text = responses[0].text
    assert "3/10" in text  # count/threshold
    assert "still learning" in text.lower() or "ఇంకా" in text.lower()


async def test_vibe_command_mature_with_traits(db, cfg):
    uid = 5021
    await create_user(db, uid, "MatureVibe")
    await ensure_user_profile(db, uid)
    traits = {"register": "casual", "humor": "frequent"}
    await update_learned_voice(db, uid, json.dumps(traits))
    await update_user_profile(db, uid, message_count=25)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    responses = await svc.handle_vibe(uid, None)

    text = responses[0].text
    assert "active" in text.lower() or "యాక్టివ్" in text.lower()
    assert "register" in text
    assert "humor" in text


# ---------------------------------------------------------------------------
# (g) /vibe set manual override
# ---------------------------------------------------------------------------


async def test_vibe_set_creates_trait(db, cfg):
    uid = 5030
    await create_user(db, uid, "VibeSetter")
    await ensure_user_profile(db, uid)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    responses = await svc.handle_vibe(uid, "set register formal")

    assert len(responses) == 1
    assert "register" in responses[0].text
    assert "formal" in responses[0].text

    profile = await get_user_profile(db, uid)
    traits = json.loads(profile["learned_voice"])
    assert traits["register"] == "formal"


async def test_vibe_set_invalid_trait(db, cfg):
    uid = 5031
    await create_user(db, uid, "VibeInvalid")
    await ensure_user_profile(db, uid)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    responses = await svc.handle_vibe(uid, "set bogus_trait value")

    assert "Unknown" in responses[0].text or "తెలియని" in responses[0].text


async def test_vibe_set_usage_missing_value(db, cfg):
    uid = 5032
    await create_user(db, uid, "VibeUsage")
    await ensure_user_profile(db, uid)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)
    responses = await svc.handle_vibe(uid, "set register")

    text = responses[0].text
    assert "Usage" in text or "వాడకం" in text


# ---------------------------------------------------------------------------
# (h) Backward compatibility — /style still works as alias
# ---------------------------------------------------------------------------


async def test_style_alias_routes_to_vibe(db, cfg):
    uid = 5040
    await create_user(db, uid, "StyleAlias")
    await ensure_user_profile(db, uid)
    await update_user_profile(db, uid, message_count=5)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=db, llm=mock_llm)

    style_resp = await svc.handle_style(uid, None)
    vibe_resp = await svc.handle_vibe(uid, None)

    # Both should return the same content
    assert style_resp[0].text == vibe_resp[0].text


# ---------------------------------------------------------------------------
# parse_llm_response extracts voice_traits
# ---------------------------------------------------------------------------


def test_parse_llm_response_extracts_voice_traits():
    raw = json.dumps({
        "reply": "hello",
        "voice_traits": {
            "register": "casual",
            "sentence_length": "short",
        },
    })
    result = parse_llm_response(raw)
    assert result["voice_traits"] == {"register": "casual", "sentence_length": "short"}


def test_parse_llm_response_defaults_voice_traits_to_empty():
    raw = json.dumps({"reply": "hello"})
    result = parse_llm_response(raw)
    assert result["voice_traits"] == {}


def test_parse_llm_response_fallback_voice_traits():
    result = parse_llm_response("not json at all")
    assert result["voice_traits"] == {}


# ---------------------------------------------------------------------------
# Config fields
# ---------------------------------------------------------------------------


def test_config_voice_fields_defaults():
    cfg = Config(telegram_token="t", gemini_api_key="k")
    assert cfg.voice_auto_learn is True
    assert cfg.voice_learn_after_messages == 10


def test_config_voice_fields_custom():
    cfg = Config(
        telegram_token="t",
        gemini_api_key="k",
        voice_auto_learn=False,
        voice_learn_after_messages=25,
    )
    assert cfg.voice_auto_learn is False
    assert cfg.voice_learn_after_messages == 25


# ---------------------------------------------------------------------------
# DB migration — columns exist after init_db
# ---------------------------------------------------------------------------


async def test_db_migration_adds_columns(tmp_path):
    """init_db on a fresh DB creates learned_voice and message_count columns."""
    db_path = str(tmp_path / "migration_test.db")
    await init_db(db_path)
    uid = 7001
    await create_user(db_path, uid, "MigTest")
    await ensure_user_profile(db_path, uid)

    profile = await get_user_profile(db_path, uid)
    assert "learned_voice" in profile
    assert "message_count" in profile
    assert profile["message_count"] == 0
    assert profile["learned_voice"] is None


# ---------------------------------------------------------------------------
# voice_auto_learn=False disables learned voice in prompt
# ---------------------------------------------------------------------------


async def test_auto_learn_disabled_skips_learned_voice(db):
    uid = 5050
    cfg_off = Config(
        telegram_token="t",
        gemini_api_key="k",
        voice_auto_learn=False,
        voice_learn_after_messages=10,
    )
    await create_user(db, uid, "NoAutoLearn")
    await ensure_user_profile(db, uid)
    traits = {"register": "formal"}
    await update_learned_voice(db, uid, json.dumps(traits))
    await update_user_profile(db, uid, message_count=50)

    mock_llm = MockLLMClient()
    svc = BotService(config=cfg_off, db_path=db, llm=mock_llm)
    voice_text, _ = await svc._get_enriched_style(uid)

    assert "Match this user" not in voice_text
