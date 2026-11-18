"""Background analysis — patterns, horsemen, voice traits, partner themes.

Runs periodically (every N messages) on the heavy model. Not per-message.
Reads the contract.md § analysis schema and returns structured observations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pairtner import db as db_mod
from pairtner.llm import LLMClient
from pairtner.core.prompts import build_analysis_prompt

log = logging.getLogger(__name__)

# Minimum messages needed before running analysis
MIN_MESSAGES_FOR_ANALYSIS = 5


async def should_run_analysis(
    db_path: str, user_id: int, interval: int = 10,
) -> bool:
    """Check if we have enough new messages since the last analysis."""
    total = await _count_messages_since_last_analysis(db_path, user_id)
    return total >= interval


async def run_analysis(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    config: Any = None,
    message_count: int = 30,
    api_key_override: str | None = None,
) -> dict[str, Any] | None:
    """Run background analysis on recent messages.

    Returns the parsed analysis result, or None if not enough data.
    """
    messages = await db_mod.get_recent_messages(db_path, user_id, limit=message_count)
    if len(messages) < MIN_MESSAGES_FOR_ANALYSIS:
        log.info("analysis: user %s has only %d messages, need %d", user_id, len(messages), MIN_MESSAGES_FOR_ANALYSIS)
        return None

    # Build the analysis prompt
    system, content = build_analysis_prompt(messages, user_id)

    # Use heavy model if available
    model = None
    if config and hasattr(config, "model_heavy") and config.model_heavy:
        model = config.model_heavy[0]

    result = await llm.generate(
        content, system, model=model, api_key_override=api_key_override,
    )

    parsed = _parse_analysis(result)
    if parsed:
        await _save_analysis(db_path, user_id, parsed)
        log.info(
            "analysis: user %s — %d patterns, %d horsemen, %d themes",
            user_id,
            len(parsed.get("patterns_observed", [])),
            len(parsed.get("horsemen_detected", [])),
            len(parsed.get("partner_themes", [])),
        )
    return parsed


def _parse_analysis(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract structured analysis fields from LLM response."""
    # The LLM returns the analysis schema directly
    parsed: dict[str, Any] = {}

    for key in ("patterns_observed", "horsemen_detected", "partner_themes"):
        val = result.get(key)
        if isinstance(val, list):
            parsed[key] = val
        elif isinstance(val, str):
            try:
                parsed[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                parsed[key] = []
        else:
            parsed[key] = []

    voice = result.get("voice_traits")
    if isinstance(voice, dict):
        parsed["voice_traits"] = voice
    elif isinstance(voice, str):
        try:
            parsed["voice_traits"] = json.loads(voice)
        except (json.JSONDecodeError, TypeError):
            parsed["voice_traits"] = {}
    else:
        parsed["voice_traits"] = {}

    return parsed if any(parsed[k] for k in parsed) else None


async def _save_analysis(
    db_path: str, user_id: int, analysis: dict[str, Any],
) -> None:
    """Save analysis results to the DB."""
    # Save patterns as facts with kind=partner_view
    for pattern in analysis.get("patterns_observed", []):
        text = pattern if isinstance(pattern, str) else str(pattern)
        if text:
            await db_mod.add_fact(
                db_path, user_id, f"[pattern] {text}", "private",
                fact_type="situational",
            )
            # Update the kind to partner_view via direct SQL
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE facts SET kind = 'partner_view' "
                    "WHERE owner_id = ? AND text = ? AND kind = 'working'",
                    (user_id, f"[pattern] {text}"),
                )
                await db.commit()

    # Save horsemen detections
    for h in analysis.get("horsemen_detected", []):
        if isinstance(h, dict):
            htype = h.get("type", "unknown")
            evidence = h.get("evidence", "")
            suggestion = h.get("suggestion", "")
            text = f"[horseman:{htype}] {evidence}"
            if suggestion:
                text += f" → {suggestion}"
            await db_mod.add_fact(
                db_path, user_id, text, "private",
                fact_type="transient",
            )

    # Save partner themes
    for theme in analysis.get("partner_themes", []):
        text = theme if isinstance(theme, str) else str(theme)
        if text:
            await db_mod.add_fact(
                db_path, user_id, f"[theme] {text}", "private",
                fact_type="situational",
            )

    # Update voice traits in user profile
    voice = analysis.get("voice_traits", {})
    if voice:
        await db_mod.update_voice_traits(db_path, user_id, voice)

    # Record the analysis timestamp
    await _record_analysis_run(db_path, user_id)


async def _count_messages_since_last_analysis(
    db_path: str, user_id: int,
) -> int:
    """Count user messages since the last analysis run."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT value FROM user_meta WHERE user_id = ? AND key = 'last_analysis_msg_id'",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row and row["value"]:
            last_id = int(row["value"])
            cursor2 = await db.execute(
                "SELECT COUNT(*) as cnt FROM messages "
                "WHERE user_id = ? AND role = 'user' AND id > ?",
                (user_id, last_id),
            )
        else:
            cursor2 = await db.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE user_id = ? AND role = 'user'",
                (user_id,),
            )
        count_row = await cursor2.fetchone()
        return count_row["cnt"] if count_row else 0


async def _record_analysis_run(db_path: str, user_id: int) -> None:
    """Record that an analysis was run now (stores the latest message ID)."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT MAX(id) as max_id FROM messages WHERE user_id = ? AND role = 'user'",
            (user_id,),
        )
        row = await cursor.fetchone()
        max_id = row["max_id"] if row else 0
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (?, 'last_analysis_msg_id', ?, CURRENT_TIMESTAMP)",
            (user_id, str(max_id)),
        )
        await db.commit()


async def update_voice_traits(db_path: str, user_id: int, traits: dict) -> None:
    """Convenience re-export for tests."""
    await db_mod.update_voice_traits(db_path, user_id, traits)
