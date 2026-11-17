"""Context builder — assembles the prompt context for a user.

Re-exports from db.py during migration. New code should import from here.
"""
from couples_bot.db import (  # noqa: F401
    get_user_context_data,
    _get_context_budgets,
    _apply_word_cap,
)
