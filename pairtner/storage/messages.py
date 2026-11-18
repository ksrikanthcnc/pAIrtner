"""Message log — conversation history storage and retrieval.

Canonical implementations of message-related database operations.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from pairtner.storage.helpers import _fetch_all, _execute


async def log_message(
    db_path: str, user_id: int, role: str, content: str
) -> int:
    """Append a message to the conversation log and return its ID."""
    return await _execute(
        db_path,
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )


async def get_recent_messages(
    db_path: str, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the last *limit* messages for a user (most recent first)."""
    return await _fetch_all(
        db_path,
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


async def get_full_history(
    db_path: str, user_id: int, limit: int = 500, offset: int = 0,
) -> list[dict[str, Any]]:
    """Return the full message history for a user (oldest first), with timestamps.

    Used by the PWA to reconstruct the full conversation on auth.
    Excludes soft-deleted messages.
    """
    return await _fetch_all(
        db_path,
        "SELECT id, role, content, created_at FROM messages "
        "WHERE user_id = ? AND deleted_at IS NULL ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


async def get_history_after_id(
    db_path: str, user_id: int, after_id: int, limit: int = 500,
) -> list[dict[str, Any]]:
    """Return messages after a given ID (for delta fetching by the PWA)."""
    return await _fetch_all(
        db_path,
        "SELECT id, role, content, created_at FROM messages "
        "WHERE user_id = ? AND id > ? AND deleted_at IS NULL ORDER BY created_at ASC LIMIT ?",
        (user_id, after_id, limit),
    )


async def soft_delete_message(
    db_path: str, user_id: int, message_id: int,
) -> bool:
    """Soft-delete a message (set deleted_at). Returns True if found and owned."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "UPDATE messages SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (message_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
