"""Migrate a v1 database to the v2 schema (see ``schema_v2.py``).

``migrate_to_v2(db_path)`` is idempotent and is called at the end of ``init_db``.

Compatibility notes (v1 code still runs against the migrated database):

* v1 tables (``pending``, ``pending_batch*``, ``partner_perceptions``,
  ``user_patterns``, ``interaction_scores``, ``user_goals``, ``shared_goals``,
  ``couple_config``) are left in place; their rows are copied once into the v2
  tables, gated by ``PRAGMA user_version``.
* Dead ``facts`` columns (``category``, ``fact_type``, ``source``) are not
  dropped. New code must not reference them.
* ``facts.kind`` carries no CHECK: v1 code writes ``episode`` / ``partner_view``,
  which the v2 CHECK would reject. ``facts.visibility`` accepts the v1 value
  ``shared`` alongside the v2 values.
* ``couple_id`` columns are kept; ``relationship_id`` is added beside them and
  backfilled (``"a:b"`` -> ``"a_b"``).
"""

from __future__ import annotations

import logging

import aiosqlite

from pairtner.storage.schema_v2 import make_relationship_id

__all__ = ["migrate_to_v2", "make_relationship_id", "SCHEMA_VERSION"]

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_RELATIONSHIPS_DDL = """
CREATE TABLE IF NOT EXISTS relationships (
    id          TEXT PRIMARY KEY,
    user_a_id   INTEGER NOT NULL REFERENCES users(tg_id),
    user_b_id   INTEGER NOT NULL REFERENCES users(tg_id),
    rel_type    TEXT NOT NULL DEFAULT 'couple'
                CHECK(rel_type IN ('couple','parent_child','friends','siblings')),
    persona     TEXT DEFAULT 'couple',
    status      TEXT DEFAULT 'active'
                CHECK(status IN ('active','paused','ended')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_a_id, user_b_id),
    CHECK(user_a_id < user_b_id)
)
"""

# {name} is the table name so the same DDL serves create and rebuild.
_FACTS_DDL = """
CREATE TABLE {name} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER REFERENCES users(tg_id),
    relationship_id TEXT REFERENCES relationships(id),
    text            TEXT NOT NULL,
    visibility      TEXT NOT NULL DEFAULT 'nudge_eligible'
                    CHECK(visibility IN ('private','nudge_eligible','shareable','shared')),
    kind            TEXT NOT NULL DEFAULT 'working',
    status          TEXT NOT NULL DEFAULT 'active',
    intensity       REAL DEFAULT 0.5,
    msg_source      TEXT DEFAULT 'user'
                    CHECK(msg_source IN ('user','bot','system')),
    nudge_target_id INTEGER REFERENCES users(tg_id),
    nudge_type      TEXT,
    nudge_suggestion TEXT,
    nudge_cooldown_h INTEGER DEFAULT 24,
    nudge_ttl_h     INTEGER,
    nudge_status    TEXT DEFAULT 'pending'
                    CHECK(nudge_status IN ('pending','delivered','cancelled','expired')),
    nudge_delivered_at TIMESTAMP,
    nudge_cancelled_at TIMESTAMP,
    nudge_cancel_reason TEXT,
    expires_at      TIMESTAMP,
    archived_at     TIMESTAMP,
    supersedes_id   INTEGER REFERENCES facts(id),
    contradicts_id  INTEGER REFERENCES facts(id),
    shared_at       TIMESTAMP,
    citation_count  INTEGER DEFAULT 0,
    last_cited_at   TIMESTAMP,
    source_message_id INTEGER REFERENCES messages(id),
    source_ids      TEXT,
    model           TEXT,
    metadata        TEXT,
    event_date      TEXT,
    recurring       INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- legacy columns, kept for v1 code; do not reference from new code
    source          TEXT,
    category        TEXT DEFAULT NULL,
    fact_type       TEXT DEFAULT 'stable'
)
"""

# Columns added to an already-v2-shaped facts table (ALTER pass).
_FACTS_NEW_COLUMNS: list[tuple[str, str]] = [
    ("relationship_id", "TEXT REFERENCES relationships(id)"),
    ("msg_source", "TEXT DEFAULT 'user'"),
    ("nudge_target_id", "INTEGER REFERENCES users(tg_id)"),
    ("nudge_type", "TEXT"),
    ("nudge_suggestion", "TEXT"),
    ("nudge_cooldown_h", "INTEGER DEFAULT 24"),
    ("nudge_ttl_h", "INTEGER"),
    ("nudge_status", "TEXT DEFAULT 'pending'"),
    ("nudge_delivered_at", "TIMESTAMP"),
    ("nudge_cancelled_at", "TIMESTAMP"),
    ("nudge_cancel_reason", "TEXT"),
]

_RELAY_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS relay_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    sender_id       INTEGER NOT NULL REFERENCES users(tg_id),
    receiver_id     INTEGER NOT NULL REFERENCES users(tg_id),
    draft           TEXT NOT NULL,
    edited_draft    TEXT,
    status          TEXT NOT NULL DEFAULT 'awaiting_approval'
                    CHECK(status IN ('awaiting_approval','accumulating','reviewing',
                                     'approved','sent','discarded')),
    batch_id        INTEGER,
    send_after      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at         TIMESTAMP,
    feedback        TEXT CHECK(feedback IN ('yes','not_sure','no')),
    acknowledged_at TIMESTAMP
)
"""

_ANALYSIS_DDL = """
CREATE TABLE {name} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    user_id         INTEGER REFERENCES users(tg_id),
    about_id        INTEGER REFERENCES users(tg_id),
    result_type     TEXT NOT NULL
                    CHECK(result_type IN ('perception','pattern','interaction',
                                          'horsemen','articulation','health_score')),
    content         TEXT NOT NULL,
    sentiment       TEXT DEFAULT 'neutral',
    confidence      REAL DEFAULT 0.5,
    frequency       INTEGER DEFAULT 1,
    interaction_dir TEXT CHECK(interaction_dir IN ('positive','negative',NULL)),
    source          TEXT DEFAULT 'llm',
    first_seen      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_GOALS_DDL = """
CREATE TABLE IF NOT EXISTS goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(tg_id),
    relationship_id TEXT REFERENCES relationships(id),
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK(scope IN ('personal', 'shared')),
    text            TEXT NOT NULL,
    created_by      INTEGER REFERENCES users(tg_id),
    status          TEXT DEFAULT 'active'
                    CHECK(status IN ('active','achieved','dropped')),
    progress        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_RELATIONSHIP_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS relationship_config (
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    config_key      TEXT NOT NULL,
    config_value    TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (relationship_id, config_key)
)
"""

# v1 couple_id is nullable here so v1 inserts that only set couple_id keep working.
_TIMELINE_DDL = """
CREATE TABLE {name} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id       TEXT,
    relationship_id TEXT REFERENCES relationships(id),
    event_type      TEXT NOT NULL
                    CHECK(event_type IN ('paired','conflict','resolution','milestone',
                                         'goal','relay','checkin','nudge')),
    summary         TEXT NOT NULL,
    metadata        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# (index name, table, DDL). Order matters only in that tables must exist.
_INDEXES: list[tuple[str, str, str]] = [
    # v1 indexes dropped along with the facts rebuild
    ("idx_facts_owner_visibility", "facts", "facts(owner_id, visibility)"),
    ("idx_facts_owner_category", "facts", "facts(owner_id, category)"),
    ("idx_facts_owner_vis", "facts", "facts(owner_id, visibility)"),
    ("idx_facts_owner_created", "facts", "facts(owner_id, created_at)"),
    ("idx_facts_kind", "facts", "facts(owner_id, kind)"),
    ("idx_facts_status", "facts", "facts(owner_id, status)"),
    ("idx_facts_relationship", "facts", "facts(relationship_id, kind)"),
    ("idx_facts_nudge", "facts",
     "facts(nudge_target_id, nudge_status) WHERE nudge_target_id IS NOT NULL"),
    ("idx_messages_user_created", "messages", "messages(user_id, created_at)"),
    ("idx_messages_relationship", "messages", "messages(relationship_id, created_at)"),
    ("idx_relay_relationship", "relay_queue", "relay_queue(relationship_id, status)"),
    ("idx_relay_status_send", "relay_queue", "relay_queue(status, send_after)"),
    ("idx_mood_user_expires", "user_mood_styles", "user_mood_styles(user_id, expires_at)"),
    ("idx_analysis_rel", "analysis_results", "analysis_results(relationship_id, result_type)"),
    ("idx_goals_user_status", "goals", "goals(user_id, status)"),
    ("idx_goals_relationship", "goals", "goals(relationship_id, status)"),
    ("idx_conflicts_relationship", "conflicts", "conflicts(relationship_id, status)"),
    ("idx_timeline_couple_created", "timeline_events",
     "timeline_events(couple_id, created_at)"),  # v1 index, dropped by the rebuild
    ("idx_timeline_relationship", "timeline_events",
     "timeline_events(relationship_id, created_at)"),
    ("idx_relationships_user_a", "relationships", "relationships(user_a_id)"),
    ("idx_relationships_user_b", "relationships", "relationships(user_b_id)"),
    ("idx_memory_versions_fact", "memory_versions", "memory_versions(fact_id)"),
    ("idx_rest_outbox_user", "rest_outbox", "rest_outbox(user_id, id)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ) as cur:
        return await cur.fetchone() is not None


async def _columns(db: aiosqlite.Connection, table: str) -> list[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return [row[1] for row in await cur.fetchall()]


async def _table_sql(db: aiosqlite.Connection, table: str) -> str:
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row and row[0] else ""


async def _add_column_if_missing(
    db: aiosqlite.Connection, table: str, column: str, decl: str
) -> None:
    if column not in await _columns(db, table):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def _rebuild_table(
    db: aiosqlite.Connection, table: str, ddl: str
) -> None:
    """Replace ``table`` with ``ddl`` (a ``{name}`` template), copying shared columns.

    Standard SQLite recipe: create new, copy, drop old, rename. Foreign keys are
    off on this connection, so no other table's references are rewritten.
    """
    new = f"{table}__v2_new"
    await db.execute(f"DROP TABLE IF EXISTS {new}")
    await db.execute(ddl.format(name=new))
    old_cols = set(await _columns(db, table))
    shared = [c for c in await _columns(db, new) if c in old_cols]
    cols = ", ".join(shared)
    await db.execute(f"INSERT INTO {new} ({cols}) SELECT {cols} FROM {table}")
    await db.execute(f"DROP TABLE {table}")
    await db.execute(f"ALTER TABLE {new} RENAME TO {table}")


async def _ensure_index(
    db: aiosqlite.Connection, name: str, table: str, definition: str
) -> None:
    """Create an index; if the name is taken by an index on another table, use a v2 name."""
    async with db.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ) as cur:
        row = await cur.fetchone()
    if row and row[0] != table:
        name = name.replace("idx_", "idx_v2_", 1)
    await db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")


def _rel_from_couple(couple_id: str | None) -> str | None:
    """Convert a v1 ``"a:b"`` couple id to a v2 ``"a_b"`` relationship id."""
    return couple_id.replace(":", "_") if couple_id else None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

async def _populate_relationships(db: aiosqlite.Connection) -> None:
    """One relationship per partner_id pair (idempotent)."""
    async with db.execute(
        "SELECT tg_id, partner_id FROM users "
        "WHERE partner_id IS NOT NULL AND partner_id != tg_id"
    ) as cur:
        pairs = {tuple(sorted(r)) for r in await cur.fetchall()}
    for a, b in sorted(pairs):
        await db.execute(
            "INSERT OR IGNORE INTO relationships (id, user_a_id, user_b_id) "
            "VALUES (?, ?, ?)",
            (make_relationship_id(a, b), a, b),
        )


async def _ensure_relationship(db: aiosqlite.Connection, a: int, b: int) -> str | None:
    if a == b:
        return None
    rid = make_relationship_id(a, b)
    lo, hi = sorted([a, b])
    await db.execute(
        "INSERT OR IGNORE INTO relationships (id, user_a_id, user_b_id) VALUES (?, ?, ?)",
        (rid, lo, hi),
    )
    return rid


async def _backfill_relationship_ids(db: aiosqlite.Connection) -> None:
    """Fill NULL relationship_id from the owner's relationship / the couple_id."""
    for table, user_col in (("facts", "owner_id"), ("messages", "user_id")):
        await db.execute(
            f"UPDATE {table} SET relationship_id = ("
            f"  SELECT r.id FROM relationships r "
            f"  WHERE r.user_a_id = {table}.{user_col} OR r.user_b_id = {table}.{user_col} "
            f"  ORDER BY r.created_at LIMIT 1) "
            f"WHERE relationship_id IS NULL"
        )
    for table in ("conflicts", "checkins", "timeline_events"):
        if await _table_exists(db, table):
            await db.execute(
                f"UPDATE {table} SET relationship_id = REPLACE(couple_id, ':', '_') "
                f"WHERE relationship_id IS NULL AND couple_id IS NOT NULL"
            )


async def _partner_relationship(db: aiosqlite.Connection, user_id: int) -> str | None:
    async with db.execute(
        "SELECT id FROM relationships WHERE user_a_id = ? OR user_b_id = ? "
        "ORDER BY created_at LIMIT 1",
        (user_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def _copy_relay(db: aiosqlite.Connection) -> None:
    if await _table_exists(db, "pending"):
        async with db.execute(
            "SELECT id, sender_id, receiver_id, draft, edited_draft, status, "
            "send_after, created_at, sent_at, feedback, acknowledged_at FROM pending"
        ) as cur:
            rows = await cur.fetchall()
        for r in rows:
            rid = await _ensure_relationship(db, r[1], r[2])
            if rid is None:
                continue
            await db.execute(
                "INSERT OR IGNORE INTO relay_queue (id, relationship_id, sender_id, "
                "receiver_id, draft, edited_draft, status, send_after, created_at, "
                "sent_at, feedback, acknowledged_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (r[0], rid, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]),
            )
    if await _table_exists(db, "pending_batch") and await _table_exists(
        db, "pending_batch_items"
    ):
        async with db.execute(
            "SELECT b.id, b.sender_id, b.receiver_id, b.status, b.send_after, "
            "i.draft, i.edited_draft, i.status, i.created_at "
            "FROM pending_batch_items i JOIN pending_batch b ON b.id = i.batch_id "
            "ORDER BY i.id"
        ) as cur:
            items = await cur.fetchall()
        for bid, snd, rcv, bstatus, send_after, draft, edited, istatus, created in items:
            rid = await _ensure_relationship(db, snd, rcv)
            if rid is None:
                continue
            status = "discarded" if istatus == "discarded" else bstatus
            await db.execute(
                "INSERT INTO relay_queue (relationship_id, sender_id, receiver_id, "
                "draft, edited_draft, status, batch_id, send_after, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, snd, rcv, draft, edited, status, bid, send_after, created),
            )


async def _copy_analysis(db: aiosqlite.Connection) -> None:
    skipped = 0
    if await _table_exists(db, "partner_perceptions"):
        async with db.execute(
            "SELECT user_id, about_id, theme, sentiment, confidence, created_at, "
            "updated_at FROM partner_perceptions"
        ) as cur:
            rows = await cur.fetchall()
        for uid, about, theme, sent, conf, created, updated in rows:
            rid = await _ensure_relationship(db, uid, about)
            if rid is None:
                skipped += 1
                continue
            await db.execute(
                "INSERT INTO analysis_results (relationship_id, user_id, about_id, "
                "result_type, content, sentiment, confidence, created_at, updated_at, "
                "first_seen, last_seen) VALUES (?,?,?,'perception',?,?,?,?,?,?,?)",
                (rid, uid, about, theme, sent, conf, created, updated, created, updated),
            )
    if await _table_exists(db, "user_patterns"):
        async with db.execute(
            "SELECT user_id, pattern, pattern_type, frequency, first_seen, last_seen "
            "FROM user_patterns"
        ) as cur:
            rows = await cur.fetchall()
        kinds = {"communication": "pattern", "horsemen": "horsemen",
                 "articulation": "articulation"}
        for uid, pattern, ptype, freq, first, last in rows:
            rid = await _partner_relationship(db, uid)
            if rid is None:
                skipped += 1
                continue
            await db.execute(
                "INSERT INTO analysis_results (relationship_id, user_id, result_type, "
                "content, frequency, first_seen, last_seen, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, uid, kinds.get(ptype or "communication", "pattern"), pattern,
                 freq, first, last, first, last),
            )
    if await _table_exists(db, "interaction_scores"):
        async with db.execute(
            "SELECT couple_id, user_id, interaction_type, source, created_at "
            "FROM interaction_scores"
        ) as cur:
            rows = await cur.fetchall()
        for cid, uid, itype, source, created in rows:
            rid = _rel_from_couple(cid)
            await db.execute(
                "INSERT INTO analysis_results (relationship_id, user_id, result_type, "
                "content, interaction_dir, source, created_at, updated_at, first_seen, "
                "last_seen) VALUES (?,?,'interaction',?,?,?,?,?,?,?)",
                (rid, uid, itype, itype, source, created, created, created, created),
            )
    if skipped:
        logger.warning(
            "migrate_to_v2: skipped %d analysis rows for users without a relationship",
            skipped,
        )


async def _copy_goals(db: aiosqlite.Connection) -> None:
    if await _table_exists(db, "user_goals"):
        async with db.execute(
            "SELECT user_id, text, status, progress, created_at, updated_at FROM user_goals"
        ) as cur:
            rows = await cur.fetchall()
        for uid, text, status, progress, created, updated in rows:
            await db.execute(
                "INSERT INTO goals (user_id, relationship_id, scope, text, created_by, "
                "status, progress, created_at, updated_at) "
                "VALUES (?,?,'personal',?,?,?,?,?,?)",
                (uid, await _partner_relationship(db, uid), text, uid, status,
                 progress, created, updated),
            )
    if await _table_exists(db, "shared_goals"):
        async with db.execute(
            "SELECT couple_id, text, created_by, status, progress, created_at, "
            "updated_at FROM shared_goals"
        ) as cur:
            rows = await cur.fetchall()
        for cid, text, by, status, progress, created, updated in rows:
            await db.execute(
                "INSERT INTO goals (relationship_id, scope, text, created_by, status, "
                "progress, created_at, updated_at) VALUES (?,'shared',?,?,?,?,?,?)",
                (_rel_from_couple(cid), text, by, status, progress, created, updated),
            )


async def _copy_config(db: aiosqlite.Connection) -> None:
    if not await _table_exists(db, "couple_config"):
        return
    async with db.execute(
        "SELECT couple_id, config_key, config_value, created_at, updated_at "
        "FROM couple_config"
    ) as cur:
        rows = await cur.fetchall()
    for cid, key, value, created, updated in rows:
        await db.execute(
            "INSERT OR IGNORE INTO relationship_config (relationship_id, config_key, "
            "config_value, created_at, updated_at) VALUES (?,?,?,?,?)",
            (_rel_from_couple(cid), key, value, created, updated),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def migrate_to_v2(db_path: str) -> None:
    """Upgrade a v1 database (or a fresh ``init_db`` one) to the v2 schema.

    Safe to run repeatedly. Row copies from the merged v1 tables run once,
    guarded by ``PRAGMA user_version``.
    """
    facts_rebuilt = False
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = OFF")
        async with db.execute("PRAGMA user_version") as cur:
            version = (await cur.fetchone())[0]
        first_run = version < SCHEMA_VERSION

        # 1. relationships (+ populate from users.partner_id)
        await db.execute(_RELATIONSHIPS_DDL)
        await _populate_relationships(db)

        # 2. facts: widen CHECKs / default via rebuild, else add missing columns
        if await _table_exists(db, "facts"):
            if "nudge_eligible" not in await _table_sql(db, "facts"):
                await _rebuild_table(db, "facts", _FACTS_DDL)
                facts_rebuilt = True
            else:
                for col, decl in _FACTS_NEW_COLUMNS:
                    await _add_column_if_missing(db, "facts", col, decl)

        # 3. messages.relationship_id
        await _add_column_if_missing(
            db, "messages", "relationship_id", "TEXT REFERENCES relationships(id)"
        )

        # 4. relay_queue
        await db.execute(_RELAY_QUEUE_DDL)

        # 5. analysis_results (rebuild if an older copy lacks 'health_score')
        if await _table_exists(db, "analysis_results"):
            if "health_score" not in await _table_sql(db, "analysis_results"):
                await _rebuild_table(db, "analysis_results", _ANALYSIS_DDL)
        else:
            await db.execute(_ANALYSIS_DDL.format(name="analysis_results"))

        # 6. goals, 7. relationship_config
        await db.execute(_GOALS_DDL)
        await db.execute(_RELATIONSHIP_CONFIG_DDL)

        # 8. user_profiles.default_interaction_mode
        await _add_column_if_missing(
            db, "user_profiles", "default_interaction_mode",
            "TEXT DEFAULT 'vent' "
            "CHECK(default_interaction_mode IN ('solution','vent','adaptive'))",
        )

        # 9. timeline_events: add 'nudge' to event_type CHECK + relationship_id
        if await _table_exists(db, "timeline_events"):
            if "'nudge'" not in await _table_sql(db, "timeline_events"):
                await _rebuild_table(db, "timeline_events", _TIMELINE_DDL)
        else:
            await db.execute(_TIMELINE_DDL.format(name="timeline_events"))

        # relationship_id beside couple_id on the remaining couple-scoped tables
        for table in ("conflicts", "checkins"):
            if await _table_exists(db, table):
                await _add_column_if_missing(
                    db, table, "relationship_id", "TEXT REFERENCES relationships(id)"
                )

        # 13 (cont.). one-time data copies from merged v1 tables
        if first_run:
            await _copy_relay(db)
            await _copy_analysis(db)
            await _copy_goals(db)
            await _copy_config(db)

        await _backfill_relationship_ids(db)

        # 11. indexes
        for name, table, definition in _INDEXES:
            if await _table_exists(db, table):
                await _ensure_index(db, name, table, definition)

        await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await db.commit()

    if facts_rebuilt:
        # Dropping `facts` removed the FTS sync triggers; recreate and reindex.
        from pairtner.core.search import init_fts, rebuild_fts_index

        await init_fts(db_path)
        await rebuild_fts_index(db_path)
