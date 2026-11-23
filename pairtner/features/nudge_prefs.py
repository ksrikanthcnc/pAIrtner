"""Natural-language nudge preferences ("nudge me less", "no nudges on weekends").

The LLM also extracts these as core facts; this module additionally sets the
whitelisted per-user setting directly so the change takes effect immediately.
"""

from __future__ import annotations

import re

from pairtner.features.settings_applier import apply_settings_ops

_LESS = re.compile(
    r"\b(nudge|remind|ping)(\s+me)?\s+(less|weekly|once a week)\b"
    r"|\b(fewer|less)\s+(nudges|reminders)\b",
    re.IGNORECASE,
)
_OFF = re.compile(
    r"\b(stop|no more|turn off|disable)\s+(nudg\w+|reminders)\b"
    r"|\b(don'?t|do not)\s+nudge\s+me\b|\bno\s+nudges\b(?!\s+on\s+week)",
    re.IGNORECASE,
)
_WEEKENDS = re.compile(
    r"\bno\s+(nudges|reminders)\s+(on|during)\s+(the\s+)?weekends?\b"
    r"|\b(don'?t|do not|stop)\s+(nudge|remind)\w*\s+me\s+(on|during)\s+(the\s+)?weekends?\b",
    re.IGNORECASE,
)


def parse_nudge_request(text: str) -> list[tuple[str, str]]:
    """Return ``[(setting_key, value), ...]`` implied by *text* (may be empty)."""
    if not text:
        return []
    ops: list[tuple[str, str]] = []
    if _WEEKENDS.search(text):
        ops.append(("nudge_skip_weekends", "true"))
    elif _OFF.search(text):
        ops.append(("nudge_frequency", "off"))
    elif _LESS.search(text):
        ops.append(("nudge_frequency", "weekly"))
    return ops


async def apply_nudge_request(db_path: str, user_id: int, text: str) -> list[dict]:
    """Parse *text* and apply any nudge settings; returns the applied results."""
    ops = [{"key": k, "value": v} for k, v in parse_nudge_request(text)]
    if not ops:
        return []
    return await apply_settings_ops(db_path, user_id, ops)
