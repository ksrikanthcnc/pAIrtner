"""Goals — personal and shared.

Re-exports from db.py during migration. New code should import from here.
"""
from couples_bot.db import (  # noqa: F401
    add_user_goal, add_shared_goal,
    get_active_goals, get_shared_goals,
    update_goal_status, update_shared_goal_status,
    get_goal_by_id, get_shared_goal_by_id,
)
