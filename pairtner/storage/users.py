"""User management — CRUD, pairing, language.

Canonical implementations of user-related database operations.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from pairtner.storage.helpers import _fetch_one, _fetch_all, _execute


# ---------------------------------------------------------------------------
# Core user CRUD
# ---------------------------------------------------------------------------

async def get_user(db_path: str, tg_id: int) -> dict[str, Any] | None:
    """Return the user row for *tg_id*, or ``None`` if not found."""
    return await _fetch_one(
        db_path, "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
    )


async def create_user(
    db_path: str, tg_id: int, name: str | None = None, lang: str = "en"
) -> None:
    """Insert a new user row, ignoring if one already exists."""
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO users (tg_id, name, lang) VALUES (?, ?, ?)",
        (tg_id, name, lang),
    )


async def ensure_user(
    db_path: str, tg_id: int, name: str | None = None
) -> dict[str, Any]:
    """Get or create a user, returning the user row."""
    user = await get_user(db_path, tg_id)
    if user is None:
        await create_user(db_path, tg_id, name)
        user = await get_user(db_path, tg_id)
    return user  # type: ignore[return-value]


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
