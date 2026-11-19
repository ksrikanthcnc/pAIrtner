"""Tests for the v1 -> v2 schema migration."""

from __future__ import annotations

import aiosqlite
import pytest

from pairtner.core.search import search_facts
from pairtner.storage.migrate_v2 import make_relationship_id, migrate_to_v2
from pairtner.storage.schema import SCHEMA_SQL, init_db

V2_TABLES = {
    "relationships", "relay_queue", "analysis_results", "goals", "relationship_config",
}
FACT_NEW_COLUMNS = {
    "relationship_id", "msg_source", "nudge_target_id", "nudge_type", "nudge_suggestion",
    "nudge_cooldown_h", "nudge_ttl_h", "nudge_status", "nudge_delivered_at",
    "nudge_cancelled_at", "nudge_cancel_reason",
}


async def _tables(db) -> set[str]:
    async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        return {r[0] for r in await cur.fetchall()}


async def _cols(db, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return {r[1] for r in await cur.fetchall()}


async def _indexes(db) -> set[str]:
    async with db.execute("SELECT name FROM sqlite_master WHERE type='index'") as cur:
        return {r[0] for r in await cur.fetchall()}


async def _count(db, table: str) -> int:
    async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        return (await cur.fetchone())[0]


async def _make_v1(path: str) -> None:
    """A populated v1 database: raw v1 schema, no migration applied."""
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.executescript(
            """
            INSERT INTO users (tg_id, name, partner_id) VALUES
                (30, 'Bea', 10), (10, 'Al', 30), (20, 'Solo', NULL);
            INSERT INTO messages (user_id, role, content) VALUES (10, 'user', 'hi');
            INSERT INTO facts (owner_id, text, visibility, kind, category)
                VALUES (10, 'likes tea', 'private', 'working', 'pref'),
                       (30, 'likes jazz', 'shared', 'episode', NULL),
                       (20, 'solo fact', 'private', 'working', NULL);
            INSERT INTO pending (sender_id, receiver_id, draft, status)
                VALUES (10, 30, 'hello', 'sent');
            INSERT INTO pending_batch (id, sender_id, receiver_id, status)
                VALUES (7, 30, 10, 'reviewing');
            INSERT INTO pending_batch_items (batch_id, draft, status)
                VALUES (7, 'one', 'pending'), (7, 'two', 'discarded');
            INSERT INTO partner_perceptions (user_id, about_id, theme)
                VALUES (10, 30, 'warm');
            INSERT INTO user_patterns (user_id, pattern, pattern_type)
                VALUES (10, 'interrupts', 'horsemen'), (20, 'orphan', 'communication');
            INSERT INTO interaction_scores (couple_id, user_id, interaction_type)
                VALUES ('10:30', 10, 'positive');
            INSERT INTO user_goals (user_id, text) VALUES (10, 'exercise');
            INSERT INTO shared_goals (couple_id, text, created_by)
                VALUES ('10:30', 'holiday', 10);
            INSERT INTO couple_config (couple_id, config_key, config_value)
                VALUES ('10:30', 'tone', 'soft');
            INSERT INTO user_profiles (user_id) VALUES (10);
            INSERT INTO timeline_events (couple_id, event_type, summary)
                VALUES ('10:30', 'paired', 'paired up');
            INSERT INTO conflicts (couple_id, theme) VALUES ('10:30', 'chores');
            """
        )
        await db.commit()


def test_make_relationship_id_is_order_independent():
    assert make_relationship_id(30, 10) == make_relationship_id(10, 30) == "10_30"


@pytest.mark.asyncio
async def test_fresh_db_gets_v2_tables(tmp_path):
    path = str(tmp_path / "fresh.db")
    await init_db(path)
    async with aiosqlite.connect(path) as db:
        assert V2_TABLES <= await _tables(db)
        assert FACT_NEW_COLUMNS <= await _cols(db, "facts")
        assert "relationship_id" in await _cols(db, "messages")
        assert "default_interaction_mode" in await _cols(db, "user_profiles")
        assert {"idx_facts_nudge", "idx_relay_status_send", "idx_analysis_rel",
                "idx_goals_relationship", "idx_relationships_user_a"} <= await _indexes(db)
        # nudge-era values are accepted by the widened CHECKs
        await db.execute("INSERT INTO users (tg_id) VALUES (1), (2)")
        await db.execute("INSERT INTO relationships (id, user_a_id, user_b_id) VALUES ('1_2',1,2)")
        await db.execute("INSERT INTO facts (owner_id, text) VALUES (1, 'x')")
        await db.execute("INSERT INTO timeline_events (relationship_id, event_type, summary) "
                         "VALUES ('1_2', 'nudge', 's')")
        await db.execute("INSERT INTO analysis_results (relationship_id, result_type, content) "
                         "VALUES ('1_2', 'health_score', '80')")
        async with db.execute("SELECT visibility FROM facts") as cur:
            assert (await cur.fetchone())[0] == "nudge_eligible"
        async with db.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_v1_db_migrates_cleanly(tmp_path):
    path = str(tmp_path / "v1.db")
    await _make_v1(path)
    await migrate_to_v2(path)

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        assert V2_TABLES <= await _tables(db)
        assert FACT_NEW_COLUMNS <= await _cols(db, "facts")

        # facts preserved (ids, text, v1 'shared'/'episode' values), relationship backfilled
        async with db.execute("SELECT * FROM facts ORDER BY id") as cur:
            facts = await cur.fetchall()
        assert [f["text"] for f in facts] == ["likes tea", "likes jazz", "solo fact"]
        assert [f["visibility"] for f in facts] == ["private", "shared", "private"]
        assert facts[1]["kind"] == "episode"
        assert [f["relationship_id"] for f in facts] == ["10_30", "10_30", None]

        # messages
        async with db.execute("SELECT relationship_id FROM messages") as cur:
            assert (await cur.fetchone())[0] == "10_30"

        # relay_queue: 1 pending + 2 batch items, batch id kept, discarded item mapped
        async with db.execute(
            "SELECT draft, status, batch_id, relationship_id FROM relay_queue ORDER BY id"
        ) as cur:
            relay = [tuple(r) for r in await cur.fetchall()]
        assert relay == [
            ("hello", "sent", None, "10_30"),
            ("one", "reviewing", 7, "10_30"),
            ("two", "discarded", 7, "10_30"),
        ]

        # analysis_results: perception + pattern(horsemen) + interaction; orphan skipped
        async with db.execute(
            "SELECT result_type, content, interaction_dir FROM analysis_results ORDER BY id"
        ) as cur:
            analysis = [tuple(r) for r in await cur.fetchall()]
        assert analysis == [
            ("perception", "warm", None),
            ("horsemen", "interrupts", None),
            ("interaction", "positive", "positive"),
        ]

        # goals + config
        async with db.execute("SELECT scope, text, relationship_id FROM goals ORDER BY id") as cur:
            assert [tuple(r) for r in await cur.fetchall()] == [
                ("personal", "exercise", "10_30"),
                ("shared", "holiday", "10_30"),
            ]
        async with db.execute("SELECT * FROM relationship_config") as cur:
            cfg = await cur.fetchone()
        assert (cfg["relationship_id"], cfg["config_key"], cfg["config_value"]) == (
            "10_30", "tone", "soft")

        # profile column, timeline + conflicts
        async with db.execute("SELECT default_interaction_mode FROM user_profiles") as cur:
            assert (await cur.fetchone())[0] == "vent"
        await db.execute("INSERT INTO timeline_events (couple_id, event_type, summary) "
                         "VALUES ('10:30', 'nudge', 'n')")
        async with db.execute(
            "SELECT relationship_id FROM timeline_events WHERE event_type='paired'"
        ) as cur:
            assert (await cur.fetchone())[0] == "10_30"
        async with db.execute("SELECT relationship_id FROM conflicts") as cur:
            assert (await cur.fetchone())[0] == "10_30"

        # health_score allowed; v1 legacy columns retained
        await db.execute("INSERT INTO analysis_results (relationship_id, result_type, content) "
                         "VALUES ('10_30', 'health_score', '70')")
        assert {"category", "fact_type", "source"} <= await _cols(db, "facts")
        assert {"idx_facts_relationship", "idx_messages_relationship",
                "idx_timeline_relationship", "idx_conflicts_relationship"} <= await _indexes(db)


@pytest.mark.asyncio
async def test_relationships_populated_from_partner_pairs(tmp_path):
    path = str(tmp_path / "rel.db")
    await _make_v1(path)
    await migrate_to_v2(path)
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT id, user_a_id, user_b_id, rel_type, status FROM relationships"
        ) as cur:
            rows = await cur.fetchall()
    # (10,30) and (30,10) collapse to one ordered row; the unpaired user has none
    assert rows == [("10_30", 10, 30, "couple", "active")]


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "idem.db")
    await _make_v1(path)
    tables = ("relationships", "relay_queue", "analysis_results", "goals",
              "relationship_config", "facts", "messages", "timeline_events")

    await migrate_to_v2(path)
    async with aiosqlite.connect(path) as db:
        first = {t: await _count(db, t) for t in tables}
        schema_first = await _tables(db), await _indexes(db)

    await migrate_to_v2(path)
    await migrate_to_v2(path)
    async with aiosqlite.connect(path) as db:
        assert {t: await _count(db, t) for t in tables} == first
        assert (await _tables(db), await _indexes(db)) == schema_first

    # init_db (which also migrates) on the already-migrated DB is safe too
    await init_db(path)
    async with aiosqlite.connect(path) as db:
        assert {t: await _count(db, t) for t in tables} == first


@pytest.mark.asyncio
async def test_fts_survives_facts_rebuild(tmp_path):
    path = str(tmp_path / "fts.db")
    await _make_v1(path)
    await init_db(path)  # init_fts, then migration rebuilds facts
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO facts (owner_id, text, visibility) VALUES (10, 'loves pottery', 'private')"
        )
        await db.commit()
    assert [f["text"] for f in await search_facts(path, 10, "pottery")] == ["loves pottery"]
    assert await search_facts(path, 10, "tea")
