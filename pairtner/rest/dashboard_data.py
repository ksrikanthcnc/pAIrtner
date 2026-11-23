"""Read-only queries behind the PWA dashboard endpoints.

Every function takes the authenticated user's id and only ever returns rows
that user owns or is a member of (relationship membership is checked by
``user_relationship``). Admin helpers at the bottom are not user-scoped; the
caller (``RestApi``) must gate them behind the admin token.
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

SUMMARY_KINDS = (
    "daily", "triday", "weekly", "biweekly", "monthly", "bimonthly",
    "quarterly", "semiannual", "yearly", "biennium", "quinquennium",
    "decade", "rolling",
)
_SUMMARY_SQL = ",".join(f"'{k}'" for k in SUMMARY_KINDS)  # constants, not user input

# Tag column holds either a plain tag or a JSON array of tags.
_TAG_EACH = "json_each(CASE WHEN json_valid(f.tag) THEN f.tag ELSE json_array(f.tag) END)"


async def _all(db_path: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _one(db_path: str, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = await _all(db_path, sql, params)
    return rows[0] if rows else None


def parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw:
        return []
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return val if isinstance(val, list) else []


def fact_tags(tag: Any) -> list[str]:
    """Normalise a ``tag`` column value to a list of strings."""
    if not tag:
        return []
    if isinstance(tag, str) and tag.startswith("["):
        return [str(t) for t in parse_json_list(tag)]
    return [str(tag)]


# ── relationships ────────────────────────────────────────────────────────

async def list_relationships(db_path: str, uid: int) -> list[dict[str, Any]]:
    rows = await _all(
        db_path,
        "SELECT r.id, r.rel_type, r.status, r.created_at, "
        "CASE WHEN r.user_a_id = ? THEN r.user_b_id ELSE r.user_a_id END AS partner_id "
        "FROM relationships r WHERE (r.user_a_id = ? OR r.user_b_id = ?) "
        "ORDER BY r.created_at",
        (uid, uid, uid),
    )
    for r in rows:
        p = await _one(db_path, "SELECT name FROM users WHERE tg_id = ?", (r["partner_id"],))
        r["partner_name"] = (p or {}).get("name")
    return rows


async def user_relationship(db_path: str, uid: int, rel_id: str) -> dict[str, Any] | None:
    """The relationship row if *uid* is a member, else None."""
    return await _one(
        db_path,
        "SELECT * FROM relationships WHERE id = ? AND (user_a_id = ? OR user_b_id = ?)",
        (rel_id, uid, uid),
    )


def _rel_clause(rel: str | None, col: str = "relationship_id") -> tuple[str, tuple]:
    if not rel:
        return "", ()
    if rel == "none":
        return f" AND {col} IS NULL", ()
    return f" AND {col} = ?", (rel,)


# ── topics / stats / summaries ───────────────────────────────────────────

async def topics(db_path: str, uid: int, rel: str | None, limit: int) -> list[dict[str, Any]]:
    clause, extra = _rel_clause(rel, "f.relationship_id")
    return await _all(
        db_path,
        f"SELECT je.value AS topic, MAX(f.created_at) AS last_discussed, "
        f"COUNT(*) AS mention_count, "
        f"SUM(CASE WHEN f.visibility = 'private' THEN 1 ELSE 0 END) AS private_count "
        f"FROM facts f, {_TAG_EACH} je "
        f"WHERE f.owner_id = ? AND f.kind NOT IN ({_SUMMARY_SQL}) "
        f"AND f.archived_at IS NULL AND f.tag IS NOT NULL AND f.tag != ''{clause} "
        f"GROUP BY je.value ORDER BY last_discussed DESC LIMIT ?",
        (uid, *extra, limit),
    )


async def stats(db_path: str, uid: int, rel: str | None) -> dict[str, int]:
    clause, extra = _rel_clause(rel)
    mclause, mextra = _rel_clause(rel)
    base = f"owner_id = ? AND archived_at IS NULL{clause}"
    row = await _one(
        db_path,
        f"SELECT "
        f"SUM(CASE WHEN kind NOT IN ({_SUMMARY_SQL}) THEN 1 ELSE 0 END) AS facts, "
        f"SUM(CASE WHEN kind = 'core' THEN 1 ELSE 0 END) AS core_facts, "
        f"SUM(CASE WHEN kind NOT IN ({_SUMMARY_SQL}) AND visibility = 'shared' "
        f"THEN 1 ELSE 0 END) AS shared_facts, "
        f"SUM(CASE WHEN kind IN ({_SUMMARY_SQL}) THEN 1 ELSE 0 END) AS summaries "
        f"FROM facts WHERE {base}",
        (uid, *extra),
    ) or {}
    msgs = await _one(
        db_path,
        f"SELECT COUNT(*) AS n FROM messages WHERE user_id = ? AND deleted_at IS NULL{mclause}",
        (uid, *mextra),
    ) or {}
    return {
        "facts": row.get("facts") or 0,
        "core_facts": row.get("core_facts") or 0,
        "shared_facts": row.get("shared_facts") or 0,
        "summaries": row.get("summaries") or 0,
        "topics": len(await topics(db_path, uid, rel, 1000)),
        "messages": msgs.get("n") or 0,
        "relationships": len(await list_relationships(db_path, uid)),
    }


async def summaries(
    db_path: str, uid: int, rel: str | None, period: str | None,
    tag: str | None, limit: int,
) -> list[dict[str, Any]]:
    clause, extra = _rel_clause(rel, "f.relationship_id")
    kinds = f"f.kind IN ({_SUMMARY_SQL})" if not period else "f.kind = ?"
    params: list[Any] = [uid]
    if period:
        params.append(period)
    sql = (f"SELECT f.id, f.kind, f.text, f.tag, f.visibility, f.created_at, "
           f"f.metadata, f.source_ids, f.relationship_id FROM facts f "
           f"WHERE f.owner_id = ? AND {kinds} AND f.archived_at IS NULL{clause}")
    params.extend(extra)
    if tag:
        sql += f" AND EXISTS (SELECT 1 FROM {_TAG_EACH} t WHERE t.value = ?)"
        params.append(tag)
    sql += " ORDER BY f.created_at DESC, f.id DESC LIMIT ?"
    params.append(limit)
    rows = await _all(db_path, sql, tuple(params))
    for r in rows:
        meta: dict[str, Any] = {}
        try:
            parsed = json.loads(r.pop("metadata") or "{}")
            meta = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        r["period_id"] = meta.get("period")
        r["source_ids"] = [i for i in parse_json_list(r["source_ids"]) if isinstance(i, int)]
        r["tags"] = fact_tags(r.pop("tag"))
    return rows


# ── couple views ─────────────────────────────────────────────────────────

async def couple_facts(db_path: str, rel_id: str, limit: int) -> list[dict[str, Any]]:
    """Facts both partners have shared in this relationship (never private ones)."""
    return await _all(
        db_path,
        f"SELECT id, owner_id, text, tag, kind, created_at, shared_at FROM facts "
        f"WHERE relationship_id = ? AND visibility = 'shared' AND archived_at IS NULL "
        f"AND kind NOT IN ({_SUMMARY_SQL}) ORDER BY COALESCE(shared_at, created_at) DESC LIMIT ?",
        (rel_id, limit),
    )


async def couple_conflicts(db_path: str, rel_id: str) -> list[dict[str, Any]]:
    return await _all(
        db_path,
        "SELECT id, theme, status, opened_at, resolved_at FROM conflicts "
        "WHERE (relationship_id = ? OR couple_id = ?) AND status IN ('open','recurring') "
        "ORDER BY opened_at DESC",
        (rel_id, rel_id),
    )


async def couple_nudges(db_path: str, rel_id: str, uid: int) -> list[dict[str, Any]]:
    """Pending nudges addressed to *uid* (the generic suggestion only)."""
    return await _all(
        db_path,
        "SELECT id AS fact_id, nudge_type, nudge_suggestion, created_at FROM facts "
        "WHERE relationship_id = ? AND nudge_target_id = ? AND nudge_status = 'pending' "
        "AND nudge_suggestion IS NOT NULL AND nudge_suggestion != '' AND archived_at IS NULL "
        "ORDER BY created_at DESC",
        (rel_id, uid),
    )


async def couple_relays(db_path: str, rel_id: str, uid: int) -> list[dict[str, Any]]:
    """Relays the user sent (any state) and relays addressed to them once sent."""
    rows = await _all(
        db_path,
        "SELECT id, sender_id, receiver_id, draft, edited_draft, status, created_at, "
        "sent_at, acknowledged_at FROM relay_queue WHERE relationship_id = ? "
        "AND (sender_id = ? OR (receiver_id = ? AND status = 'sent')) "
        "ORDER BY created_at DESC LIMIT 50",
        (rel_id, uid, uid),
    )
    return [{
        "id": r["id"],
        "direction": "sent" if r["sender_id"] == uid else "received",
        "text": r["edited_draft"] or r["draft"],
        "status": r["status"],
        "created_at": r["created_at"],
        "sent_at": r["sent_at"],
        "acknowledged_at": r["acknowledged_at"],
    } for r in rows]


# ── admin (not user scoped) ──────────────────────────────────────────────

async def admin_stats(db_path: str) -> dict[str, Any]:
    tables = await _all(
        db_path, "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%\\_fts%' ESCAPE '\\' ORDER BY name")
    counts: dict[str, int] = {}
    async with aiosqlite.connect(db_path) as db:
        for t in tables:
            name = t["name"].replace('"', '""')  # name comes from sqlite_master
            try:
                async with db.execute(f'SELECT COUNT(*) FROM "{name}"') as cur:
                    counts[t["name"]] = (await cur.fetchone())[0]
            except aiosqlite.Error:
                counts[t["name"]] = -1  # virtual table that cannot be counted
    return {
        "users": counts.get("users", 0),
        "facts": counts.get("facts", 0),
        "messages": counts.get("messages", 0),
        "relationships": counts.get("relationships", 0),
        "tables": counts,
    }


async def admin_users(db_path: str) -> list[dict[str, Any]]:
    return await _all(
        db_path,
        "SELECT u.tg_id AS user_id, u.name, u.partner_id, u.lang, u.mode, u.paused, "
        "u.created_at, "
        "(SELECT COUNT(*) FROM facts f WHERE f.owner_id = u.tg_id AND f.archived_at IS NULL) "
        "AS facts, "
        "(SELECT COUNT(*) FROM messages m WHERE m.user_id = u.tg_id AND m.deleted_at IS NULL) "
        "AS messages FROM users u ORDER BY u.tg_id",
    )


async def admin_pipeline(db_path: str, msg_id: int) -> dict[str, Any] | None:
    """Trace message -> facts extracted from it -> summaries that cite those facts."""
    msg = await _one(
        db_path,
        "SELECT id, user_id, role, content, source, relationship_id, created_at, deleted_at "
        "FROM messages WHERE id = ?", (msg_id,))
    if msg is None:
        return None
    facts = await _all(
        db_path,
        "SELECT id, owner_id, text, kind, visibility, tag, status, model, created_at "
        "FROM facts WHERE source_message_id = ? ORDER BY id", (msg_id,))
    fact_ids = {f["id"] for f in facts}
    used_by: list[dict[str, Any]] = []
    if fact_ids:
        cands = await _all(
            db_path,
            f"SELECT id, kind, text, source_ids, created_at FROM facts "
            f"WHERE owner_id = ? AND kind IN ({_SUMMARY_SQL}) AND source_ids IS NOT NULL",
            (msg["user_id"],))
        for c in cands:
            if fact_ids & {i for i in parse_json_list(c["source_ids"]) if isinstance(i, int)}:
                used_by.append({k: c[k] for k in ("id", "kind", "text", "created_at")})
    return {"message": msg, "facts": facts, "summaries": used_by}
