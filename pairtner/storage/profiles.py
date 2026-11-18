"""User profiles, moods, patterns, perceptions, interactions.

Canonical implementations of profile-related database operations.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from pairtner.storage.helpers import (
    _now_iso, _to_iso, _fetch_one, _fetch_all, _execute,
)


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------

async def get_user_profile(
    db_path: str, user_id: int
) -> dict[str, Any] | None:
    """Return the user profile row, or ``None`` if not found."""
    return await _fetch_one(
        db_path, "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
    )


async def ensure_user_profile(
    db_path: str, user_id: int
) -> dict[str, Any]:
    """Get or create a default profile row for *user_id*."""
    profile = await get_user_profile(db_path, user_id)
    if profile is None:
        await _execute(
            db_path,
            "INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)",
            (user_id,),
        )
        profile = await get_user_profile(db_path, user_id)
    return profile  # type: ignore[return-value]


async def update_user_profile(
    db_path: str, user_id: int, **fields: Any
) -> None:
    """Update one or more columns on a user's profile.

    Creates the profile row if it doesn't exist yet.
    """
    await ensure_user_profile(db_path, user_id)
    if not fields:
        return
    set_parts = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    await _execute(
        db_path,
        f"UPDATE user_profiles SET {set_parts}, updated_at = CURRENT_TIMESTAMP "
        f"WHERE user_id = ?",
        tuple(values),
    )


async def increment_message_count(db_path: str, user_id: int) -> None:
    """Increment the message_count on a user's profile."""
    await ensure_user_profile(db_path, user_id)
    await _execute(
        db_path,
        "UPDATE user_profiles SET message_count = message_count + 1, "
        "updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (user_id,),
    )


async def get_message_count(db_path: str, user_id: int) -> int:
    """Return the message_count for a user, or 0 if no profile exists."""
    profile = await get_user_profile(db_path, user_id)
    if profile is None:
        return 0
    return profile.get("message_count") or 0


async def update_voice_traits(
    db_path: str, user_id: int, traits: dict[str, Any],
) -> None:
    """Merge new voice traits into the existing learned_voice JSON."""
    profile = await ensure_user_profile(db_path, user_id)
    existing_raw = profile.get("learned_voice")
    existing: dict[str, Any] = {}
    if existing_raw:
        try:
            existing = json.loads(existing_raw) if isinstance(existing_raw, str) else existing_raw
        except (json.JSONDecodeError, TypeError):
            pass
    existing.update(traits)
    await update_user_profile(db_path, user_id, learned_voice=json.dumps(existing))


async def update_learned_voice(
    db_path: str, user_id: int, voice_json: str
) -> None:
    """Store a JSON string representing learned voice traits."""
    await ensure_user_profile(db_path, user_id)
    await _execute(
        db_path,
        "UPDATE user_profiles SET learned_voice = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (voice_json, user_id),
    )


# ---------------------------------------------------------------------------
# User mood styles
# ---------------------------------------------------------------------------

async def set_user_mood(
    db_path: str,
    user_id: int,
    mood: str,
    expires_at: str,
    **overrides: Any,
) -> int:
    """Insert a new mood row and return its id.

    *overrides* can include ``voice_override``, ``dampening_override``,
    ``response_length``.
    """
    cols = ["user_id", "mood", "expires_at"]
    vals: list[Any] = [user_id, mood, expires_at]
    for key in ("voice_override", "dampening_override", "response_length"):
        if key in overrides:
            cols.append(key)
            vals.append(overrides[key])
    placeholders = ", ".join("?" for _ in cols)
    col_str = ", ".join(cols)
    return await _execute(
        db_path,
        f"INSERT INTO user_mood_styles ({col_str}) VALUES ({placeholders})",
        tuple(vals),
    )


async def get_active_mood(
    db_path: str, user_id: int
) -> dict[str, Any] | None:
    """Return the latest non-expired mood row, or ``None``."""
    now = _now_iso()
    return await _fetch_one(
        db_path,
        "SELECT * FROM user_mood_styles "
        "WHERE user_id = ? AND expires_at > ? "
        "ORDER BY set_at DESC LIMIT 1",
        (user_id, now),
    )


async def clear_expired_moods(db_path: str) -> int:
    """Delete all expired mood rows. Returns count deleted."""
    now = _now_iso()
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM user_mood_styles WHERE expires_at <= ?", (now,)
        )
        await db.commit()
        return cur.rowcount


async def clear_user_mood(db_path: str, user_id: int) -> int:
    """Delete all mood rows for a user (active and expired). Returns count deleted."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM user_mood_styles WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# Partner perceptions
# ---------------------------------------------------------------------------

async def add_partner_perception(
    db_path: str,
    user_id: int,
    about_id: int,
    theme: str,
    sentiment: str = "neutral",
    confidence: float = 0.5,
) -> int:
    """Insert a partner perception; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO partner_perceptions "
        "(user_id, about_id, theme, sentiment, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, about_id, theme, sentiment, confidence),
    )


async def get_partner_perceptions(
    db_path: str, about_id: int
) -> list[dict[str, Any]]:
    """Get all perceptions ABOUT a user (for the other partner's prompt)."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM partner_perceptions WHERE about_id = ? "
        "ORDER BY updated_at DESC",
        (about_id,),
    )


async def get_perceptions_between(
    db_path: str, a: int, b: int, since: str
) -> list[dict[str, Any]]:
    """Perceptions in both directions (a about b, b about a) updated since ``since``."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM partner_perceptions "
        "WHERE ((user_id = ? AND about_id = ?) OR (user_id = ? AND about_id = ?)) "
        "AND updated_at >= ? ORDER BY updated_at DESC, id DESC",
        (a, b, b, a, since),
    )


async def update_perception_confidence(
    db_path: str, perception_id: int, confidence: float
) -> None:
    """Update the confidence score of a perception."""
    await _execute(
        db_path,
        "UPDATE partner_perceptions SET confidence = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (confidence, perception_id),
    )


# ---------------------------------------------------------------------------
# User patterns
# ---------------------------------------------------------------------------

async def record_pattern(
    db_path: str, user_id: int, pattern: str,
    pattern_type: str = "communication",
) -> int:
    """Upsert a communication pattern.

    If the pattern text already exists (case-insensitive) with the same
    pattern_type, increment frequency and update last_seen.  Otherwise
    insert a new row.  Returns the row id.
    """
    if pattern_type not in ("communication", "horsemen", "articulation"):
        pattern_type = "communication"
    existing = await _fetch_one(
        db_path,
        "SELECT id, frequency FROM user_patterns "
        "WHERE user_id = ? AND LOWER(pattern) = LOWER(?) AND pattern_type = ?",
        (user_id, pattern, pattern_type),
    )
    if existing:
        await _execute(
            db_path,
            "UPDATE user_patterns SET frequency = frequency + 1, "
            "last_seen = CURRENT_TIMESTAMP WHERE id = ?",
            (existing["id"],),
        )
        return existing["id"]
    return await _execute(
        db_path,
        "INSERT INTO user_patterns (user_id, pattern, pattern_type) VALUES (?, ?, ?)",
        (user_id, pattern, pattern_type),
    )


async def get_user_patterns(
    db_path: str, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Return top patterns by frequency for a user."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM user_patterns WHERE user_id = ? "
        "ORDER BY frequency DESC LIMIT ?",
        (user_id, limit),
    )


async def get_user_patterns_by_type(
    db_path: str, user_id: int, pattern_type: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Return top patterns filtered by pattern_type for a user."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM user_patterns WHERE user_id = ? AND pattern_type = ? "
        "ORDER BY frequency DESC LIMIT ?",
        (user_id, pattern_type, limit),
    )


# ---------------------------------------------------------------------------
# Interaction scores (Gottman 5:1 appreciation ratio)
# ---------------------------------------------------------------------------

async def record_interaction(
    db_path: str,
    couple_id: str,
    user_id: int,
    interaction_type: str,
    source: str = "llm",
) -> int:
    """Record a positive or negative interaction; return row id."""
    if interaction_type not in ("positive", "negative"):
        interaction_type = "negative"
    if source not in ("relay", "checkin", "nudge", "llm"):
        source = "llm"
    return await _execute(
        db_path,
        "INSERT INTO interaction_scores (couple_id, user_id, interaction_type, source) "
        "VALUES (?, ?, ?, ?)",
        (couple_id, user_id, interaction_type, source),
    )


async def get_interaction_ratio(
    db_path: str, couple_id: str, days: int = 7
) -> tuple[int, int]:
    """Return (positive_count, negative_count) for the last *days* days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    pos = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM interaction_scores "
        "WHERE couple_id = ? AND interaction_type = 'positive' AND created_at >= ?",
        (couple_id, cutoff),
    )
    neg = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM interaction_scores "
        "WHERE couple_id = ? AND interaction_type = 'negative' AND created_at >= ?",
        (couple_id, cutoff),
    )
    return (pos["cnt"] if pos else 0, neg["cnt"] if neg else 0)
