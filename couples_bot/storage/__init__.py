"""Storage package — modular database layer.

This package re-exports everything from couples_bot.db for backward
compatibility. New code should import from the specific submodules:

    from couples_bot.storage.schema import init_db
    from couples_bot.storage.helpers import _fetch_all, _execute
    from couples_bot.storage.users import get_user, ensure_user
    from couples_bot.storage.facts import add_fact, get_active_facts
    from couples_bot.storage.messages import log_message, get_recent_messages
    ...etc.

The full db.py re-export ensures existing code (handlers, tests, adapters)
continues to work during the migration.
"""

from __future__ import annotations

# Re-export schema and init
from couples_bot.storage.schema import SCHEMA_SQL, init_db  # noqa: F401

# Re-export helpers
from couples_bot.storage.helpers import (  # noqa: F401
    _now_iso,
    _row_to_dict,
    _fetch_one,
    _fetch_all,
    _execute,
)

# Everything from db.py (full backward compat during migration)
from couples_bot.db import *  # noqa: F401, F403
