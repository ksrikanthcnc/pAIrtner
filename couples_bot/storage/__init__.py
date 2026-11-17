"""Storage package — modular database layer.

Organized by domain:
    from couples_bot.storage.users import get_user, ensure_user
    from couples_bot.storage.facts import add_fact, get_active_facts
    from couples_bot.storage.messages import log_message, get_full_history
    from couples_bot.storage.pending import create_pending, get_deliverable
    from couples_bot.storage.couples import make_couple_id, create_conflict
    from couples_bot.storage.profiles import set_user_mood, record_pattern
    from couples_bot.storage.goals import add_user_goal, get_active_goals
    from couples_bot.storage.context import get_user_context_data
    from couples_bot.storage.settings import get_runtime_setting
    from couples_bot.storage.outbox import add_outbox_event
    from couples_bot.storage.schema import init_db

All functions are also available via `from couples_bot.db import ...`
for backward compatibility during migration.
"""

from couples_bot.storage.schema import SCHEMA_SQL, init_db  # noqa: F401
from couples_bot.storage.helpers import (  # noqa: F401
    _now_iso, _row_to_dict, _fetch_one, _fetch_all, _execute,
)
