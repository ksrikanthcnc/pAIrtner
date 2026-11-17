"""Data classes and utility functions for the handler layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


@dataclass
class InlineButton:
    """A single inline keyboard button."""

    text: str
    callback_data: str


@dataclass
class BotResponse:
    """Transport-agnostic response from a handler method.

    Attributes:
        text: The message body.
        buttons: Optional inline keyboard (rows of buttons).
        document: Optional file attachment as ``(filename, text_content)``.
        target_user_id: Recipient override; ``None`` means reply to sender.
    """

    text: str
    buttons: list[list[InlineButton]] | None = None
    document: tuple[str, str] | None = None
    target_user_id: int | None = None


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

_TAGS = {"#keep", "#send", "#deep", "#now", "#blunt", "#honest"}


def parse_tags(text: str) -> tuple[set[str], str]:
    """Extract known hash-tags and return ``(tags, cleaned_text)``."""
    found: set[str] = set()
    clean = text
    for tag in _TAGS:
        if tag in clean.lower():
            found.add(tag)
            idx = clean.lower().find(tag)
            clean = clean[:idx] + clean[idx + len(tag) :]
    return found, clean.strip()


# ---------------------------------------------------------------------------
# Date / time formatting helpers
# ---------------------------------------------------------------------------


def format_fact_date(created_at: str | None) -> str:
    """Format a fact's ``created_at`` timestamp into ``'Jun 15'``."""
    if not created_at:
        return "?"
    try:
        dt = datetime.fromisoformat(created_at)
        return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return "?"


def format_relative_time(timestamp_str: str | None) -> str:
    """Format a timestamp into a relative string like ``'2h ago'``."""
    if not timestamp_str:
        return "never"
    try:
        dt = datetime.fromisoformat(timestamp_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        return f"{diff.days}d ago"
    except (ValueError, TypeError):
        return "unknown"
