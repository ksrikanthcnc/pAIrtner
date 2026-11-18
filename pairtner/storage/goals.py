"""Goals — personal and shared.

Canonical implementations of goal-related database operations.
"""
from __future__ import annotations

from typing import Any

from pairtner.storage.helpers import _fetch_one, _fetch_all, _execute


async def add_user_goal(db_path: str, user_id: int, text: str) -> int:
    """Create a personal goal; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO user_goals (user_id, text) VALUES (?, ?)",
        (user_id, text),
    )


async def add_shared_goal(
    db_path: str, couple_id: str, created_by: int, text: str
) -> int:
    """Create a shared goal; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO shared_goals (couple_id, text, created_by) VALUES (?, ?, ?)",
        (couple_id, text, created_by),
    )


async def get_active_goals(
    db_path: str, user_id: int
) -> list[dict[str, Any]]:
    """Get active personal goals for a user."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM user_goals WHERE user_id = ? AND status = 'active' "
        "ORDER BY created_at",
        (user_id,),
    )


async def get_shared_goals(
    db_path: str, couple_id: str
) -> list[dict[str, Any]]:
    """Get active shared goals for a couple."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM shared_goals WHERE couple_id = ? AND status = 'active' "
        "ORDER BY created_at",
        (couple_id,),
    )


async def update_goal_status(
    db_path: str, goal_id: int, status: str, progress: str | None = None
) -> None:
    """Update a personal goal's status and optional progress."""
    if progress is not None:
        await _execute(
            db_path,
            "UPDATE user_goals SET status = ?, progress = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, progress, goal_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE user_goals SET status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, goal_id),
        )


async def update_shared_goal_status(
    db_path: str, goal_id: int, status: str, progress: str | None = None
) -> None:
    """Update a shared goal's status and optional progress."""
    if progress is not None:
        await _execute(
            db_path,
            "UPDATE shared_goals SET status = ?, progress = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, progress, goal_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE shared_goals SET status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, goal_id),
        )


async def get_goal_by_id(db_path: str, goal_id: int) -> dict[str, Any] | None:
    """Return a personal goal by its id."""
    return await _fetch_one(
        db_path, "SELECT * FROM user_goals WHERE id = ?", (goal_id,)
    )


async def get_shared_goal_by_id(db_path: str, goal_id: int) -> dict[str, Any] | None:
    """Return a shared goal by its id."""
    return await _fetch_one(
        db_path, "SELECT * FROM shared_goals WHERE id = ?", (goal_id,)
    )
