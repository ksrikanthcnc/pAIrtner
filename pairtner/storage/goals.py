"""Goals — personal and shared, unified in one table.

The ``goals`` table has a ``scope`` column: 'personal' or 'shared'.
Personal goals have ``user_id`` set; shared goals also have ``couple_id``.
Function names are kept for backward compatibility.
"""
from __future__ import annotations

from typing import Any

from pairtner.storage.helpers import _fetch_one, _fetch_all, _execute


async def add_user_goal(db_path: str, user_id: int, text: str) -> int:
    """Create a personal goal; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO goals (scope, user_id, created_by, text) VALUES ('personal', ?, ?, ?)",
        (user_id, user_id, text),
    )


async def add_shared_goal(
    db_path: str, couple_id: str, created_by: int, text: str
) -> int:
    """Create a shared goal; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO goals (scope, user_id, couple_id, created_by, text) "
        "VALUES ('shared', ?, ?, ?, ?)",
        (created_by, couple_id, created_by, text),
    )


async def get_active_goals(
    db_path: str, user_id: int
) -> list[dict[str, Any]]:
    """Get active personal goals for a user."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM goals WHERE user_id = ? AND scope = 'personal' AND status = 'active' "
        "ORDER BY created_at",
        (user_id,),
    )


async def get_shared_goals(
    db_path: str, couple_id: str
) -> list[dict[str, Any]]:
    """Get active shared goals for a couple."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM goals WHERE couple_id = ? AND scope = 'shared' AND status = 'active' "
        "ORDER BY created_at",
        (couple_id,),
    )


async def update_goal_status(
    db_path: str, goal_id: int, status: str, progress: str | None = None
) -> None:
    """Update a goal's status and optional progress (works for both personal and shared)."""
    if progress is not None:
        await _execute(
            db_path,
            "UPDATE goals SET status = ?, progress = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, progress, goal_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE goals SET status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, goal_id),
        )


async def update_shared_goal_status(
    db_path: str, goal_id: int, status: str, progress: str | None = None
) -> None:
    """Update a shared goal's status. Delegates to update_goal_status (unified table)."""
    await update_goal_status(db_path, goal_id, status, progress)


async def get_goal_by_id(db_path: str, goal_id: int) -> dict[str, Any] | None:
    """Return a goal by its id (personal or shared)."""
    return await _fetch_one(
        db_path, "SELECT * FROM goals WHERE id = ?", (goal_id,)
    )


async def get_shared_goal_by_id(db_path: str, goal_id: int) -> dict[str, Any] | None:
    """Return a shared goal by its id. Delegates to get_goal_by_id (unified table)."""
    return await get_goal_by_id(db_path, goal_id)
