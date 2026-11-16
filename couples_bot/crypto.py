"""Encryption helpers — Fernet-based at-rest encryption for sensitive config."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


def encrypt_value(plaintext: str, key: str) -> str:
    """Encrypt *plaintext* with a Fernet *key* and return the token as a string.

    *key* must be a valid 32-byte URL-safe base64 Fernet key.
    """
    f = Fernet(key.encode())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(token: str, key: str) -> str:
    """Decrypt a Fernet *token* using *key* and return the plaintext string.

    Raises ``cryptography.fernet.InvalidToken`` on bad key or corrupt data.
    """
    f = Fernet(key.encode())
    return f.decrypt(token.encode()).decode()


def mask_key(api_key: str) -> str:
    """Partially mask an API key for safe display.

    Shows first 4 and last 4 characters, masks the middle with asterisks.
    Keys shorter than 10 characters are fully masked.
    """
    if len(api_key) < 10:
        return "****"
    return api_key[:4] + "****" + api_key[-4:]
