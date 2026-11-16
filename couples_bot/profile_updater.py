"""Continuous profile auto-updates from LLM response extras.

Processes the extended fields in the LLM JSON response (detected_mood,
patterns_observed, partner_themes, articulation_note) and persists them
to the database.  Designed to be lightweight — no extra LLM calls, just
DB writes from fields already in the response.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from couples_bot import db as db_mod

logger = logging.getLogger(__name__)


async def process_llm_extras(
    db_path: str,
    user_id: int,
    llm_response: dict[str, Any],
    config: Any,
) -> None:
    """Extract and persist intelligence from LLM response extras.

    This function is fire-and-forget: it never raises, so it cannot
    break the main response flow.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database.
    user_id : int
        The Telegram user id.
    llm_response : dict
        The parsed LLM response containing extended fields.
    config : Config
        Bot configuration.
    """
    try:
        await _process(db_path, user_id, llm_response, config)
    except Exception:
        logger.exception(
            "Error processing LLM extras for user %d", user_id,
        )


async def _process(
    db_path: str,
    user_id: int,
    llm_response: dict[str, Any],
    config: Any,
) -> None:
    """Inner implementation — may raise."""
    # (a) Detected mood → set_user_mood
    mood = llm_response.get("detected_mood")
    if mood and mood != "neutral":
        hours = getattr(config, "mood_default_expiry_hours", 2)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=hours)
        ).isoformat()
        style = getattr(config, "mood_styles", {}).get(mood, {})
        await db_mod.set_user_mood(
            db_path,
            user_id,
            mood,
            expires_at,
            dampening_override=style.get("dampening_override"),
            response_length=style.get("response_length", "normal"),
        )

    # (b) Patterns observed → record_pattern
    patterns = llm_response.get("patterns_observed") or []
    for pattern in patterns:
        if isinstance(pattern, str) and pattern.strip():
            await db_mod.record_pattern(db_path, user_id, pattern.strip())

    # (c) Partner themes → add_partner_perception
    themes = llm_response.get("partner_themes") or []
    if themes:
        user = await db_mod.get_user(db_path, user_id)
        partner_id = (user or {}).get("partner_id")
        if partner_id:
            for theme in themes:
                if isinstance(theme, str) and theme.strip():
                    await db_mod.add_partner_perception(
                        db_path,
                        user_id=user_id,
                        about_id=partner_id,
                        theme=theme.strip(),
                    )

    # (d) Articulation note → record as pattern
    note = llm_response.get("articulation_note")
    if isinstance(note, str) and note.strip():
        await db_mod.record_pattern(
            db_path, user_id, note.strip(), pattern_type="articulation",
        )

    # (e) Horsemen detection → record patterns with pattern_type='horsemen'
    horsemen = llm_response.get("horsemen_detected") or []
    if getattr(config, "horsemen_enabled", True):
        for item in horsemen:
            if isinstance(item, dict):
                h_type = item.get("type", "")
                evidence = item.get("evidence", "")
                pattern_text = f"{h_type}: {evidence}" if evidence else h_type
                if pattern_text.strip():
                    await db_mod.record_pattern(
                        db_path, user_id, pattern_text.strip(),
                        pattern_type="horsemen",
                    )

    # (f) Record interaction score based on mood + horsemen
    user = await db_mod.get_user(db_path, user_id)
    partner_id = (user or {}).get("partner_id")
    if partner_id:
        couple_id = db_mod.make_couple_id(user_id, partner_id)
        if horsemen:
            await db_mod.record_interaction(
                db_path, couple_id, user_id, "negative", source="llm",
            )
        elif mood in ("happy", "reflective"):
            await db_mod.record_interaction(
                db_path, couple_id, user_id, "positive", source="llm",
            )

    # (g) Voice traits → store learned voice profile
    voice_traits = llm_response.get("voice_traits")
    if isinstance(voice_traits, dict) and voice_traits:
        import json as _json
        await db_mod.update_learned_voice(
            db_path, user_id, _json.dumps(voice_traits),
        )

    # (h) Always increment message count for voice maturity tracking
    await db_mod.increment_message_count(db_path, user_id)
