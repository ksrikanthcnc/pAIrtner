"""Setup / per-partner key tests (FEAT-004)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from couples_bot.crypto import decrypt_value, encrypt_value
from couples_bot.db import (
    get_couple_config,
    make_couple_id,
    set_couple_config,
)
from couples_bot.handlers import BotService


@pytest.fixture
def fernet_key() -> str:
    """Generate a valid Fernet key for testing."""
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# /setup no args → show tier info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_no_args_shows_tier_info(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, _ = paired_users
    responses = await service.handle_setup(a_id, None)
    assert len(responses) == 1
    text = responses[0].text
    assert "global key" in text.lower() or "active tier" in text.lower()


# ---------------------------------------------------------------------------
# /setup my-key <key>
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_my_key_store(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    responses = await service.handle_setup(a_id, "my-key AAAA-my-personal-key")
    assert len(responses) == 1
    assert "personal" in responses[0].text.lower() or "stored" in responses[0].text.lower()

    couple_id = make_couple_id(a_id, b_id)
    stored = await get_couple_config(test_db, couple_id, f"gemini_key:{a_id}")
    assert stored is not None

    # Verify it decrypts back
    plaintext = decrypt_value(stored, fernet_key)
    assert plaintext == "AAAA-my-personal-key"


@pytest.mark.asyncio
async def test_setup_my_key_clear(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    await service.handle_setup(a_id, "my-key some-key")
    responses = await service.handle_setup(a_id, "my-key clear")
    assert len(responses) == 1
    assert "removed" in responses[0].text.lower() or "cleared" in responses[0].text.lower()

    couple_id = make_couple_id(a_id, b_id)
    stored = await get_couple_config(test_db, couple_id, f"gemini_key:{a_id}")
    assert stored is None


# ---------------------------------------------------------------------------
# /setup our-key <key>
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_our_key_store(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    responses = await service.handle_setup(a_id, "our-key BBBB-couple-key")
    assert len(responses) == 1
    assert "couple" in responses[0].text.lower() or "stored" in responses[0].text.lower()

    couple_id = make_couple_id(a_id, b_id)
    stored = await get_couple_config(test_db, couple_id, "gemini_key")
    assert stored is not None


@pytest.mark.asyncio
async def test_setup_our_key_clear(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    await service.handle_setup(a_id, "our-key some-key")
    responses = await service.handle_setup(a_id, "our-key clear")
    assert len(responses) == 1
    assert "removed" in responses[0].text.lower() or "cleared" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# Backward compat: /setup gemini_key <key> → maps to our-key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_gemini_key_backward_compat(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    responses = await service.handle_setup(a_id, "gemini_key CCCC-compat-key")
    assert len(responses) == 1
    assert "couple" in responses[0].text.lower() or "stored" in responses[0].text.lower()

    couple_id = make_couple_id(a_id, b_id)
    stored = await get_couple_config(test_db, couple_id, "gemini_key")
    assert stored is not None


# ---------------------------------------------------------------------------
# Three-tier key lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_tier_user_key_wins(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    """User's own key takes precedence over couple key."""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)

    await set_couple_config(
        test_db, couple_id, f"gemini_key:{a_id}",
        encrypt_value("user-personal-key", fernet_key),
    )
    await set_couple_config(
        test_db, couple_id, "gemini_key",
        encrypt_value("couple-shared-key", fernet_key),
    )

    resolved = await service._get_couple_api_key(a_id)
    assert resolved == "user-personal-key"


@pytest.mark.asyncio
async def test_three_tier_couple_key_fallback(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    """Couple key is used when no user key exists."""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)

    await set_couple_config(
        test_db, couple_id, "gemini_key",
        encrypt_value("couple-shared-key", fernet_key),
    )

    resolved = await service._get_couple_api_key(a_id)
    assert resolved == "couple-shared-key"


@pytest.mark.asyncio
async def test_three_tier_global_fallback(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    """Global key is used (returns None) when no user or couple key exists."""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, _ = paired_users
    resolved = await service._get_couple_api_key(a_id)
    assert resolved is None


@pytest.mark.asyncio
async def test_setup_tier_info_shows_personal_active(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    """No-arg setup shows personal tier when personal key is set."""
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)

    await set_couple_config(
        test_db, couple_id, f"gemini_key:{a_id}",
        encrypt_value("personal-key-123", fernet_key),
    )

    responses = await service.handle_setup(a_id, None)
    assert len(responses) == 1
    assert "personal" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_setup_invalid_subcommand(
    service: BotService, test_db, paired_users, fernet_key, monkeypatch,
):
    monkeypatch.setenv("DB_ENCRYPTION_KEY", fernet_key)
    a_id, _ = paired_users
    responses = await service.handle_setup(a_id, "invalid-cmd some-key")
    assert len(responses) == 1
    assert "Usage" in responses[0].text or "usage" in responses[0].text.lower()
