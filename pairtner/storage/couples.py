"""Couple config, conflicts, timeline, check-ins.

Canonical implementations of couple-related database operations.
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
# Couple config (multi-tenant settings)
# ---------------------------------------------------------------------------

def make_couple_id(user_a: int, user_b: int) -> str:
    """Canonical couple identifier — ensures both partners share one config."""
    return f"{min(user_a, user_b)}:{max(user_a, user_b)}"


async def set_couple_config(
    db_path: str, couple_id: str, key: str, value: str
) -> None:
    """Insert or update a couple config entry."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO couple_config (couple_id, config_key, config_value, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(couple_id, config_key) DO UPDATE
               SET config_value = excluded.config_value,
                   updated_at = CURRENT_TIMESTAMP""",
            (couple_id, key, value),
        )
        await db.commit()


async def get_couple_config(
    db_path: str, couple_id: str, key: str
) -> str | None:
    """Return a single config value for a couple, or None."""
    row = await _fetch_one(
        db_path,
        "SELECT config_value FROM couple_config WHERE couple_id = ? AND config_key = ?",
        (couple_id, key),
    )
    return row["config_value"] if row else None


async def delete_couple_config(
    db_path: str, couple_id: str, key: str
) -> bool:
    """Delete a couple config entry. Returns True if something was deleted."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM couple_config WHERE couple_id = ? AND config_key = ?",
            (couple_id, key),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_all_couples(db_path: str) -> list[tuple[int, int]]:
    """Return all paired user tuples, deduplicated in canonical order."""
    rows = await _fetch_all(
        db_path,
        "SELECT tg_id, partner_id FROM users WHERE partner_id IS NOT NULL",
    )
    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []
    for row in rows:
        pair = (min(row["tg_id"], row["partner_id"]),
                max(row["tg_id"], row["partner_id"]))
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

async def create_conflict(
    db_path: str, couple_id: str, theme: str
) -> int:
    """Create a new open conflict; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO conflicts (couple_id, theme) VALUES (?, ?)",
        (couple_id, theme),
    )


async def get_open_conflicts(
    db_path: str, couple_id: str
) -> list[dict[str, Any]]:
    """Return all open conflicts for a couple."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM conflicts WHERE couple_id = ? AND status = 'open' "
        "ORDER BY opened_at DESC",
        (couple_id,),
    )


async def get_conflict_by_id(
    db_path: str, conflict_id: int
) -> dict[str, Any] | None:
    """Return a single conflict row, or ``None``."""
    return await _fetch_one(
        db_path, "SELECT * FROM conflicts WHERE id = ?", (conflict_id,)
    )


async def update_conflict_status(
    db_path: str,
    conflict_id: int,
    status: str,
    resolved_at: str | None = None,
) -> None:
    """Transition a conflict to a new status."""
    if resolved_at:
        await _execute(
            db_path,
            "UPDATE conflicts SET status = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_at, conflict_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE conflicts SET status = ? WHERE id = ?",
            (status, conflict_id),
        )


async def add_relay_to_conflict(
    db_path: str, conflict_id: int, relay_id: int
) -> None:
    """Append a relay id to the conflict's relay_ids JSON array."""
    conflict = await get_conflict_by_id(db_path, conflict_id)
    if not conflict:
        return
    existing = conflict.get("relay_ids")
    try:
        ids = json.loads(existing) if existing else []
    except (json.JSONDecodeError, TypeError):
        ids = []
    if relay_id not in ids:
        ids.append(relay_id)
    await _execute(
        db_path,
        "UPDATE conflicts SET relay_ids = ? WHERE id = ?",
        (json.dumps(ids), conflict_id),
    )


async def get_conflict_summary(
    db_path: str, couple_id: str
) -> dict[str, int]:
    """Return counts by status for a couple's conflicts."""
    rows = await _fetch_all(
        db_path,
        "SELECT status, COUNT(*) AS cnt FROM conflicts "
        "WHERE couple_id = ? GROUP BY status",
        (couple_id,),
    )
    return {row["status"]: row["cnt"] for row in rows}


# ---------------------------------------------------------------------------
# Check-ins
# ---------------------------------------------------------------------------

async def create_checkin(
    db_path: str,
    couple_id: str,
    week_start: str,
    partner_a_id: int,
    partner_b_id: int,
) -> int:
    """Create a new check-in record; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO checkins (couple_id, week_start, partner_a_id, partner_b_id) "
        "VALUES (?, ?, ?, ?)",
        (couple_id, week_start, partner_a_id, partner_b_id),
    )


async def get_active_checkin(
    db_path: str, couple_id: str
) -> dict[str, Any] | None:
    """Return the currently active (non-complete) check-in for a couple."""
    return await _fetch_one(
        db_path,
        "SELECT * FROM checkins "
        "WHERE couple_id = ? AND status IN ('pending', 'partial') "
        "ORDER BY created_at DESC LIMIT 1",
        (couple_id,),
    )


async def update_checkin_answers(
    db_path: str,
    checkin_id: int,
    partner: str,
    answers: str,
) -> None:
    """Store answers for partner 'a' or 'b' and update status."""
    col = "partner_a_answers" if partner == "a" else "partner_b_answers"
    await _execute(
        db_path,
        f"UPDATE checkins SET {col} = ?, status = 'partial' WHERE id = ?",
        (answers, checkin_id),
    )


async def mark_checkin_complete(
    db_path: str,
    checkin_id: int,
    summary: str,
) -> None:
    """Mark a check-in as complete with the generated summary."""
    await _execute(
        db_path,
        "UPDATE checkins SET status = 'complete', summary = ? WHERE id = ?",
        (summary, checkin_id),
    )


# ---------------------------------------------------------------------------
# Timeline events
# ---------------------------------------------------------------------------

async def add_timeline_event(
    db_path: str,
    couple_id: str,
    event_type: str,
    summary: str,
    metadata: str | None = None,
) -> int:
    """Record a timeline event; return its id."""
    valid = ("paired", "conflict", "resolution", "milestone", "goal", "relay", "checkin")
    if event_type not in valid:
        event_type = "milestone"
    return await _execute(
        db_path,
        "INSERT INTO timeline_events (couple_id, event_type, summary, metadata) "
        "VALUES (?, ?, ?, ?)",
        (couple_id, event_type, summary, metadata),
    )


async def get_timeline_events(
    db_path: str,
    couple_id: str,
    limit: int = 20,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent timeline events, optionally filtered by type."""
    if event_type:
        return await _fetch_all(
            db_path,
            "SELECT * FROM timeline_events "
            "WHERE couple_id = ? AND event_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (couple_id, event_type, limit),
        )
    return await _fetch_all(
        db_path,
        "SELECT * FROM timeline_events "
        "WHERE couple_id = ? ORDER BY created_at DESC LIMIT ?",
        (couple_id, limit),
    )


async def get_timeline_events_range(
    db_path: str,
    couple_id: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Return timeline events within a date range."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM timeline_events "
        "WHERE couple_id = ? AND created_at >= ? AND created_at < ? "
        "ORDER BY created_at ASC",
        (couple_id, start_date, end_date),
    )


async def get_timeline_summary(
    db_path: str, couple_id: str
) -> dict[str, int]:
    """Return counts by event_type for a couple's timeline."""
    rows = await _fetch_all(
        db_path,
        "SELECT event_type, COUNT(*) AS cnt FROM timeline_events "
        "WHERE couple_id = ? GROUP BY event_type",
        (couple_id,),
    )
    return {row["event_type"]: row["cnt"] for row in rows}
