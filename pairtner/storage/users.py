"""User management — CRUD, pairing, language.

Canonical implementations of user-related database operations.
"""
from __future__ import annotations

from typing import Any

import asyncio

import aiosqlite
import bcrypt

from pairtner.storage.helpers import _fetch_one, _fetch_all, _execute


# ---------------------------------------------------------------------------
# Core user CRUD
# ---------------------------------------------------------------------------

async def get_user(db_path: str, tg_id: int) -> dict[str, Any] | None:
    """Return the user row for *tg_id*, or ``None`` if not found."""
    return await _fetch_one(
        db_path, "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
    )


USER_MODES = ("couple", "solo")


async def create_user(
    db_path: str, tg_id: int, name: str | None = None, lang: str = "en",
    mode: str = "couple",
) -> None:
    """Insert a new user row, ignoring if one already exists."""
    if mode not in USER_MODES:
        raise ValueError(f"invalid user mode: {mode!r}")
    if mode == "couple":
        await _execute(
            db_path,
            "INSERT OR IGNORE INTO users (tg_id, name, lang) VALUES (?, ?, ?)",
            (tg_id, name, lang),
        )
        return
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO users (tg_id, name, lang, mode) VALUES (?, ?, ?, ?)",
        (tg_id, name, lang, mode),
    )


async def ensure_user(
    db_path: str, tg_id: int, name: str | None = None, mode: str = "couple",
) -> dict[str, Any]:
    """Get or create a user, returning the user row.

    *mode* only applies when the user is created; existing users keep theirs.
    """
    user = await get_user(db_path, tg_id)
    if user is None:
        await create_user(db_path, tg_id, name, mode=mode)
        user = await get_user(db_path, tg_id)
    return user  # type: ignore[return-value]


async def get_user_mode(db_path: str, tg_id: int) -> str:
    """Return ``'solo'`` or ``'couple'`` (default for unknown users/old DBs)."""
    try:
        row = await _fetch_one(
            db_path, "SELECT mode FROM users WHERE tg_id = ?", (tg_id,)
        )
    except aiosqlite.OperationalError:  # pre-migration DB without the column
        return "couple"
    return "solo" if (row or {}).get("mode") == "solo" else "couple"


async def set_user_mode(db_path: str, tg_id: int, mode: str) -> None:
    """Persist the user's mode (``couple`` | ``solo``)."""
    if mode not in USER_MODES:
        raise ValueError(f"invalid user mode: {mode!r}")
    await _execute(
        db_path, "UPDATE users SET mode = ? WHERE tg_id = ?", (mode, tg_id),
    )


_BCRYPT_MAX_BYTES = 72


def _pw_bytes(password: str) -> bytes:
    # bcrypt only uses the first 72 bytes (and bcrypt>=5 raises beyond that).
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


async def set_password(db_path: str, user_id: int, password: str) -> None:
    """Bcrypt-hash *password* and store it for *user_id*."""
    hashed = await asyncio.to_thread(
        lambda: bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")
    )
    await _execute(
        db_path,
        "UPDATE users SET password_hash = ? WHERE tg_id = ?",
        (hashed, user_id),
    )


async def verify_password(db_path: str, user_id: int, password: str) -> bool:
    """Return True if *password* matches the stored hash for *user_id*."""
    row = await _fetch_one(
        db_path, "SELECT password_hash FROM users WHERE tg_id = ?", (user_id,)
    )
    stored = (row or {}).get("password_hash")
    if not stored or not isinstance(password, str):
        return False
    try:
        return await asyncio.to_thread(
            bcrypt.checkpw, _pw_bytes(password), stored.encode("ascii")
        )
    except ValueError:
        return False  # malformed stored hash


async def find_user_ids_by_name(db_path: str, name: str) -> list[int]:
    """Return tg_ids of users with a password whose name matches (case-insensitive)."""
    rows = await _fetch_all(
        db_path,
        "SELECT tg_id FROM users WHERE lower(name) = lower(?) "
        "AND password_hash IS NOT NULL ORDER BY tg_id",
        (name,),
    )
    return [r["tg_id"] for r in rows]


async def update_user_lang(db_path: str, user_id: int, lang: str) -> None:
    """Update a user's language preference."""
    await _execute(
        db_path,
        "UPDATE users SET lang = ? WHERE tg_id = ?",
        (lang, user_id),
    )


async def set_partner(
    db_path: str, user_id: int, partner_id: int
) -> None:
    """Pair two users (sets partner_id on both)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET partner_id = ? WHERE tg_id = ?",
            (partner_id, user_id),
        )
        await db.execute(
            "UPDATE users SET partner_id = ? WHERE tg_id = ?",
            (user_id, partner_id),
        )
        await db.commit()


async def set_user_paused(
    db_path: str, user_id: int, paused: bool
) -> None:
    """Set or clear the paused flag for a user."""
    await _execute(
        db_path,
        "UPDATE users SET paused = ? WHERE tg_id = ?",
        (int(paused), user_id),
    )


async def get_partner_last_active(
    db_path: str, partner_id: int
) -> str | None:
    """Return the last message timestamp for the partner, or None."""
    row = await _fetch_one(
        db_path,
        "SELECT MAX(created_at) as last_active FROM messages WHERE user_id = ?",
        (partner_id,),
    )
    return row["last_active"] if row else None
