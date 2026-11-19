"""Database schema definition and migrations.

This module contains the SQL schema and the init_db function that creates
tables and runs migrations for existing databases.
"""

from __future__ import annotations

import aiosqlite

# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
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
    category    TEXT DEFAULT NULL,
    fact_type   TEXT DEFAULT 'stable' CHECK(fact_type IN ('transient','situational','stable')),
    kind        TEXT NOT NULL DEFAULT 'working',
    status      TEXT NOT NULL DEFAULT 'active',
    expires_at  TIMESTAMP,
    archived_at TIMESTAMP,
    supersedes_id INTEGER REFERENCES facts(id),
    contradicts_id INTEGER REFERENCES facts(id),
    shared_at   TIMESTAMP,
    citation_count INTEGER DEFAULT 0,
    last_cited_at TIMESTAMP,
    source_message_id INTEGER REFERENCES messages(id),
    source_ids  TEXT,
    intensity   REAL DEFAULT 0.5,
    model       TEXT,
    metadata    TEXT,
    event_date  TEXT,
    recurring   INTEGER DEFAULT 0,
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
    sent_at       TIMESTAMP,
    feedback      TEXT CHECK(feedback IN ('yes','not_sure','no')),
    acknowledged_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    deleted_at  TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage (
    date   TEXT NOT NULL,
    model  TEXT NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, model)
);

CREATE TABLE IF NOT EXISTS pack_snapshots (
    pack_hash        TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    version          TEXT NOT NULL,
    engine_contract  INTEGER,
    files_json       TEXT NOT NULL,
    first_seen_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task           TEXT NOT NULL,
    model          TEXT,
    pack_hash      TEXT NOT NULL,
    prompt_sha256  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS couple_config (
    couple_id   TEXT NOT NULL,
    config_key  TEXT NOT NULL,
    config_value TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (couple_id, config_key)
);

CREATE TABLE IF NOT EXISTS pending_batch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id   INTEGER NOT NULL REFERENCES users(tg_id),
    receiver_id INTEGER NOT NULL REFERENCES users(tg_id),
    status      TEXT NOT NULL DEFAULT 'accumulating'
                CHECK(status IN ('accumulating','reviewing','approved','sent','discarded')),
    send_after  TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_batch_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     INTEGER NOT NULL REFERENCES pending_batch(id),
    draft        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending','approved','edited','discarded')),
    edited_draft TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checkins (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id        TEXT NOT NULL,
    week_start       TEXT NOT NULL,
    partner_a_id     INTEGER NOT NULL,
    partner_a_answers TEXT,
    partner_b_id     INTEGER NOT NULL,
    partner_b_answers TEXT,
    summary          TEXT,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending','partial','complete')),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rate_limit (
    user_id   INTEGER NOT NULL,
    window    TEXT NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    reset_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id, window)
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id       INTEGER PRIMARY KEY REFERENCES users(tg_id),
    default_voice TEXT DEFAULT 'casual_en',
    voice_intensity REAL DEFAULT 0.5,
    dampening_base REAL DEFAULT 0.7,
    onboarding_done INTEGER DEFAULT 0,
    onboarding_step INTEGER DEFAULT 0,
    relationship_type TEXT,
    duration_years INTEGER,
    has_kids INTEGER,
    age_range TEXT,
    love_story_brief TEXT,
    learned_voice TEXT,
    message_count INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_mood_styles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(tg_id),
    mood          TEXT NOT NULL,
    voice_override TEXT,
    dampening_override REAL,
    response_length TEXT DEFAULT 'normal',
    set_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mood_user_expires ON user_mood_styles(user_id, expires_at);

CREATE TABLE IF NOT EXISTS partner_perceptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    about_id    INTEGER NOT NULL REFERENCES users(tg_id),
    theme       TEXT NOT NULL,
    sentiment   TEXT DEFAULT 'neutral',
    confidence  REAL DEFAULT 0.5,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_perceptions_user_about ON partner_perceptions(user_id, about_id);

CREATE TABLE IF NOT EXISTS user_patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    pattern     TEXT NOT NULL,
    pattern_type TEXT DEFAULT 'communication'
                CHECK(pattern_type IN ('communication','horsemen','articulation')),
    frequency   INTEGER DEFAULT 1,
    first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_patterns_user ON user_patterns(user_id);

CREATE TABLE IF NOT EXISTS interaction_scores (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id        TEXT NOT NULL,
    user_id          INTEGER NOT NULL REFERENCES users(tg_id),
    interaction_type TEXT NOT NULL CHECK(interaction_type IN ('positive','negative')),
    source           TEXT DEFAULT 'llm' CHECK(source IN ('relay','checkin','nudge','llm')),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interaction_couple_created
    ON interaction_scores(couple_id, created_at);

CREATE TABLE IF NOT EXISTS user_goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    text        TEXT NOT NULL,
    status      TEXT DEFAULT 'active' CHECK(status IN ('active','achieved','dropped')),
    progress    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_goals_user_status ON user_goals(user_id, status);

CREATE TABLE IF NOT EXISTS shared_goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id   TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_by  INTEGER NOT NULL REFERENCES users(tg_id),
    status      TEXT DEFAULT 'active' CHECK(status IN ('active','achieved','dropped')),
    progress    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shared_goals_couple ON shared_goals(couple_id, status);

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
CREATE INDEX IF NOT EXISTS idx_batch_sender_status
    ON pending_batch(sender_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_status_send_after
    ON pending_batch(status, send_after);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch
    ON pending_batch_items(batch_id);

CREATE TABLE IF NOT EXISTS conflicts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id   TEXT NOT NULL,
    theme       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open','resolved','recurring')),
    opened_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    relay_ids   TEXT
);

CREATE INDEX IF NOT EXISTS idx_conflicts_couple_status
    ON conflicts(couple_id, status);

CREATE TABLE IF NOT EXISTS timeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL
                CHECK(event_type IN ('paired','conflict','resolution','milestone','goal','relay','checkin')),
    summary     TEXT NOT NULL,
    metadata    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_timeline_couple_created
    ON timeline_events(couple_id, created_at);

CREATE TABLE IF NOT EXISTS fact_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    year_month  TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    fact_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_summaries_user_month
    ON fact_summaries(user_id, year_month);

CREATE TABLE IF NOT EXISTS auth_tokens (
    user_id     INTEGER PRIMARY KEY,
    token       TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rest_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rest_outbox_user
    ON rest_outbox(user_id, id);

-- Memory version history (audit trail for fact changes).
CREATE TABLE IF NOT EXISTS memory_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id         INTEGER NOT NULL REFERENCES facts(id),
    version         INTEGER NOT NULL,
    text            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    op              TEXT NOT NULL DEFAULT 'add',
    reason          TEXT,
    source_msg_id   INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fact_id, version)
);

CREATE INDEX IF NOT EXISTS idx_memory_versions_fact
    ON memory_versions(fact_id);

-- Memory operation audit log.
CREATE TABLE IF NOT EXISTS memory_op_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    message_id      INTEGER,
    op_json         TEXT NOT NULL,
    outcome         TEXT NOT NULL DEFAULT 'applied',
    reason          TEXT,
    fact_id         INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Key-value metadata per user (analysis timestamps, preferences, etc.).
CREATE TABLE IF NOT EXISTS user_meta (
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
);
"""


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

async def init_db(db_path: str) -> None:
    """Create tables and indexes if they don't exist, run migrations."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)

        # Migration: add 'paused' column to users if missing (existing DBs)
        async with db.execute("PRAGMA table_info(users)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "paused" not in columns:
            await db.execute(
                "ALTER TABLE users ADD COLUMN paused INTEGER DEFAULT 0"
            )

        # Migration: add 'category' column to facts if missing
        async with db.execute("PRAGMA table_info(facts)") as cur:
            fact_columns = {row[1] for row in await cur.fetchall()}
        if "category" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN category TEXT DEFAULT NULL"
            )

        # Migration: add fact lifecycle columns
        if "fact_type" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN fact_type TEXT DEFAULT 'stable'"
            )
        if "expires_at" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN expires_at TIMESTAMP"
            )
        if "archived_at" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN archived_at TIMESTAMP"
            )
        if "supersedes_id" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN supersedes_id INTEGER REFERENCES facts(id)"
            )
        if "shared_at" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN shared_at TIMESTAMP"
            )

        # Migration: add memory store columns
        if "kind" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN kind TEXT NOT NULL DEFAULT 'working'"
            )
        if "status" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )

        # Migration: memory v1 columns
        if "citation_count" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN citation_count INTEGER DEFAULT 0"
            )
        if "last_cited_at" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN last_cited_at TIMESTAMP"
            )
        if "source_message_id" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN source_message_id INTEGER REFERENCES messages(id)"
            )
        if "contradicts_id" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN contradicts_id INTEGER REFERENCES facts(id)"
            )

        # Migration: v2 memory columns
        if "source_ids" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN source_ids TEXT"
            )
        if "intensity" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN intensity REAL DEFAULT 0.5"
            )
        if "model" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN model TEXT"
            )
        if "metadata" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN metadata TEXT"
            )
        if "event_date" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN event_date TEXT"
            )
        if "recurring" not in fact_columns:
            await db.execute(
                "ALTER TABLE facts ADD COLUMN recurring INTEGER DEFAULT 0"
            )

        # Create memory indexes (after migration)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts(owner_id, kind)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(owner_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_owner_category ON facts(owner_id, category)"
        )

        # Migration: pattern_type column
        async with db.execute("PRAGMA table_info(user_patterns)") as cur:
            pattern_columns = {row[1] for row in await cur.fetchall()}
        if "pattern_type" not in pattern_columns:
            await db.execute(
                "ALTER TABLE user_patterns ADD COLUMN pattern_type TEXT DEFAULT 'communication'"
            )

        # Migration: feedback/acknowledged_at on pending
        async with db.execute("PRAGMA table_info(pending)") as cur:
            pending_columns = {row[1] for row in await cur.fetchall()}
        if "feedback" not in pending_columns:
            await db.execute("ALTER TABLE pending ADD COLUMN feedback TEXT")
        if "acknowledged_at" not in pending_columns:
            await db.execute("ALTER TABLE pending ADD COLUMN acknowledged_at TIMESTAMP")

        # Migration: learned_voice and message_count on user_profiles
        async with db.execute("PRAGMA table_info(user_profiles)") as cur:
            profile_columns = {row[1] for row in await cur.fetchall()}
        if "learned_voice" not in profile_columns:
            await db.execute("ALTER TABLE user_profiles ADD COLUMN learned_voice TEXT")
        if "message_count" not in profile_columns:
            await db.execute(
                "ALTER TABLE user_profiles ADD COLUMN message_count INTEGER DEFAULT 0"
            )

        # Migration: add deleted_at to messages if missing
        async with db.execute("PRAGMA table_info(messages)") as cur:
            msg_columns = {row[1] for row in await cur.fetchall()}
        if "deleted_at" not in msg_columns:
            await db.execute("ALTER TABLE messages ADD COLUMN deleted_at TIMESTAMP")

        await db.commit()

    # Initialise FTS5 search index
    from pairtner.core.search import init_fts
    await init_fts(db_path)

    # Upgrade to the v2 schema (idempotent)
    from pairtner.storage.migrate_v2 import migrate_to_v2
    await migrate_to_v2(db_path)
