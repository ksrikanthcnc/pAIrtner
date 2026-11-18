"""Usage tracking, rate limiting, and analytics.

Canonical implementations of usage/rate-limit database operations.
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
# Usage counters
# ---------------------------------------------------------------------------

async def increment_usage(db_path: str, model: str) -> None:
    """Increment today's usage counter for *model*."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO usage (date, model, count) VALUES (?, ?, 1)
               ON CONFLICT(date, model) DO UPDATE SET count = count + 1""",
            (today, model),
        )
        await db.commit()


async def record_pack_snapshot(
    db_path: str, pack_hash: str, name: str, version: str,
    engine_contract: int | None, files: dict[str, str],
) -> None:
    """Remember which file hashes make up *pack_hash* (once per distinct hash)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO pack_snapshots
               (pack_hash, name, version, engine_contract, files_json)
               VALUES (?, ?, ?, ?, ?)""",
            (pack_hash, name, version, engine_contract,
             json.dumps(files, sort_keys=True)),
        )
        await db.commit()


async def record_llm_call(
    db_path: str, task: str, model: str | None, pack_hash: str, prompt_sha256: str,
) -> None:
    """Trace one LLM call: no prompt text and no user id are stored."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO llm_calls (task, model, pack_hash, prompt_sha256)
               VALUES (?, ?, ?, ?)""",
            (task, model, pack_hash, prompt_sha256),
        )
        await db.commit()


async def get_usage_count(db_path: str, model: str) -> int:
    """Return today's usage count for a specific *model*."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = await _fetch_one(
        db_path,
        "SELECT count FROM usage WHERE date = ? AND model = ?",
        (today, model),
    )
    return row["count"] if row else 0


async def get_usage_today(db_path: str) -> int:
    """Sum of all model counts from the usage table for today's date."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = await _fetch_one(
        db_path,
        "SELECT COALESCE(SUM(count), 0) AS total FROM usage WHERE date = ?",
        (today,),
    )
    return row["total"] if row else 0


async def get_usage_detailed(db_path: str, days: int = 7) -> list[dict]:
    """Return per-model usage for the last *days* days, most recent first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return await _fetch_all(
        db_path,
        "SELECT date, model, count FROM usage WHERE date >= ? ORDER BY date DESC, model",
        (cutoff,),
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

async def check_rate_limit(
    db_path: str,
    user_id: int,
    window: str,
    max_count: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Check if a user is within the rate limit for *window*.

    Returns ``(allowed, remaining)`` where *remaining* is how many more
    actions are permitted in the current window.
    """
    now = datetime.now(timezone.utc)

    row = await _fetch_one(
        db_path,
        "SELECT count, reset_at FROM rate_limit WHERE user_id = ? AND window = ?",
        (user_id, window),
    )

    if row is None:
        return True, max_count

    reset_at = datetime.fromisoformat(row["reset_at"])
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)

    if now >= reset_at:
        return True, max_count

    count = row["count"]
    if count >= max_count:
        return False, 0

    return True, max_count - count


async def increment_rate_counter(
    db_path: str,
    user_id: int,
    window: str,
    window_seconds: int,
) -> None:
    """Increment the rate counter for *user_id* + *window*.

    Creates the row if missing; resets count when the window has expired.
    """
    now = datetime.now(timezone.utc)
    reset_at = _to_iso(now + timedelta(seconds=window_seconds))

    row = await _fetch_one(
        db_path,
        "SELECT count, reset_at FROM rate_limit WHERE user_id = ? AND window = ?",
        (user_id, window),
    )

    if row is None:
        await _execute(
            db_path,
            "INSERT INTO rate_limit (user_id, window, count, reset_at) "
            "VALUES (?, ?, 1, ?)",
            (user_id, window, reset_at),
        )
        return

    existing_reset = datetime.fromisoformat(row["reset_at"])
    if existing_reset.tzinfo is None:
        existing_reset = existing_reset.replace(tzinfo=timezone.utc)

    if now >= existing_reset:
        await _execute(
            db_path,
            "UPDATE rate_limit SET count = 1, reset_at = ? "
            "WHERE user_id = ? AND window = ?",
            (reset_at, user_id, window),
        )
    else:
        await _execute(
            db_path,
            "UPDATE rate_limit SET count = count + 1 "
            "WHERE user_id = ? AND window = ?",
            (user_id, window),
        )


# ---------------------------------------------------------------------------
# Admin: conversation history (both users interleaved)
# ---------------------------------------------------------------------------

async def get_couple_conversation(
    db_path: str, user_a_id: int, user_b_id: int, limit: int = 200,
) -> list[dict[str, Any]]:
    """Get interleaved conversation for a couple (both users + bot responses).

    Returns messages from both users, ordered by created_at, with user names.
    Used by admin dashboard to view the full conversation.
    """
    return await _fetch_all(
        db_path,
        """SELECT m.id, m.user_id, m.role, m.content, m.created_at,
                  u.name as user_name
           FROM messages m
           JOIN users u ON m.user_id = u.tg_id
           WHERE m.user_id IN (?, ?)
           ORDER BY m.created_at ASC
           LIMIT ?""",
        (user_a_id, user_b_id, limit),
    )
