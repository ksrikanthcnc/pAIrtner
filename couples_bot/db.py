"""SQLite database layer — schema, migrations, and async CRUD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    name        TEXT,
    partner_id  INTEGER REFERENCES users(tg_id),
    lang        TEXT DEFAULT 'en',
    paused      INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(tg_id),
    text        TEXT NOT NULL,
    visibility  TEXT NOT NULL CHECK(visibility IN ('private', 'shared')),
    source      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id     INTEGER NOT NULL REFERENCES users(tg_id),
    receiver_id   INTEGER NOT NULL REFERENCES users(tg_id),
    draft         TEXT NOT NULL,
    edited_draft  TEXT,
    status        TEXT NOT NULL DEFAULT 'awaiting_approval'
                  CHECK(status IN ('awaiting_approval','approved','sent','discarded')),
    send_after    TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage (
    date   TEXT NOT NULL,
    model  TEXT NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, model)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_facts_owner_visibility
    ON facts(owner_id, visibility);
CREATE INDEX IF NOT EXISTS idx_facts_owner_created
    ON facts(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_user_created
    ON messages(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_pending_sender_status
    ON pending(sender_id, status);
CREATE INDEX IF NOT EXISTS idx_pending_status_send_after
    ON pending(status, send_after);
"""


async def init_db(db_path: str) -> None:
    """Create tables and indexes if they don't exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA_SQL)

        # Migration: add 'paused' column to users if missing (existing DBs)
        async with db.execute("PRAGMA table_info(users)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "paused" not in columns:
            await db.execute(
                "ALTER TABLE users ADD COLUMN paused INTEGER DEFAULT 0"
            )

        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return dict(row)


async def _fetch_one(
    db_path: str, sql: str, params: tuple = ()
) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None


async def _fetch_all(
    db_path: str, sql: str, params: tuple = ()
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]


async def _execute(
    db_path: str, sql: str, params: tuple = ()
) -> int:
    """Execute a write statement; return lastrowid."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def get_user(db_path: str, tg_id: int) -> dict[str, Any] | None:
    return await _fetch_one(
        db_path, "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
    )


async def create_user(
    db_path: str, tg_id: int, name: str | None = None, lang: str = "en"
) -> None:
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO users (tg_id, name, lang) VALUES (?, ?, ?)",
        (tg_id, name, lang),
    )


async def ensure_user(
    db_path: str, tg_id: int, name: str | None = None
) -> dict[str, Any]:
    """Get or create a user, returning the user row."""
    user = await get_user(db_path, tg_id)
    if user is None:
        await create_user(db_path, tg_id, name)
        user = await get_user(db_path, tg_id)
    return user  # type: ignore[return-value]


async def update_user_lang(db_path: str, user_id: int, lang: str) -> None:
    """Update a user's language preference."""
    await _execute(
        db_path,
        "UPDATE users SET lang = ? WHERE tg_id = ?",
        (lang, user_id),
    )


async def set_partner(
    db_path: str, user_id: int, partner_id: int
) -> None:
    """Pair two users (sets partner_id on both)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET partner_id = ? WHERE tg_id = ?",
            (partner_id, user_id),
        )
        await db.execute(
            "UPDATE users SET partner_id = ? WHERE tg_id = ?",
            (user_id, partner_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

async def add_fact(
    db_path: str,
    owner_id: int,
    text: str,
    visibility: str = "private",
    source: str | None = None,
) -> int:
    """Insert a fact; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO facts (owner_id, text, visibility, source) VALUES (?, ?, ?, ?)",
        (owner_id, text, visibility, source),
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


# ---------------------------------------------------------------------------
# Pending (relay drafts)
# ---------------------------------------------------------------------------

async def create_pending(
    db_path: str,
    sender_id: int,
    receiver_id: int,
    draft: str,
) -> int:
    """Create a pending relay; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO pending (sender_id, receiver_id, draft) VALUES (?, ?, ?)",
        (sender_id, receiver_id, draft),
    )


async def get_pending(db_path: str, pending_id: int) -> dict[str, Any] | None:
    return await _fetch_one(
        db_path, "SELECT * FROM pending WHERE id = ?", (pending_id,)
    )


async def update_pending_status(
    db_path: str,
    pending_id: int,
    status: str,
    send_after: str | None = None,
) -> None:
    if send_after:
        await _execute(
            db_path,
            "UPDATE pending SET status = ?, send_after = ? WHERE id = ?",
            (status, send_after, pending_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE pending SET status = ? WHERE id = ?",
            (status, pending_id),
        )


async def update_pending_edited_draft(
    db_path: str, pending_id: int, edited_draft: str
) -> None:
    await _execute(
        db_path,
        "UPDATE pending SET edited_draft = ? WHERE id = ?",
        (edited_draft, pending_id),
    )


async def get_deliverable(db_path: str) -> list[dict[str, Any]]:
    """Get approved pending messages whose cooldown has elapsed."""
    now = _now_iso()
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending WHERE status = 'approved' AND send_after <= ?",
        (now,),
    )


async def mark_as_sent(db_path: str, pending_id: int) -> None:
    now = _now_iso()
    await _execute(
        db_path,
        "UPDATE pending SET status = 'sent', sent_at = ? WHERE id = ?",
        (now, pending_id),
    )


# ---------------------------------------------------------------------------
# Messages (conversation history)
# ---------------------------------------------------------------------------

async def log_message(
    db_path: str, user_id: int, role: str, content: str
) -> int:
    return await _execute(
        db_path,
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )


async def get_recent_messages(
    db_path: str, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    return await _fetch_all(
        db_path,
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

async def increment_usage(db_path: str, model: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO usage (date, model, count) VALUES (?, ?, 1)
               ON CONFLICT(date, model) DO UPDATE SET count = count + 1""",
            (today, model),
        )
        await db.commit()


async def get_usage_count(db_path: str, model: str) -> int:
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


# ---------------------------------------------------------------------------
# Pause / status helpers
# ---------------------------------------------------------------------------

async def set_user_paused(
    db_path: str, user_id: int, paused: bool
) -> None:
    """Set or clear the paused flag on a user."""
    await _execute(
        db_path,
        "UPDATE users SET paused = ? WHERE tg_id = ?",
        (1 if paused else 0, user_id),
    )


async def get_pending_count(db_path: str, user_id: int) -> int:
    """Count pending rows awaiting approval or delivery for a given sender."""
    row = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM pending "
        "WHERE sender_id = ? AND status IN ('awaiting_approval', 'approved')",
        (user_id,),
    )
    return row["cnt"] if row else 0


async def get_partner_last_active(
    db_path: str, partner_id: int
) -> str | None:
    """Return the most recent message timestamp for *partner_id*, or None."""
    row = await _fetch_one(
        db_path,
        "SELECT MAX(created_at) AS last_active FROM messages WHERE user_id = ?",
        (partner_id,),
    )
    return row["last_active"] if row and row["last_active"] else None


# ---------------------------------------------------------------------------
# Context builder (used by prompts module)
# ---------------------------------------------------------------------------

async def get_user_context_data(
    db_path: str, user_id: int
) -> dict[str, Any]:
    """Gather all context data for prompt building.

    Returns a dict with keys: user_name, partner_name, private_facts,
    shared_facts, recent_messages, lang.
    """
    user = await get_user(db_path, user_id)
    if not user:
        return {
            "user_name": "",
            "partner_name": None,
            "private_facts": [],
            "shared_facts": [],
            "recent_messages": [],
            "lang": "en",
        }

    private_facts = [
        f["text"] for f in await get_facts(db_path, user_id, "private")
    ]

    partner_id = user.get("partner_id")
    partner_name = None
    if partner_id:
        shared_facts = [
            f["text"]
            for f in await get_shared_facts_for_pair(db_path, user_id, partner_id)
        ]
        partner = await get_user(db_path, partner_id)
        partner_name = partner["name"] if partner else None
    else:
        shared_facts = [
            f["text"] for f in await get_facts(db_path, user_id, "shared")
        ]

    recent = await get_recent_messages(db_path, user_id)
    # Reverse so oldest is first
    recent_messages = [(m["role"], m["content"]) for m in reversed(recent)]

    return {
        "user_name": user.get("name") or "",
        "partner_name": partner_name,
        "private_facts": private_facts,
        "shared_facts": shared_facts,
        "recent_messages": recent_messages,
        "lang": user.get("lang") or "en",
    }
