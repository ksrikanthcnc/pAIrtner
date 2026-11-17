"""REST outbox events for long-poll delivery.

Re-exports from db.py during migration. New code should import from here.
"""
from couples_bot.db import (  # noqa: F401
    add_outbox_event, get_outbox_events, prune_outbox,
)
