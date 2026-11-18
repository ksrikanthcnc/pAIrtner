"""Fact/memory CRUD — add, get, archive, search, aging, versions.

Canonical implementations of fact-related database operations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from pairtner.storage.helpers import (
    _now_iso, _to_iso, _fetch_one, _fetch_all, _execute,
)


# ---------------------------------------------------------------------------
# Basic fact CRUD
# ---------------------------------------------------------------------------

async def add_fact(
    db_path: str,
    owner_id: int,
    text: str,
    visibility: str = "private",
    source: str | None = None,
    category: str | None = None,
    fact_type: str = "stable",
    source_message_id: int | None = None,
) -> int:
    """Insert a fact; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO facts (owner_id, text, visibility, source, category, fact_type, source_message_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner_id, text, visibility, source, category, fact_type, source_message_id),
    )


async def get_facts(
    db_path: str,
    owner_id: int,
    visibility: str | None = None,
) -> list[dict[str, Any]]:
    """Get facts for a user, optionally filtered by visibility."""
    if visibility:
        return await _fetch_all(
            db_path,
            "SELECT * FROM facts WHERE owner_id = ? AND visibility = ? ORDER BY created_at",
            (owner_id, visibility),
        )
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id = ? ORDER BY created_at",
        (owner_id,),
    )


async def get_shared_facts_for_pair(
    db_path: str, user_id: int, partner_id: int
) -> list[dict[str, Any]]:
    """Get all shared facts from both partners."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id IN (?, ?) AND visibility = 'shared' ORDER BY created_at",
        (user_id, partner_id),
    )


async def set_fact_visibility(
    db_path: str, fact_id: int, owner_id: int, visibility: str
) -> bool:
    """Set an active fact's visibility; only its owner can change it.

    ``shared_at`` is stamped when shared and cleared when made private.
    Returns True if a row was updated.
    """
    if visibility not in ("private", "shared"):
        raise ValueError(f"invalid visibility: {visibility!r}")
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "UPDATE facts SET visibility = ?, "
            "shared_at = CASE WHEN ? = 'shared' THEN CURRENT_TIMESTAMP ELSE NULL END, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND owner_id = ? AND archived_at IS NULL",
            (visibility, visibility, fact_id, owner_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_fact_text(db_path: str, fact_id: int, new_text: str) -> bool:
    """Update a fact's text. Returns True if updated."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "UPDATE facts SET text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_text, fact_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_fact_status(db_path: str, fact_id: int, status: str) -> bool:
    """Set a fact's status. Returns True if updated.

    Valid statuses: active, improving, resolved, outdated.
    """
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "UPDATE facts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, fact_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def promote_to_core(db_path: str, fact_id: int) -> bool:
    """Promote a fact to core kind. Returns True if updated.

    Core facts are never aged or archived.
    """
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "UPDATE facts SET kind = 'core', fact_type = 'stable', "
            "expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (fact_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_fact(db_path: str, fact_id: int, owner_id: int) -> bool:
    """Delete a specific fact owned by the user. Returns True if deleted."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM facts WHERE id = ? AND owner_id = ?",
            (fact_id, owner_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_all_facts(db_path: str, owner_id: int) -> int:
    """Delete all facts owned by the user. Returns count deleted."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM facts WHERE owner_id = ?", (owner_id,)
        )
        await db.commit()
        return cur.rowcount


async def get_fact_by_id(
    db_path: str, fact_id: int
) -> dict[str, Any] | None:
    """Return a single fact by its id."""
    return await _fetch_one(
        db_path, "SELECT * FROM facts WHERE id = ?", (fact_id,)
    )


# ---------------------------------------------------------------------------
# Active (non-archived) facts
# ---------------------------------------------------------------------------

async def get_active_facts(
    db_path: str,
    owner_id: int,
    visibility: str | None = None,
) -> list[dict[str, Any]]:
    """Get non-archived facts for a user, optionally filtered by visibility."""
    if visibility:
        return await _fetch_all(
            db_path,
            "SELECT * FROM facts WHERE owner_id = ? AND visibility = ? "
            "AND archived_at IS NULL ORDER BY created_at",
            (owner_id, visibility),
        )
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id = ? AND archived_at IS NULL "
        "ORDER BY created_at",
        (owner_id,),
    )


async def get_shared_facts_since(
    db_path: str, a: int, b: int, since: str
) -> list[dict[str, Any]]:
    """Active shared facts of either partner shared/created at or after ``since``."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id IN (?, ?) AND visibility = 'shared' "
        "AND archived_at IS NULL AND COALESCE(shared_at, created_at) >= ? "
        "ORDER BY COALESCE(shared_at, created_at)",
        (a, b, since),
    )


async def get_positive_shared_facts(
    db_path: str, user_id: int, partner_id: int
) -> list[dict[str, Any]]:
    """Get shared facts with positive categories from both partners."""
    positive_categories = ("relationship", "emotional")
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id IN (?, ?) AND visibility = 'shared' "
        "AND category IN (?, ?) AND archived_at IS NULL "
        "ORDER BY created_at DESC",
        (user_id, partner_id, *positive_categories),
    )


async def get_facts_since(
    db_path: str, user_id: int, since_date: str
) -> list[dict[str, Any]]:
    """Get non-archived facts created after *since_date*.

    Includes the user's own facts plus partner's shared facts.
    """
    from pairtner.storage.users import get_user

    user = await get_user(db_path, user_id)
    partner_id = (user or {}).get("partner_id")

    if partner_id:
        return await _fetch_all(
            db_path,
            "SELECT * FROM facts "
            "WHERE archived_at IS NULL AND created_at >= ? "
            "AND (owner_id = ? OR (owner_id = ? AND visibility = 'shared')) "
            "ORDER BY created_at DESC",
            (since_date, user_id, partner_id),
        )

    return await _fetch_all(
        db_path,
        "SELECT * FROM facts "
        "WHERE owner_id = ? AND archived_at IS NULL AND created_at >= ? "
        "ORDER BY created_at DESC",
        (user_id, since_date),
    )


# ---------------------------------------------------------------------------
# Archiving, expiry, aging
# ---------------------------------------------------------------------------

async def archive_fact(db_path: str, fact_id: int) -> None:
    """Set archived_at to now on a fact."""
    now = _now_iso()
    await _execute(
        db_path,
        "UPDATE facts SET archived_at = ? WHERE id = ?",
        (now, fact_id),
    )


async def set_fact_expiry(
    db_path: str, fact_id: int, expires_at: str
) -> None:
    """Set the expires_at timestamp on a fact."""
    await _execute(
        db_path,
        "UPDATE facts SET expires_at = ? WHERE id = ?",
        (expires_at, fact_id),
    )


async def get_expiring_facts(
    db_path: str, owner_id: int
) -> list[dict[str, Any]]:
    """Get facts where expires_at is within 24 hours from now."""
    now = datetime.now(timezone.utc)
    soon = _to_iso(now + timedelta(hours=24))
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id = ? AND archived_at IS NULL "
        "AND expires_at IS NOT NULL AND expires_at <= ? AND expires_at > ?",
        (owner_id, soon, _to_iso(now)),
    )


async def supersede_fact(
    db_path: str, old_fact_id: int, new_fact_id: int
) -> None:
    """Mark old fact as superseded by new fact and archive it."""
    now = _now_iso()
    await _execute(
        db_path,
        "UPDATE facts SET supersedes_id = ?, archived_at = ? WHERE id = ?",
        (new_fact_id, now, old_fact_id),
    )


async def run_fact_aging(db_path: str) -> list[dict[str, Any]]:
    """Archive expired facts by type.

    - Transient facts older than 14 days → archived automatically.
    - Situational facts older than 30 days → returned for user confirmation.

    Returns the list of situational facts that need confirmation.
    """
    now = datetime.now(timezone.utc)
    transient_cutoff = _to_iso(now - timedelta(days=14))
    situational_cutoff = _to_iso(now - timedelta(days=30))
    now_iso = _to_iso(now)

    # Auto-archive transient facts older than 14 days (skip core)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE facts SET archived_at = ? "
            "WHERE fact_type = 'transient' AND archived_at IS NULL "
            "AND kind != 'core' "
            "AND created_at <= ?",
            (now_iso, transient_cutoff),
        )
        await db.commit()

    # Find situational facts older than 30 days that need confirmation (skip core)
    needs_confirmation = await _fetch_all(
        db_path,
        "SELECT * FROM facts "
        "WHERE fact_type = 'situational' AND archived_at IS NULL "
        "AND kind != 'core' "
        "AND created_at <= ?",
        (situational_cutoff,),
    )

    return needs_confirmation


# ---------------------------------------------------------------------------
# Memory versions and op log
# ---------------------------------------------------------------------------

async def append_memory_version(
    db_path: str,
    fact_id: int,
    text: str,
    status: str = "active",
    op: str = "add",
    reason: str | None = None,
    source_msg_id: int | None = None,
) -> None:
    """Append a version record to the memory version history."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM memory_versions WHERE fact_id = ?",
            (fact_id,),
        )
        row = await cursor.fetchone()
        next_ver = (row[0] if row else 0) + 1

        await db.execute(
            "INSERT INTO memory_versions (fact_id, version, text, status, op, reason, source_msg_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fact_id, next_ver, text, status, op, reason, source_msg_id),
        )
        await db.commit()


async def log_memory_op(
    db_path: str,
    user_id: int,
    op_json: str,
    outcome: str,
    *,
    message_id: int | None = None,
    reason: str | None = None,
    fact_id: int | None = None,
) -> None:
    """Log a memory operation to the audit trail."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO memory_op_log (user_id, message_id, op_json, outcome, reason, fact_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, message_id, op_json, outcome, reason, fact_id),
        )
        await db.commit()


async def get_memory_versions(
    db_path: str, fact_id: int
) -> list[dict[str, Any]]:
    """Get version history for a fact."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM memory_versions WHERE fact_id = ? ORDER BY version",
        (fact_id,),
    )


# ---------------------------------------------------------------------------
# Fact summaries (monthly compressed context)
# ---------------------------------------------------------------------------

async def add_fact_summary(
    db_path: str,
    user_id: int,
    year_month: str,
    summary_text: str,
    fact_count: int,
) -> int:
    """Insert or replace a monthly fact summary; return its id."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO fact_summaries (user_id, year_month, summary_text, fact_count) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, year_month) DO UPDATE SET "
            "  summary_text = excluded.summary_text, "
            "  fact_count = excluded.fact_count, "
            "  created_at = CURRENT_TIMESTAMP",
            (user_id, year_month, summary_text, fact_count),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def get_fact_summaries(
    db_path: str, user_id: int, limit: int = 12
) -> list[dict[str, Any]]:
    """Return the most recent *limit* monthly summaries for a user."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM fact_summaries WHERE user_id = ? "
        "ORDER BY year_month DESC LIMIT ?",
        (user_id, limit),
    )
