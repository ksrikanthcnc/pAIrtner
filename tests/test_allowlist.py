"""Allowlist tests — verify access control on all handler entry points."""

from __future__ import annotations

import pytest
import pytest_asyncio

from couples_bot.config import Config, is_user_allowed
from couples_bot.db import create_user, init_db, set_partner
from couples_bot.handlers import BotService

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALLOWED = frozenset({1001, 1002})
BLOCKED_ID = 9999


@pytest_asyncio.fixture
async def restricted_db(tmp_path) -> str:
    db_path = str(tmp_path / "restricted.db")
    await init_db(db_path)
    await create_user(db_path, 1001, "Alice")
    await create_user(db_path, 1002, "Bob")
    await set_partner(db_path, 1001, 1002)
    return db_path


@pytest.fixture
def restricted_config(restricted_db) -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        model_default="test-default",
        model_heavy=["test-heavy-1"],
        db_path=restricted_db,
        allowed_user_ids=ALLOWED,
    )


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest_asyncio.fixture
async def restricted_service(
    restricted_db, restricted_config, mock_llm
) -> BotService:
    return BotService(
        config=restricted_config, db_path=restricted_db, llm=mock_llm
    )


@pytest_asyncio.fixture
async def open_service(restricted_db, mock_llm) -> BotService:
    """Service with empty allowlist (open mode)."""
    cfg = Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        model_default="test-default",
        model_heavy=["test-heavy-1"],
        db_path=restricted_db,
        allowed_user_ids=frozenset(),  # open mode
    )
    return BotService(config=cfg, db_path=restricted_db, llm=mock_llm)


# ---------------------------------------------------------------------------
# is_user_allowed helper
# ---------------------------------------------------------------------------

class TestIsUserAllowed:
    def test_open_mode(self):
        cfg = Config(telegram_token="t", allowed_user_ids=frozenset())
        assert is_user_allowed(cfg, 123) is True
        assert is_user_allowed(cfg, 999) is True

    def test_restricted_allowed(self):
        cfg = Config(telegram_token="t", allowed_user_ids=frozenset({100}))
        assert is_user_allowed(cfg, 100) is True

    def test_restricted_blocked(self):
        cfg = Config(telegram_token="t", allowed_user_ids=frozenset({100}))
        assert is_user_allowed(cfg, 999) is False


# ---------------------------------------------------------------------------
# Blocked user gets no response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocked_start(restricted_service):
    responses = await restricted_service.handle_start(BLOCKED_ID, "Hacker")
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_message(restricted_service):
    responses = await restricted_service.handle_message(BLOCKED_ID, "Hacker", "hello")
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_tell(restricted_service):
    responses = await restricted_service.handle_tell(BLOCKED_ID, "test message")
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_callback(restricted_service):
    responses = await restricted_service.handle_callback(BLOCKED_ID, "ap:1")
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_join(restricted_service):
    responses = await restricted_service.handle_join(BLOCKED_ID, "Hacker", "CODE")
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_mediate(restricted_service):
    responses = await restricted_service.handle_mediate(BLOCKED_ID)
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_whatdoyouknow(restricted_service):
    responses = await restricted_service.handle_whatdoyouknow(BLOCKED_ID)
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_forget(restricted_service):
    responses = await restricted_service.handle_forget(BLOCKED_ID, "all")
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_export(restricted_service):
    responses = await restricted_service.handle_export(BLOCKED_ID)
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_help(restricted_service):
    responses = await restricted_service.handle_help(BLOCKED_ID)
    assert responses == []


@pytest.mark.asyncio
async def test_blocked_lang(restricted_service):
    responses = await restricted_service.handle_lang(BLOCKED_ID, "en")
    assert responses == []


# ---------------------------------------------------------------------------
# Allowed user gets normal responses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allowed_start(restricted_service):
    responses = await restricted_service.handle_start(1001, "Alice")
    assert len(responses) > 0


@pytest.mark.asyncio
async def test_allowed_help(restricted_service):
    responses = await restricted_service.handle_help(1001)
    assert len(responses) > 0
    assert "/start" in responses[0].text


@pytest.mark.asyncio
async def test_allowed_message(restricted_service):
    responses = await restricted_service.handle_message(1001, "Alice", "hello")
    assert len(responses) > 0


# ---------------------------------------------------------------------------
# Open mode allows everyone
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_mode_allows_anyone(open_service):
    responses = await open_service.handle_start(55555, "Stranger")
    assert len(responses) > 0


@pytest.mark.asyncio
async def test_open_mode_message(open_service):
    responses = await open_service.handle_message(55555, "Stranger", "hi")
    assert len(responses) > 0


def test_blocked_user_id_logged_once_without_content(caplog):
    import logging
    from couples_bot import config as cfg_mod
    from couples_bot.config import Config, is_user_allowed

    cfg_mod._logged_denied.discard(424242)
    c = Config(telegram_token="t", gemini_api_key="k", allowed_user_ids={1})
    with caplog.at_level(logging.WARNING, logger="couples_bot.config"):
        assert not is_user_allowed(c, 424242)
        assert not is_user_allowed(c, 424242)
    hits = [r for r in caplog.records if "424242" in r.getMessage()]
    assert len(hits) == 1
