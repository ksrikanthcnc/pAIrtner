"""FTS5 full-text search on facts — indexing, querying, and maintenance."""

from __future__ import annotations

import re
from typing import Any

import aiosqlite

from couples_bot.db import _fetch_all, _fetch_one, get_user


# ---------------------------------------------------------------------------
# FTS query sanitization
# ---------------------------------------------------------------------------

def _sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH syntax.

    Strips special characters and joins terms with OR for broader matching.
    Returns an empty string when the input has no usable tokens.
    """
    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    terms = cleaned.split()
    if not terms:
        return ""
    if len(terms) > 1:
        return " OR ".join(terms)
    return terms[0]


# ---------------------------------------------------------------------------
# FTS5 initialisation
# ---------------------------------------------------------------------------

async def init_fts(db_path: str) -> None:
    """Create FTS5 virtual table and sync triggers.

    Called at the end of :func:`init_db` to ensure the FTS infrastructure
    exists.  On first run the index is rebuilt to capture pre-existing facts.
    """
    async with aiosqlite.connect(db_path) as db:
        # Check whether the FTS table already exists (for rebuild decision)
        async with db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='facts_fts'"
        ) as cur:
            existing = await cur.fetchone()

        # External-content FTS5 table backed by `facts`
        await db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
            "USING fts5(text, content=facts, content_rowid=id)"
        )

        # Keep the FTS index in sync via triggers
        await db.execute(
            "CREATE TRIGGER IF NOT EXISTS facts_fts_ai "
            "AFTER INSERT ON facts BEGIN "
            "  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); "
            "END"
        )

        await db.execute(
            "CREATE TRIGGER IF NOT EXISTS facts_fts_ad "
            "AFTER DELETE ON facts BEGIN "
            "  INSERT INTO facts_fts(facts_fts, rowid, text) "
            "    VALUES('delete', old.id, old.text); "
            "END"
        )

        await db.execute(
            "CREATE TRIGGER IF NOT EXISTS facts_fts_au "
            "AFTER UPDATE OF text ON facts BEGIN "
            "  INSERT INTO facts_fts(facts_fts, rowid, text) "
            "    VALUES('delete', old.id, old.text); "
            "  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); "
            "END"
        )

        # First-time creation → index any facts that were inserted before FTS
        if not existing:
            await db.execute(
                "INSERT INTO facts_fts(facts_fts) VALUES('rebuild')"
            )

        await db.commit()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_facts(
    db_path: str,
    user_id: int,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search facts via FTS5 with BM25 ranking.

    Returns the user's own facts **plus** partner's *shared* facts that
    match the query.  Partner's private facts are **never** returned.
    """
    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []

    user = await get_user(db_path, user_id)
    partner_id = (user or {}).get("partner_id")

    try:
        if partner_id:
            return await _fetch_all(
                db_path,
                "SELECT facts.* FROM facts_fts "
                "JOIN facts ON facts.id = facts_fts.rowid "
                "WHERE facts_fts MATCH ? "
                "AND facts.archived_at IS NULL "
                "AND (facts.owner_id = ? "
                "     OR (facts.owner_id = ? AND facts.visibility = 'shared')) "
                "ORDER BY bm25(facts_fts) LIMIT ?",
                (sanitized, user_id, partner_id, limit),
            )

        return await _fetch_all(
            db_path,
            "SELECT facts.* FROM facts_fts "
            "JOIN facts ON facts.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? "
            "AND facts.owner_id = ? "
            "AND facts.archived_at IS NULL "
            "ORDER BY bm25(facts_fts) LIMIT ?",
            (sanitized, user_id, limit),
        )
    except Exception:
        # Malformed FTS query or other DB error — return empty rather than crash
        return []


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

async def rebuild_fts_index(db_path: str) -> None:
    """Drop and recreate FTS content from the source table."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_fact_count(db_path: str, user_id: int) -> int:
    """Count non-archived facts for *user_id*."""
    row = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM facts "
        "WHERE owner_id = ? AND archived_at IS NULL",
        (user_id,),
    )
    return row["cnt"] if row else 0
