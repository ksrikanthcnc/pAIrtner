"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest
import pytest_asyncio

from pairtner import pack
from pairtner.config import Config
from pairtner.db import (
    add_fact,
    create_user,
    init_db,
    log_message,
    set_partner,
)
from pairtner.handlers import BotService

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_active_pack():
    """A test that activates a custom pack must not leak it into the next test."""
    pack._current = None
    yield
    pack._current = None


@pytest.fixture
def config() -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        model_default="test-default",
        model_heavy=["test-heavy-1", "test-heavy-2"],
        cooldown_minutes=5,
        db_path=":memory:",  # overridden by test_db
        heavy_daily_cap=10,
    )


@pytest_asyncio.fixture
async def test_db(tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    return db_path


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest_asyncio.fixture
async def service(test_db, config, mock_llm) -> BotService:
    cfg = Config(
        telegram_token=config.telegram_token,
        gemini_api_key=config.gemini_api_key,
        model_default=config.model_default,
        model_heavy=config.model_heavy,
        cooldown_minutes=config.cooldown_minutes,
        db_path=test_db,
        heavy_daily_cap=config.heavy_daily_cap,
    )
    return BotService(config=cfg, db_path=test_db, llm=mock_llm)


@pytest_asyncio.fixture
async def paired_users(test_db) -> tuple[int, int]:
    """Create two paired users with sample facts.

    Returns (user_a_id, user_b_id).
    """
    a_id, b_id = 1001, 1002

    await create_user(test_db, a_id, "Alice")
    await create_user(test_db, b_id, "Bob")
    await set_partner(test_db, a_id, b_id)

    # Mark onboarding complete so tests aren't intercepted by onboarding flow
    from pairtner.db import ensure_user_profile, update_user_profile
    await ensure_user_profile(test_db, a_id)
    await update_user_profile(test_db, a_id, onboarding_done=1)
    await ensure_user_profile(test_db, b_id)
    await update_user_profile(test_db, b_id, onboarding_done=1)

    # Alice's facts
    await add_fact(test_db, a_id, "Alice feels stressed about work", "private")
    await add_fact(test_db, a_id, "Alice likes morning walks", "private")
    await add_fact(test_db, a_id, "We agreed to cook together on weekends", "shared")

    # Bob's facts
    await add_fact(test_db, b_id, "Bob is anxious about finances", "private")
    await add_fact(test_db, b_id, "Bob prefers quiet evenings", "private")
    await add_fact(test_db, b_id, "We decided to try date nights", "shared")

    # Some chat history
    await log_message(test_db, a_id, "user", "I had a tough day")
    await log_message(test_db, a_id, "assistant", "I hear you. Want to talk about it?")
    await log_message(test_db, b_id, "user", "Money is tight this month")
    await log_message(test_db, b_id, "assistant", "That sounds stressful. Let's think about this.")

    return a_id, b_id
