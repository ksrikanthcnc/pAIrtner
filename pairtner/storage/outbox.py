"""REST outbox events, auth tokens, and user-id allocation.

Canonical implementations of REST adapter database operations.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from pairtner.storage.helpers import _fetch_one, _fetch_all, _execute


# ---------------------------------------------------------------------------
# Auth tokens (adapter authentication)
# ---------------------------------------------------------------------------

async def create_auth_token(
    db_path: str, user_id: int, token: str
) -> None:
    """Store an authentication token for a user (upsert)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO auth_tokens (user_id, token) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "token = excluded.token, created_at = CURRENT_TIMESTAMP",
            (user_id, token),
        )
        await db.commit()


async def get_auth_token(
    db_path: str, user_id: int
) -> str | None:
    """Return the stored token for *user_id*, or ``None``."""
    row = await _fetch_one(
        db_path,
        "SELECT token FROM auth_tokens WHERE user_id = ?",
        (user_id,),
    )
    return row["token"] if row else None


async def validate_auth_token(
    db_path: str, user_id: int, token: str
) -> bool:
    """Check if a token matches the stored token for *user_id*.

    Uses constant-time comparison to prevent timing attacks.
    """
    import secrets as _secrets

    stored = await get_auth_token(db_path, user_id)
    if stored is None:
        return False
    return _secrets.compare_digest(stored, token)


# ---------------------------------------------------------------------------
# Durable outbox for long-poll delivery
# ---------------------------------------------------------------------------

async def add_outbox_event(
    db_path: str, user_id: int, kind: str, payload: str
) -> int:
    """Append an event for *user_id*; return its id (the poll cursor)."""
    return await _execute(
        db_path,
        "INSERT INTO rest_outbox (user_id, kind, payload) VALUES (?, ?, ?)",
        (user_id, kind, payload),
    )


async def get_outbox_events(
    db_path: str, user_id: int, after: int = 0, limit: int = 100
) -> list[dict[str, Any]]:
    """Events for *user_id* with id > *after*, oldest first."""
    return await _fetch_all(
        db_path,
        "SELECT id, kind, payload, created_at FROM rest_outbox "
        "WHERE user_id = ? AND id > ? ORDER BY id LIMIT ?",
        (user_id, after, limit),
    )


async def prune_outbox(db_path: str, older_than_days: int) -> int:
    """Delete outbox events older than *older_than_days*; return rows removed."""
    modifier = f"-{int(older_than_days)} days"
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM rest_outbox WHERE created_at < datetime('now', ?)",
            (modifier,),
        )
        await db.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# REST user-id allocation
# ---------------------------------------------------------------------------

async def allocate_rest_user(
    db_path: str, name: str, lang: str, base: int
) -> int:
    """Create a user with the next free id >= *base* and return that id.

    The SELECT MAX and the INSERT run on one connection inside an immediate
    transaction, so concurrent callers cannot receive the same id.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT MAX(tg_id) FROM users WHERE tg_id >= ?", (base,)
            ) as cur:
                row = await cur.fetchone()
            new_id = base if row is None or row[0] is None else row[0] + 1
            await db.execute(
                "INSERT INTO users (tg_id, name, lang) VALUES (?, ?, ?)",
                (new_id, name, lang),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return new_id
