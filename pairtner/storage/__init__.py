"""Storage package — modular database layer.

Organized by domain:
    from pairtner.storage.users import get_user, ensure_user
    from pairtner.storage.facts import add_fact, get_active_facts
    from pairtner.storage.messages import log_message, get_full_history
    from pairtner.storage.pending import create_pending, get_deliverable
    from pairtner.storage.couples import make_couple_id, create_conflict
    from pairtner.storage.profiles import set_user_mood, record_pattern
    from pairtner.storage.goals import add_user_goal, get_active_goals
    from pairtner.storage.context import get_user_context_data
    from pairtner.storage.settings import get_runtime_setting
    from pairtner.storage.outbox import add_outbox_event
    from pairtner.storage.schema import init_db

All functions are also available via `from pairtner.db import ...`
for backward compatibility during migration.
"""

from pairtner.storage.schema import SCHEMA_SQL, init_db  # noqa: F401
from pairtner.storage.helpers import (  # noqa: F401
    _now_iso, _row_to_dict, _fetch_one, _fetch_all, _execute,
)
