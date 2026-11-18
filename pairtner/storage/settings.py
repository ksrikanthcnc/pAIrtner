"""Runtime settings and user preferences.

Canonical implementations of settings-related database operations.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from pairtner.storage.helpers import _fetch_one, _fetch_all, _execute


async def get_runtime_setting(
    db_path: str, key: str, default: str | None = None,
) -> str | None:
    """Get a runtime setting from user_meta (user_id=0 for global settings)."""
    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = 0 AND key = ?",
        (key,),
    )
    return row["value"] if row else default


async def set_runtime_setting(
    db_path: str, key: str, value: str,
) -> None:
    """Set a global runtime setting (user_id=0)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (0, ?, ?, CURRENT_TIMESTAMP)",
            (key, value),
        )
        await db.commit()


async def get_all_runtime_settings(db_path: str) -> dict[str, str]:
    """Get all global runtime settings."""
    rows = await _fetch_all(
        db_path,
        "SELECT key, value FROM user_meta WHERE user_id = 0",
    )
    return {r["key"]: r["value"] for r in rows}


async def get_user_preference(
    db_path: str, user_id: int, key: str, default: str | None = None,
) -> str | None:
    """Get a per-user preference from user_meta."""
    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = ? AND key = ?",
        (user_id, key),
    )
    return row["value"] if row else default


async def set_user_preference(
    db_path: str, user_id: int, key: str, value: str,
) -> None:
    """Set a per-user preference."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, key, value),
        )
        await db.commit()
