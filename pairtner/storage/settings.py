"""Runtime settings and user preferences.

Re-exports from db.py during migration. New code should import from here.
"""
from pairtner.db import (  # noqa: F401
    get_runtime_setting, set_runtime_setting, get_all_runtime_settings,
    get_user_preference, set_user_preference,
)
