"""Conflict detection and resolution tracking (FEAT-002).

Provides heuristics for:
- Opening a conflict (harsh tone, relay frequency spike)
- Detecting conflict resolution (mood shift, inactivity timeout)
- Extracting a theme from LLM response fields
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from couples_bot import db as db_mod

logger = logging.getLogger(__name__)


async def detect_conflict_opening(
    db_path: str,
    couple_id: str,
    user_id: int,
    llm_response: dict[str, Any],
) -> tuple[bool, str | None]:
    """Check if the current interaction signals a conflict opening.

    Returns ``(is_conflict, theme_suggestion)``.  A conflict is detected
    when either:

    * The LLM reports an angry mood or horsemen patterns, **or**
    * The sender has created more than 3 relay drafts in the last hour
      (relay frequency spike).
    """
    # Check 1: harsh tone — angry mood or horsemen detected
    mood = llm_response.get("detected_mood", "")
    horsemen = llm_response.get("horsemen_detected") or []
    harsh_tone = mood == "angry" or len(horsemen) > 0

    # Check 2: relay frequency spike — >3 relays in 1 hour
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    recent_count = await db_mod.get_recent_pending_count(
        db_path, user_id, cutoff,
    )
    relay_spike = recent_count > 3

    is_conflict = harsh_tone or relay_spike

    if not is_conflict:
        return False, None

    # Don't duplicate if there's already an open conflict with a similar theme
    existing = await db_mod.get_open_conflicts(db_path, couple_id)
    theme = extract_conflict_theme(llm_response)
    for c in existing:
        if c["theme"].lower() == theme.lower():
            return False, None

    return True, theme


async def detect_conflict_resolution(
    db_path: str,
    conflict_id: int,
    llm_response: dict[str, Any] | None = None,
    timeout_hours: int = 48,
) -> bool:
    """Check if an open conflict should be marked as resolved.

    Resolution signals:

    * **Timeout**: no activity on the conflict for *timeout_hours*+.
    * **Mood shift**: the latest LLM response shows a positive mood with
      no horsemen detected (only checked when *llm_response* is provided).
    """
    conflict = await db_mod.get_conflict_by_id(db_path, conflict_id)
    if not conflict or conflict["status"] != "open":
        return False

    # Check 1: inactivity timeout
    opened = conflict["opened_at"]
    try:
        opened_dt = datetime.fromisoformat(opened)
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        opened_dt = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)
    hours_open = (now - opened_dt).total_seconds() / 3600
    if hours_open >= timeout_hours:
        return True

    # Check 2: positive mood shift (only when LLM response available)
    if llm_response:
        mood = llm_response.get("detected_mood", "")
        horsemen = llm_response.get("horsemen_detected") or []
        if mood in ("happy", "reflective") and not horsemen:
            return True

    return False


def extract_conflict_theme(llm_response: dict[str, Any]) -> str:
    """Extract a short theme string from LLM response fields.

    Checks partner_themes, patterns_observed, and horsemen_detected
    in that order, falling back to a generic label.
    """
    # Try partner_themes first
    themes = llm_response.get("partner_themes") or []
    if themes and isinstance(themes[0], str) and themes[0].strip():
        return themes[0].strip()

    # Try patterns_observed
    patterns = llm_response.get("patterns_observed") or []
    if patterns and isinstance(patterns[0], str) and patterns[0].strip():
        return patterns[0].strip()

    # Fall back to horsemen type
    horsemen = llm_response.get("horsemen_detected") or []
    if horsemen and isinstance(horsemen[0], dict):
        h_type = horsemen[0].get("type", "")
        if h_type.strip():
            return h_type.strip()

    return "unresolved tension"


async def check_conflict_resolutions(
    db_path: str,
    couple_id: str,
    timeout_hours: int = 48,
) -> int:
    """Check all open conflicts for a couple and resolve timed-out ones.

    Returns the number of conflicts resolved.
    """
    open_conflicts = await db_mod.get_open_conflicts(db_path, couple_id)
    resolved_count = 0
    for conflict in open_conflicts:
        should_resolve = await detect_conflict_resolution(
            db_path, conflict["id"], timeout_hours=timeout_hours,
        )
        if should_resolve:
            now_iso = datetime.now(timezone.utc).isoformat()
            await db_mod.update_conflict_status(
                db_path, conflict["id"], "resolved", resolved_at=now_iso,
            )
            resolved_count += 1
            logger.info(
                "Conflict %d ('%s') auto-resolved for couple %s",
                conflict["id"], conflict["theme"], couple_id,
            )
    return resolved_count
