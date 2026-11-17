"""REST user-id range, kept in its own module so db and adapters can share it."""
from __future__ import annotations

# Telegram ids are below 2**52; REST users get ids from this base upward.
# 2**52 + n stays below 2**53, so JavaScript numbers represent them exactly.
REST_ID_BASE = 2**52
