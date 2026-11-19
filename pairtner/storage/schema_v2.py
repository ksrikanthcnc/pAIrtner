"""Database schema v2 — consolidated from 22 tables to 16.

Changes from v1:
- Merged: pending + pending_batch + pending_batch_items → relay_queue
- Merged: partner_perceptions + user_patterns + interaction_scores → analysis_results
- Merged: user_goals + shared_goals → goals
- Dropped: fact_summaries (replaced by v2 consolidation episodes in facts table)
- All else: unchanged
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Schema SQL — v2 (16 tables + FTS5 virtual table)
# ---------------------------------------------------------------------------

SCHEMA_V2_SQL = """
-- ═══════════════════════════════════════════════════════════════════════════
-- CORE
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    name        TEXT,
    partner_id  INTEGER REFERENCES users(tg_id),
    lang        TEXT DEFAULT 'en',
    paused      INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    deleted_at  TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(tg_id),
    text            TEXT NOT NULL,
    visibility      TEXT NOT NULL CHECK(visibility IN ('private', 'shared')),
    source          TEXT,
    kind            TEXT NOT NULL DEFAULT 'working',
    status          TEXT NOT NULL DEFAULT 'active',
    fact_type       TEXT DEFAULT 'stable' CHECK(fact_type IN ('transient','situational','stable')),
    category        TEXT DEFAULT NULL,
    intensity       REAL DEFAULT 0.5,
    -- lifecycle
    expires_at      TIMESTAMP,
    archived_at     TIMESTAMP,
    supersedes_id   INTEGER REFERENCES facts(id),
    contradicts_id  INTEGER REFERENCES facts(id),
    shared_at       TIMESTAMP,
    -- scoring
    citation_count  INTEGER DEFAULT 0,
    last_cited_at   TIMESTAMP,
    -- provenance
    source_message_id INTEGER REFERENCES messages(id),
    source_ids      TEXT,           -- JSON array of source fact IDs (for episodes)
    model           TEXT,           -- which LLM extracted this
    metadata        TEXT,           -- JSON: {level: weekly|monthly|quarterly, fact_count: N, ...}
    -- proactive
    event_date      TEXT,           -- ISO date for date-based reminders
    recurring       INTEGER DEFAULT 0,
    -- timestamps
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id         INTEGER PRIMARY KEY REFERENCES users(tg_id),
    default_voice   TEXT DEFAULT 'casual_en',
    voice_intensity REAL DEFAULT 0.5,
    dampening_base  REAL DEFAULT 0.7,
    onboarding_done INTEGER DEFAULT 0,
    onboarding_step INTEGER DEFAULT 0,
    relationship_type TEXT,
    duration_years  INTEGER,
    has_kids        INTEGER,
    age_range       TEXT,
    love_story_brief TEXT,
    learned_voice   TEXT,
    message_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_meta (
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
);

-- ═══════════════════════════════════════════════════════════════════════════
-- RELAY (merged from pending + pending_batch + pending_batch_items)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS relay_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id     INTEGER NOT NULL REFERENCES users(tg_id),
    receiver_id   INTEGER NOT NULL REFERENCES users(tg_id),
    draft         TEXT NOT NULL,
    edited_draft  TEXT,
    status        TEXT NOT NULL DEFAULT 'awaiting_approval'
                  CHECK(status IN ('awaiting_approval','accumulating','reviewing',
                                   'approved','sent','discarded')),
    batch_id      INTEGER,         -- NULL = single relay, non-NULL = part of a batch
    send_after    TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at       TIMESTAMP,
    feedback      TEXT CHECK(feedback IN ('yes','not_sure','no')),
    acknowledged_at TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════════════════
-- MEMORY SYSTEM (v2)
-- ═══════════════════════════════════════════════════════════════════════════

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

-- ═══════════════════════════════════════════════════════════════════════════
-- RELATIONSHIP INTELLIGENCE (merged analysis)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS analysis_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    about_id    INTEGER REFERENCES users(tg_id),   -- NULL for self-patterns
    couple_id   TEXT,                                -- for interaction scores
    result_type TEXT NOT NULL
                CHECK(result_type IN ('perception','pattern','interaction','horsemen','articulation')),
    content     TEXT NOT NULL,                       -- the theme/pattern/description
    sentiment   TEXT DEFAULT 'neutral',              -- for perceptions
    confidence  REAL DEFAULT 0.5,
    frequency   INTEGER DEFAULT 1,                   -- for patterns
    interaction_dir TEXT CHECK(interaction_dir IN ('positive','negative',NULL)),
    source      TEXT DEFAULT 'llm',
    first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

-- ═══════════════════════════════════════════════════════════════════════════
-- FEATURES
-- ═══════════════════════════════════════════════════════════════════════════

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

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(tg_id),   -- NULL for shared goals
    couple_id   TEXT,                                -- non-NULL for shared goals
    scope       TEXT NOT NULL DEFAULT 'personal'
                CHECK(scope IN ('personal', 'shared')),
    text        TEXT NOT NULL,
    created_by  INTEGER REFERENCES users(tg_id),
    status      TEXT DEFAULT 'active'
                CHECK(status IN ('active','achieved','dropped')),
    progress    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS timeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL
                CHECK(event_type IN ('paired','conflict','resolution','milestone',
                                     'goal','relay','checkin')),
    summary     TEXT NOT NULL,
    metadata    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════════════════
-- INFRASTRUCTURE
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS usage (
    date   TEXT NOT NULL,
    model  TEXT NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, model)
);

CREATE TABLE IF NOT EXISTS rate_limit (
    user_id   INTEGER NOT NULL,
    window    TEXT NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    reset_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id, window)
);

CREATE TABLE IF NOT EXISTS couple_config (
    couple_id    TEXT NOT NULL,
    config_key   TEXT NOT NULL,
    config_value TEXT NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (couple_id, config_key)
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

-- ═══════════════════════════════════════════════════════════════════════════
-- INDEXES
-- ═══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_facts_owner_visibility ON facts(owner_id, visibility);
CREATE INDEX IF NOT EXISTS idx_facts_owner_created ON facts(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts(owner_id, kind);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_relay_sender_status ON relay_queue(sender_id, status);
CREATE INDEX IF NOT EXISTS idx_relay_status_send ON relay_queue(status, send_after);
CREATE INDEX IF NOT EXISTS idx_mood_user_expires ON user_mood_styles(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_analysis_user ON analysis_results(user_id, result_type);
CREATE INDEX IF NOT EXISTS idx_analysis_couple ON analysis_results(couple_id, created_at);
CREATE INDEX IF NOT EXISTS idx_goals_user_status ON goals(user_id, status);
CREATE INDEX IF NOT EXISTS idx_goals_couple ON goals(couple_id, status);
CREATE INDEX IF NOT EXISTS idx_conflicts_couple ON conflicts(couple_id, status);
CREATE INDEX IF NOT EXISTS idx_timeline_couple ON timeline_events(couple_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_versions_fact ON memory_versions(fact_id);
CREATE INDEX IF NOT EXISTS idx_rest_outbox_user ON rest_outbox(user_id, id);
"""
