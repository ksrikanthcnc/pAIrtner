"""SQLite database layer — schema, migrations, and async CRUD.

NOTE: This module is being gradually migrated to couples_bot.storage/.
New code should prefer importing from the storage submodules.
The schema and init_db now live in storage.schema; this module re-exports them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import json

import aiosqlite

# Schema and init_db are now canonical in storage.schema.
# Re-export here for backward compatibility.
from couples_bot.storage.schema import SCHEMA_SQL as _SCHEMA_SQL, init_db  # noqa: F401
from couples_bot.storage.helpers import (  # noqa: F401
    _now_iso, _row_to_dict, _fetch_one, _fetch_all, _execute,
)

# Schema SQL is in storage.schema (re-exported as _SCHEMA_SQL above)


# ---------------------------------------------------------------------------
# Internal helpers are in storage.helpers (re-exported above)
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
    """Return the user row for *tg_id*, or ``None`` if not found."""
    return await _fetch_one(
        db_path, "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
    )


async def create_user(
    db_path: str, tg_id: int, name: str | None = None, lang: str = "en"
) -> None:
    """Insert a new user row, ignoring if one already exists."""
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
    category: str | None = None,
    fact_type: str = "stable",
) -> int:
    """Insert a fact; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO facts (owner_id, text, visibility, source, category, fact_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (owner_id, text, visibility, source, category, fact_type),
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
        # Get next version number
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
    """Return a pending relay row by ID, or ``None``."""
    return await _fetch_one(
        db_path, "SELECT * FROM pending WHERE id = ?", (pending_id,)
    )


async def update_pending_status(
    db_path: str,
    pending_id: int,
    status: str,
    send_after: str | None = None,
) -> None:
    """Transition a pending row to *status*, optionally setting *send_after*."""
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
    """Store the user-edited draft text on a pending row."""
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
    """Mark a pending row as sent and record the delivery timestamp."""
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
    """Append a message to the conversation log and return its ID."""
    return await _execute(
        db_path,
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )


async def get_recent_messages(
    db_path: str, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the last *limit* messages for a user (most recent first)."""
    return await _fetch_all(
        db_path,
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


async def get_full_history(
    db_path: str, user_id: int, limit: int = 500, offset: int = 0,
) -> list[dict[str, Any]]:
    """Return the full message history for a user (oldest first), with timestamps.

    Used by the PWA to reconstruct the full conversation on auth.
    """
    return await _fetch_all(
        db_path,
        "SELECT id, role, content, created_at FROM messages "
        "WHERE user_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


async def get_history_after_id(
    db_path: str, user_id: int, after_id: int, limit: int = 500,
) -> list[dict[str, Any]]:
    """Return messages after a given ID (for delta fetching by the PWA)."""
    return await _fetch_all(
        db_path,
        "SELECT id, role, content, created_at FROM messages "
        "WHERE user_id = ? AND id > ? ORDER BY created_at ASC LIMIT ?",
        (user_id, after_id, limit),
    )


# ---------------------------------------------------------------------------
# Usage tracking
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


# ---------------------------------------------------------------------------
# Context builder (used by prompts module)
# ---------------------------------------------------------------------------

async def get_user_context_data(
    db_path: str, user_id: int
) -> dict[str, Any]:
    """Gather all context data for prompt building.

    Returns a dict with keys: user_name, partner_name, private_facts,
    shared_facts, recent_messages, lang.

    Facts are formatted with IDs, kind labels, and status when non-active.
    Core facts are listed first (always included), then working (ranked by
    status: active > improving > resolved > outdated), then episodes if any.
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

    all_private = await get_active_facts(db_path, user_id, "private")

    # Sort by kind priority, then status priority within kind
    _KIND_ORDER = {"core": 0, "episode": 1, "working": 2, "partner_view": 3}
    _STATUS_ORDER = {"active": 0, "improving": 1, "resolved": 2, "outdated": 3}

    def _sort_key(f: dict) -> tuple[int, int, str]:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"
        created = f.get("created_at") or ""
        return (_KIND_ORDER.get(kind, 9), _STATUS_ORDER.get(status, 9), created)

    all_private.sort(key=_sort_key)

    private_facts: list[str] = []
    kind_words: dict[str, int] = {}  # track words used per kind

    # Budget caps (words per kind) -- loaded from pack or defaults
    budgets = _get_context_budgets()

    for f in all_private:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"
        prefix = f"[{f['id']}]"
        if kind == "core":
            prefix += " (core)"
        elif kind == "episode":
            prefix += " (episode)"
        suffix = ""
        if status != "active":
            suffix = f" [{status}]"
        line = f"{prefix} {f['text']}{suffix}"

        # Check budget cap
        line_words = len(line.split())
        budget_key = kind if kind in budgets else "working"
        current = kind_words.get(budget_key, 0)
        cap = budgets.get(budget_key, 400)
        if current + line_words > cap and current > 0:
            continue  # Over budget, skip (but allow at least one item)

        private_facts.append(line)
        kind_words[budget_key] = current + line_words

    partner_id = user.get("partner_id")
    partner_name = None
    if partner_id:
        shared_facts = [
            f"[{f['id']}] {f['text']}"
            for f in await get_active_facts(db_path, user_id, "shared")
        ]
        partner_shared = [
            f["text"]  # no IDs for partner's shared items (privacy)
            for f in await get_active_facts(db_path, partner_id, "shared")
        ]
        # Merge without duplicates while preserving order
        seen = set(shared_facts)
        for t_ in partner_shared:
            if t_ not in seen:
                shared_facts.append(t_)
                seen.add(t_)

        # Apply shared budget
        shared_cap = budgets.get("shared", 150)
        shared_facts = _apply_word_cap(shared_facts, shared_cap)
        partner = await get_user(db_path, partner_id)
        partner_name = partner["name"] if partner else None
    else:
        shared_facts = [
            f"[{f['id']}] {f['text']}" for f in await get_active_facts(db_path, user_id, "shared")
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


def _get_context_budgets() -> dict[str, int]:
    """Load context budgets from the active pack, or use defaults."""
    try:
        from couples_bot import pack as pack_mod
        p = pack_mod.current()
        manifest = p._state.manifest or {}
        budgets_raw = manifest.get("budgets") or {}
        return {k: int(v) for k, v in budgets_raw.items() if isinstance(v, (int, float))}
    except Exception:
        return {"core": 250, "shared": 150, "partner_view": 150, "working": 400, "episodes": 300}


def _apply_word_cap(items: list[str], cap: int) -> list[str]:
    """Keep items until the cumulative word count exceeds *cap*."""
    result: list[str] = []
    total = 0
    for item in items:
        words = len(item.split())
        if total + words > cap and total > 0:
            break
        result.append(item)
        total += words
    return result


# ---------------------------------------------------------------------------
# Pending batches (cooldown accumulation)
# ---------------------------------------------------------------------------

async def get_active_batch_for_sender(
    db_path: str, sender_id: int, receiver_id: int
) -> dict[str, Any] | None:
    """Get the currently accumulating batch for a sender→receiver pair."""
    return await _fetch_one(
        db_path,
        "SELECT * FROM pending_batch "
        "WHERE sender_id = ? AND receiver_id = ? AND status = 'accumulating'",
        (sender_id, receiver_id),
    )


async def create_or_get_active_batch(
    db_path: str, sender_id: int, receiver_id: int, send_after: str
) -> dict[str, Any]:
    """Return the active batch for this pair, creating one if none exists."""
    batch = await get_active_batch_for_sender(db_path, sender_id, receiver_id)
    if batch:
        return batch
    batch_id = await _execute(
        db_path,
        "INSERT INTO pending_batch (sender_id, receiver_id, send_after) "
        "VALUES (?, ?, ?)",
        (sender_id, receiver_id, send_after),
    )
    return await _fetch_one(  # type: ignore[return-value]
        db_path, "SELECT * FROM pending_batch WHERE id = ?", (batch_id,)
    )


async def add_item_to_batch(
    db_path: str, batch_id: int, draft: str
) -> int:
    """Add an item to a batch; return the item id."""
    return await _execute(
        db_path,
        "INSERT INTO pending_batch_items (batch_id, draft) VALUES (?, ?)",
        (batch_id, draft),
    )


async def get_batch(db_path: str, batch_id: int) -> dict[str, Any] | None:
    return await _fetch_one(
        db_path, "SELECT * FROM pending_batch WHERE id = ?", (batch_id,)
    )


async def get_batch_item(db_path: str, item_id: int) -> dict[str, Any] | None:
    return await _fetch_one(
        db_path, "SELECT * FROM pending_batch_items WHERE id = ?", (item_id,)
    )


async def get_batch_items(
    db_path: str, batch_id: int
) -> list[dict[str, Any]]:
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending_batch_items WHERE batch_id = ? ORDER BY created_at",
        (batch_id,),
    )


async def update_batch_status(
    db_path: str, batch_id: int, status: str
) -> None:
    await _execute(
        db_path,
        "UPDATE pending_batch SET status = ? WHERE id = ?",
        (status, batch_id),
    )


async def update_batch_item_status(
    db_path: str,
    item_id: int,
    status: str,
    edited_draft: str | None = None,
) -> None:
    if edited_draft is not None:
        await _execute(
            db_path,
            "UPDATE pending_batch_items SET status = ?, edited_draft = ? WHERE id = ?",
            (status, edited_draft, item_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE pending_batch_items SET status = ? WHERE id = ?",
            (status, item_id),
        )


async def reset_batch_timer(
    db_path: str, batch_id: int, send_after: str
) -> None:
    """Reset the batch's send_after (cooldown timer)."""
    await _execute(
        db_path,
        "UPDATE pending_batch SET send_after = ? WHERE id = ?",
        (send_after, batch_id),
    )


async def get_deliverable_batches(db_path: str) -> list[dict[str, Any]]:
    """Get batches ready for delivery or review.

    Returns accumulating batches whose timer has elapsed, plus approved
    batches that are ready to send.
    """
    now = _now_iso()
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending_batch WHERE "
        "(status = 'accumulating' AND send_after <= ?) OR status = 'approved'",
        (now,),
    )


# ---------------------------------------------------------------------------
# Sent relays (history)
# ---------------------------------------------------------------------------

async def get_sent_relays(
    db_path: str, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Fetch sent pending rows where the user is sender or receiver.

    Ordered by ``sent_at`` DESC with LIMIT.
    """
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending "
        "WHERE (sender_id = ? OR receiver_id = ?) AND status = 'sent' "
        "ORDER BY sent_at DESC LIMIT ?",
        (user_id, user_id, limit),
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
        # Window has expired — user has a fresh budget.
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
    reset_at = (now + timedelta(seconds=window_seconds)).isoformat()

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
        # Window expired — start fresh.
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
# Couples (cross-pair queries)
# ---------------------------------------------------------------------------

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


async def get_sent_relays_since(
    db_path: str, user_id: int, since: str
) -> list[dict[str, Any]]:
    """Get relays sent by or to *user_id* since a timestamp."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending "
        "WHERE (sender_id = ? OR receiver_id = ?) "
        "AND status = 'sent' AND sent_at >= ? "
        "ORDER BY sent_at",
        (user_id, user_id, since),
    )


# ---------------------------------------------------------------------------
# Checkins (weekly check-in state tracking)
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
# Active facts (non-archived)
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
    soon = (now + timedelta(hours=24)).isoformat()
    return await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id = ? AND archived_at IS NULL "
        "AND expires_at IS NOT NULL AND expires_at <= ? AND expires_at > ?",
        (owner_id, soon, now.isoformat()),
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
    transient_cutoff = (now - timedelta(days=14)).isoformat()
    situational_cutoff = (now - timedelta(days=30)).isoformat()
    now_iso = now.isoformat()

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


async def get_fact_by_id(
    db_path: str, fact_id: int
) -> dict[str, Any] | None:
    """Return a single fact by its id."""
    return await _fetch_one(
        db_path, "SELECT * FROM facts WHERE id = ?", (fact_id,)
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


async def get_facts_since(
    db_path: str, user_id: int, since_date: str
) -> list[dict[str, Any]]:
    """Get non-archived facts created after *since_date*.

    Includes the user's own facts plus partner's shared facts.
    """
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


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

async def add_user_goal(db_path: str, user_id: int, text: str) -> int:
    """Create a personal goal; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO user_goals (user_id, text) VALUES (?, ?)",
        (user_id, text),
    )


async def add_shared_goal(
    db_path: str, couple_id: str, created_by: int, text: str
) -> int:
    """Create a shared goal; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO shared_goals (couple_id, text, created_by) VALUES (?, ?, ?)",
        (couple_id, text, created_by),
    )


async def get_active_goals(
    db_path: str, user_id: int
) -> list[dict[str, Any]]:
    """Get active personal goals for a user."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM user_goals WHERE user_id = ? AND status = 'active' "
        "ORDER BY created_at",
        (user_id,),
    )


async def get_shared_goals(
    db_path: str, couple_id: str
) -> list[dict[str, Any]]:
    """Get active shared goals for a couple."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM shared_goals WHERE couple_id = ? AND status = 'active' "
        "ORDER BY created_at",
        (couple_id,),
    )


async def update_goal_status(
    db_path: str, goal_id: int, status: str, progress: str | None = None
) -> None:
    """Update a personal goal's status and optional progress."""
    if progress is not None:
        await _execute(
            db_path,
            "UPDATE user_goals SET status = ?, progress = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, progress, goal_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE user_goals SET status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, goal_id),
        )


async def update_shared_goal_status(
    db_path: str, goal_id: int, status: str, progress: str | None = None
) -> None:
    """Update a shared goal's status and optional progress."""
    if progress is not None:
        await _execute(
            db_path,
            "UPDATE shared_goals SET status = ?, progress = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, progress, goal_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE shared_goals SET status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, goal_id),
        )


async def get_goal_by_id(db_path: str, goal_id: int) -> dict[str, Any] | None:
    """Return a personal goal by its id."""
    return await _fetch_one(
        db_path, "SELECT * FROM user_goals WHERE id = ?", (goal_id,)
    )


async def get_shared_goal_by_id(db_path: str, goal_id: int) -> dict[str, Any] | None:
    """Return a shared goal by its id."""
    return await _fetch_one(
        db_path, "SELECT * FROM shared_goals WHERE id = ?", (goal_id,)
    )


# ---------------------------------------------------------------------------
# Conflicts (FEAT-002)
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
    import json

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


async def get_recent_pending_count(
    db_path: str, sender_id: int, since: str
) -> int:
    """Count pending rows created since a timestamp for a sender."""
    row = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM pending "
        "WHERE sender_id = ? AND created_at >= ?",
        (sender_id, since),
    )
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Feedback on relays (FEAT-003 batch4)
# ---------------------------------------------------------------------------

async def update_pending_feedback(
    db_path: str, pending_id: int, feedback: str
) -> None:
    """Record the sender's feedback on a delivered relay."""
    if feedback not in ("yes", "not_sure", "no"):
        feedback = "not_sure"
    await _execute(
        db_path,
        "UPDATE pending SET feedback = ? WHERE id = ?",
        (feedback, pending_id),
    )


async def mark_pending_acknowledged(
    db_path: str, pending_id: int
) -> None:
    """Set acknowledged_at to now on a delivered relay."""
    now = _now_iso()
    await _execute(
        db_path,
        "UPDATE pending SET acknowledged_at = ? WHERE id = ?",
        (now, pending_id),
    )


async def get_pending_needing_feedback(
    db_path: str, sender_id: int
) -> list[dict[str, Any]]:
    """Return sent relays FROM this sender where feedback is NULL and within 24h."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending "
        "WHERE sender_id = ? AND status = 'sent' "
        "AND feedback IS NULL AND sent_at >= ? "
        "ORDER BY sent_at DESC",
        (sender_id, cutoff),
    )


async def get_feedback_stats(
    db_path: str, user_id: int
) -> dict[str, int]:
    """Return counts of yes/not_sure/no feedback for a sender."""
    rows = await _fetch_all(
        db_path,
        "SELECT feedback, COUNT(*) AS cnt FROM pending "
        "WHERE sender_id = ? AND feedback IS NOT NULL "
        "GROUP BY feedback",
        (user_id,),
    )
    return {row["feedback"]: row["cnt"] for row in rows}


async def get_unacknowledged_relays_to_user(
    db_path: str, receiver_id: int
) -> list[dict[str, Any]]:
    """Return sent relays TO this user that are not yet acknowledged."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM pending "
        "WHERE receiver_id = ? AND status = 'sent' "
        "AND acknowledged_at IS NULL "
        "ORDER BY sent_at DESC",
        (receiver_id,),
    )


# ---------------------------------------------------------------------------
# Timeline events (FEAT-004 batch4)
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


# ---------------------------------------------------------------------------
# Auth tokens (adapter authentication)
# ---------------------------------------------------------------------------

async def create_auth_token(
    db_path: str, user_id: int, token: str
) -> None:
    """Store an authentication token for a user (upsert)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO auth_tokens (user_id, token) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "token = excluded.token, created_at = CURRENT_TIMESTAMP",
            (user_id, token),
        )
        await db.commit()


async def get_auth_token(
    db_path: str, user_id: int
) -> str | None:
    """Return the stored token for *user_id*, or ``None``."""
    row = await _fetch_one(
        db_path,
        "SELECT token FROM auth_tokens WHERE user_id = ?",
        (user_id,),
    )
    return row["token"] if row else None


async def validate_auth_token(
    db_path: str, user_id: int, token: str
) -> bool:
    """Check if a token matches the stored token for *user_id*.

    Uses constant-time comparison to prevent timing attacks.
    """
    import secrets as _secrets

    stored = await get_auth_token(db_path, user_id)
    if stored is None:
        return False
    return _secrets.compare_digest(stored, token)


# ---------------------------------------------------------------------------
# REST adapter: durable outbox and user-id allocation
# ---------------------------------------------------------------------------

async def add_outbox_event(
    db_path: str, user_id: int, kind: str, payload: str
) -> int:
    """Append an event for *user_id*; return its id (the poll cursor)."""
    return await _execute(
        db_path,
        "INSERT INTO rest_outbox (user_id, kind, payload) VALUES (?, ?, ?)",
        (user_id, kind, payload),
    )


async def get_outbox_events(
    db_path: str, user_id: int, after: int = 0, limit: int = 100
) -> list[dict[str, Any]]:
    """Events for *user_id* with id > *after*, oldest first."""
    return await _fetch_all(
        db_path,
        "SELECT id, kind, payload, created_at FROM rest_outbox "
        "WHERE user_id = ? AND id > ? ORDER BY id LIMIT ?",
        (user_id, after, limit),
    )


async def prune_outbox(db_path: str, older_than_days: int) -> int:
    """Delete outbox events older than *older_than_days*; return rows removed."""
    modifier = f"-{int(older_than_days)} days"
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM rest_outbox WHERE created_at < datetime('now', ?)",
            (modifier,),
        )
        await db.commit()
        return cur.rowcount


async def allocate_rest_user(
    db_path: str, name: str, lang: str, base: int
) -> int:
    """Create a user with the next free id >= *base* and return that id.

    The SELECT MAX and the INSERT run on one connection inside an immediate
    transaction, so concurrent callers cannot receive the same id.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT MAX(tg_id) FROM users WHERE tg_id >= ?", (base,)
            ) as cur:
                row = await cur.fetchone()
            new_id = base if row is None or row[0] is None else row[0] + 1
            await db.execute(
                "INSERT INTO users (tg_id, name, lang) VALUES (?, ?, ?)",
                (new_id, name, lang),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return new_id



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



# ---------------------------------------------------------------------------
# Runtime settings (DB-backed, overrides static config)
# ---------------------------------------------------------------------------

async def get_runtime_setting(
    db_path: str, key: str, default: str | None = None,
) -> str | None:
    """Get a runtime setting from user_meta (user_id=0 for global settings)."""
    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = 0 AND key = ?",
        (key,),
    )
    return row["value"] if row else default


async def set_runtime_setting(
    db_path: str, key: str, value: str,
) -> None:
    """Set a global runtime setting (user_id=0)."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (0, ?, ?, CURRENT_TIMESTAMP)",
            (key, value),
        )
        await db.commit()


async def get_all_runtime_settings(db_path: str) -> dict[str, str]:
    """Get all global runtime settings."""
    rows = await _fetch_all(
        db_path,
        "SELECT key, value FROM user_meta WHERE user_id = 0",
    )
    return {r["key"]: r["value"] for r in rows}


async def get_user_preference(
    db_path: str, user_id: int, key: str, default: str | None = None,
) -> str | None:
    """Get a per-user preference from user_meta."""
    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = ? AND key = ?",
        (user_id, key),
    )
    return row["value"] if row else default


async def set_user_preference(
    db_path: str, user_id: int, key: str, value: str,
) -> None:
    """Set a per-user preference."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, key, value),
        )
        await db.commit()
