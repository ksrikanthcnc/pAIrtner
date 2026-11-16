"""Relay feedback loop — post-delivery feedback and dampening adjustment.

After a relay is delivered, the sender's next message triggers a feedback
request asking whether the relay landed well.  Aggregate feedback drives
dampening suggestions.
"""

from __future__ import annotations

from typing import Any

from couples_bot import db as db_mod
from couples_bot.config import Config
from couples_bot.i18n import t


async def check_for_feedback_opportunity(
    db_path: str, user_id: int
) -> dict[str, Any] | None:
    """Return the most recent sent relay needing feedback, or ``None``."""
    rows = await db_mod.get_pending_needing_feedback(db_path, user_id)
    return rows[0] if rows else None


def generate_feedback_request(
    pending_row: dict[str, Any], lang: str
) -> tuple[str, list[list[tuple[str, str]]]]:
    """Format a feedback request message with inline buttons.

    Returns ``(text, button_rows)`` where each button row is a list of
    ``(label, callback_data)`` tuples.
    """
    draft = pending_row.get("edited_draft") or pending_row["draft"]
    # Truncate for display
    topic = draft[:60] + "…" if len(draft) > 60 else draft
    text = t(lang, "feedback_ask", topic=topic)
    buttons = [[
        (t(lang, "feedback_yes"), f"fb_y:{pending_row['id']}"),
        (t(lang, "feedback_not_sure"), f"fb_m:{pending_row['id']}"),
        (t(lang, "feedback_no"), f"fb_n:{pending_row['id']}"),
    ]]
    return text, buttons


async def adjust_dampening_from_feedback(
    db_path: str, user_id: int, config: Config
) -> float | None:
    """Suggest a dampening adjustment based on feedback history.

    Returns a signed float (e.g. -0.05 or +0.05) or ``None`` if no
    significant pattern.  Does NOT auto-apply — the caller decides.
    """
    stats = await db_mod.get_feedback_stats(db_path, user_id)
    total = sum(stats.values())
    if total < 3:
        return None

    yes_pct = stats.get("yes", 0) / total
    no_pct = stats.get("no", 0) / total

    if yes_pct > 0.7:
        return -0.05  # consistent positive → lower dampening
    if no_pct > 0.3:
        return 0.05   # consistent negative → raise dampening
    return None
