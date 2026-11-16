"""Tests for the crypto module — Fernet encrypt/decrypt and key masking."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

from couples_bot.crypto import decrypt_value, encrypt_value, mask_key


@pytest.fixture
def fernet_key() -> str:
    """Generate a valid Fernet key for testing."""
    return Fernet.generate_key().decode()


class TestEncryptDecrypt:
    def test_round_trip(self, fernet_key):
        plaintext = "AIzaSyD-test-key-1234567890"
        token = encrypt_value(plaintext, fernet_key)
        assert token != plaintext  # should be encrypted
        assert decrypt_value(token, fernet_key) == plaintext

    def test_different_keys_fail(self, fernet_key):
        other_key = Fernet.generate_key().decode()
        token = encrypt_value("secret", fernet_key)
        with pytest.raises(InvalidToken):
            decrypt_value(token, other_key)

    def test_invalid_key_raises(self):
        with pytest.raises(Exception):
            encrypt_value("hello", "not-a-valid-fernet-key")

    def test_empty_string(self, fernet_key):
        token = encrypt_value("", fernet_key)
        assert decrypt_value(token, fernet_key) == ""

    def test_unicode_content(self, fernet_key):
        plaintext = "తెలుగు-key-🔑"
        token = encrypt_value(plaintext, fernet_key)
        assert decrypt_value(token, fernet_key) == plaintext


class TestMaskKey:
    def test_normal_key(self):
        assert mask_key("AIzaSyD12345xY9z") == "AIza****xY9z"

    def test_short_key(self):
        assert mask_key("short") == "****"

    def test_exactly_10_chars(self):
        assert mask_key("1234567890") == "1234****7890"

    def test_long_key(self):
        result = mask_key("abcdefghijklmnopqrstuvwxyz")
        assert result.startswith("abcd")
        assert result.endswith("wxyz")
        assert "****" in result
