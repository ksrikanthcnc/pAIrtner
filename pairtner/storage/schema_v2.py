"""Database schema v2 — consolidated and redesigned.

Changes from v1 (22 tables → 17 + FTS5):
- Merged: pending + pending_batch + pending_batch_items → relay_queue
- Merged: partner_perceptions + user_patterns + interaction_scores → analysis_results
- Merged: user_goals + shared_goals → goals
- Dropped: fact_summaries (replaced by v2 summary tiers in facts table)
- Dropped: category column (FTS5 handles discovery)
- Removed: users.partner_id (replaced by relationships table)
- Renamed: kind → tier (working|core|daily|weekly|monthly|quarterly|yearly)
- Changed: visibility default 'private' → 'nudge_eligible'
- Added: relationships table (multi-relationship support, deterministic IDs)
- Added: relationship_id on facts (replaces couple_id; links to relationships)
- Added: msg_source column (user|bot|system) — extract facts from bot responses too
- Added: nudge columns + self_nudge + probe_partner for therapeutic steering
- Added: messages.relationship_id (which relationship context this message belongs to)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_relationship_id(user_a_id: int, user_b_id: int) -> str:
    """Deterministic, order-independent relationship ID from two user IDs."""
    lo, hi = sorted([user_a_id, user_b_id])
    return f"{lo}_{hi}"


# ---------------------------------------------------------------------------
# Schema SQL — v2 (17 tables + FTS5 virtual table)
# ---------------------------------------------------------------------------

SCHEMA_V2_SQL = """
-- ═══════════════════════════════════════════════════════════════════════════
-- CORE
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    name        TEXT,
    lang        TEXT DEFAULT 'en',
    paused      INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relationships (
    id          TEXT PRIMARY KEY,        -- deterministic: "min_max" of tg_ids
    user_a_id   INTEGER NOT NULL REFERENCES users(tg_id),
    user_b_id   INTEGER NOT NULL REFERENCES users(tg_id),
    rel_type    TEXT NOT NULL DEFAULT 'couple'
                CHECK(rel_type IN ('couple','parent_child','friends','siblings')),
    persona     TEXT DEFAULT 'couple',   -- which persona pack to load
    status      TEXT DEFAULT 'active'
                CHECK(status IN ('active','paused','ended')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_a_id, user_b_id),
    CHECK(user_a_id < user_b_id)         -- enforce ordering, matches make_relationship_id
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(tg_id),
    relationship_id TEXT REFERENCES relationships(id),  -- which relationship context
    role            TEXT NOT NULL,       -- user|assistant|system
    content         TEXT NOT NULL,
    deleted_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER REFERENCES users(tg_id),    -- NULL for relationship-level summaries
    relationship_id TEXT REFERENCES relationships(id),   -- which relationship this belongs to
    text            TEXT NOT NULL,
    visibility      TEXT NOT NULL DEFAULT 'nudge_eligible'
                    CHECK(visibility IN ('private', 'nudge_eligible', 'shareable')),
    tier            TEXT NOT NULL DEFAULT 'working'
                    CHECK(tier IN ('working','core','daily','weekly','monthly',
                                   'quarterly','yearly')),
    status          TEXT NOT NULL DEFAULT 'active',
    fact_type       TEXT DEFAULT 'stable'
                    CHECK(fact_type IN ('transient','situational','stable')),
    intensity       REAL DEFAULT 0.5,
    -- source tracking
    msg_source      TEXT DEFAULT 'user'
                    CHECK(msg_source IN ('user','bot','system')),
    -- nudge system
    nudge_target_id INTEGER REFERENCES users(tg_id),    -- partner to nudge (NULL = no nudge)
    nudge_type      TEXT,               -- appreciation|attention|communication|support|quality_time|conflict_prevention
    nudge_cooldown_h INTEGER DEFAULT 24, -- min hours before nudge can fire
    nudge_delivered_at TIMESTAMP,        -- when the nudge was actually delivered
    nudge_cancelled_at TIMESTAMP,        -- if cancelled (organically addressed or by LLM)
    nudge_cancel_reason TEXT,            -- why cancelled
    self_nudge      INTEGER DEFAULT 0,   -- therapeutically engage the fact owner about this
    probe_partner   INTEGER DEFAULT 0,   -- subtly probe partner's perspective on the TOPIC (no attribution)
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
    source_ids      TEXT,               -- JSON array of source fact/summary IDs
    model           TEXT,               -- which LLM extracted this
    metadata        TEXT,               -- JSON: {period, resolved, ongoing, new, narrative, ...}
    -- proactive
    event_date      TEXT,               -- ISO date for date-based reminders
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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    sender_id       INTEGER NOT NULL REFERENCES users(tg_id),
    receiver_id     INTEGER NOT NULL REFERENCES users(tg_id),
    draft           TEXT NOT NULL,
    edited_draft    TEXT,
    status          TEXT NOT NULL DEFAULT 'awaiting_approval'
                    CHECK(status IN ('awaiting_approval','accumulating','reviewing',
                                     'approved','sent','discarded')),
    batch_id        INTEGER,        -- NULL = single relay, non-NULL = part of a batch
    send_after      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at         TIMESTAMP,
    feedback        TEXT CHECK(feedback IN ('yes','not_sure','no')),
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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    user_id         INTEGER REFERENCES users(tg_id),    -- NULL for relationship-level
    about_id        INTEGER REFERENCES users(tg_id),    -- NULL for self-patterns
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
    relationship_id  TEXT NOT NULL REFERENCES relationships(id),
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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(tg_id),    -- NULL for shared goals
    relationship_id TEXT REFERENCES relationships(id),   -- non-NULL for shared goals
    scope           TEXT NOT NULL DEFAULT 'personal'
                    CHECK(scope IN ('personal', 'shared')),
    text            TEXT NOT NULL,
    created_by      INTEGER REFERENCES users(tg_id),
    status          TEXT DEFAULT 'active'
                    CHECK(status IN ('active','achieved','dropped')),
    progress        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    theme           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','resolved','recurring')),
    opened_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    relay_ids       TEXT
);

CREATE TABLE IF NOT EXISTS timeline_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    event_type      TEXT NOT NULL
                    CHECK(event_type IN ('paired','conflict','resolution','milestone',
                                         'goal','relay','checkin','nudge')),
    summary         TEXT NOT NULL,
    metadata        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS relationship_config (
    relationship_id TEXT NOT NULL REFERENCES relationships(id),
    config_key      TEXT NOT NULL,
    config_value    TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (relationship_id, config_key)
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

CREATE INDEX IF NOT EXISTS idx_facts_owner_vis ON facts(owner_id, visibility);
CREATE INDEX IF NOT EXISTS idx_facts_owner_created ON facts(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_facts_tier ON facts(owner_id, tier);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_facts_relationship ON facts(relationship_id, tier);
CREATE INDEX IF NOT EXISTS idx_facts_nudge ON facts(nudge_target_id, nudge_delivered_at)
    WHERE nudge_target_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_facts_self_nudge ON facts(owner_id, self_nudge)
    WHERE self_nudge = 1;
CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_relationship ON messages(relationship_id, created_at);
CREATE INDEX IF NOT EXISTS idx_relay_relationship ON relay_queue(relationship_id, status);
CREATE INDEX IF NOT EXISTS idx_relay_status_send ON relay_queue(status, send_after);
CREATE INDEX IF NOT EXISTS idx_mood_user_expires ON user_mood_styles(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_analysis_rel ON analysis_results(relationship_id, result_type);
CREATE INDEX IF NOT EXISTS idx_goals_user_status ON goals(user_id, status);
CREATE INDEX IF NOT EXISTS idx_goals_relationship ON goals(relationship_id, status);
CREATE INDEX IF NOT EXISTS idx_conflicts_relationship ON conflicts(relationship_id, status);
CREATE INDEX IF NOT EXISTS idx_timeline_relationship ON timeline_events(relationship_id, created_at);
CREATE INDEX IF NOT EXISTS idx_relationships_user_a ON relationships(user_a_id);
CREATE INDEX IF NOT EXISTS idx_relationships_user_b ON relationships(user_b_id);
CREATE INDEX IF NOT EXISTS idx_memory_versions_fact ON memory_versions(fact_id);
CREATE INDEX IF NOT EXISTS idx_rest_outbox_user ON rest_outbox(user_id, id);
"""
